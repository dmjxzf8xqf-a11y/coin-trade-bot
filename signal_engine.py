"""signal_engine.py

One shared signal engine for live trading, /why-style diagnosis, and backtests.

Design goals
- Keep this dependency-free and runtime-patch friendly.
- Make LONG and SHORT scoring symmetric.
- Return a stable tuple for the existing trader.py API:
    ok, reason, score, sl, tp, atr
- Also expose detailed metadata for logging and later winrate analysis.

This module does not place orders. It only evaluates whether a candidate trade is
worth taking.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import math
import os
from typing import Any, Iterable


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(str(os.getenv(name, str(default))).strip()))
    except Exception:
        return int(default)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _ema(vals: Iterable[float], period: int) -> float:
    vals = [float(x) for x in vals if x is not None]
    if not vals:
        return 0.0
    period = max(1, int(period))
    k = 2.0 / (period + 1.0)
    e = float(vals[0])
    for v in vals[1:]:
        e = float(v) * k + e * (1.0 - k)
    return e


def _rsi(closes: list[float], period: int) -> float:
    p = max(2, int(period))
    if len(closes) < p + 1:
        return 50.0
    gain = 0.0
    loss = 0.0
    for i in range(-p, 0):
        diff = float(closes[i]) - float(closes[i - 1])
        if diff > 0:
            gain += diff
        else:
            loss -= diff
    if loss <= 0:
        return 100.0 if gain > 0 else 50.0
    rs = gain / loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    p = max(2, int(period))
    if len(closes) < p + 1:
        return max(0.0, float(closes[-1]) * 0.005) if closes else 0.0
    trs: list[float] = []
    start = max(1, len(closes) - p)
    for i in range(start, len(closes)):
        trs.append(max(
            float(highs[i]) - float(lows[i]),
            abs(float(highs[i]) - float(closes[i - 1])),
            abs(float(lows[i]) - float(closes[i - 1])),
        ))
    return sum(trs) / max(1, len(trs))


def _adx(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    p = max(2, int(period))
    if len(closes) < p + 2:
        return 0.0
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    tr: list[float] = []
    for i in range(1, len(closes)):
        up = float(highs[i]) - float(highs[i - 1])
        down = float(lows[i - 1]) - float(lows[i])
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(
            float(highs[i]) - float(lows[i]),
            abs(float(highs[i]) - float(closes[i - 1])),
            abs(float(lows[i]) - float(closes[i - 1])),
        ))

    dx_vals: list[float] = []
    start = max(p, len(tr) - p * 3)
    for end in range(start, len(tr) + 1):
        tr_sum = sum(tr[end - p:end])
        if tr_sum <= 0:
            continue
        plus = 100.0 * sum(plus_dm[end - p:end]) / tr_sum
        minus = 100.0 * sum(minus_dm[end - p:end]) / tr_sum
        denom = plus + minus
        dx_vals.append(0.0 if denom <= 0 else 100.0 * abs(plus - minus) / denom)
    if not dx_vals:
        return 0.0
    tail = dx_vals[-p:] if len(dx_vals) >= p else dx_vals
    return sum(tail) / len(tail)


def _mean(vals: list[float]) -> float:
    return sum(vals) / max(1, len(vals))


@dataclass
class SignalResult:
    ok: bool
    symbol: str
    side: str
    price: float
    score: int
    reason: str
    sl: float | None
    tp: float | None
    atr: float
    regime: str
    meta: dict[str, Any]

    def as_tuple(self):
        return self.ok, self.reason, self.score, self.sl, self.tp, self.atr

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get_attr(module: Any, name: str, default: Any) -> Any:
    try:
        return getattr(module, name, default)
    except Exception:
        return default


def _parse_klines(raw: list[Any]) -> tuple[list[float], list[float], list[float], list[float]]:
    # Bybit returns newest-first. Most bot indicator code expects oldest-first.
    rows = list(reversed(raw or []))
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    vols: list[float] = []
    for x in rows:
        try:
            highs.append(float(x[2]))
            lows.append(float(x[3]))
            closes.append(float(x[4]))
            # Bybit kline volume is usually x[5], turnover x[6]. Use volume first.
            vols.append(_safe_float(x[5] if len(x) > 5 else 0.0, 0.0))
        except Exception:
            continue
    return highs, lows, closes, vols


def _htf_trend(symbol: str, trader_module: Any, ema_fast: int, ema_slow: int) -> str:
    if not _env_bool("SIGNAL_HTF_ON", False):
        return "off"
    try:
        interval = str(os.getenv("SIGNAL_HTF_INTERVAL", os.getenv("MTF_TREND_INTERVAL", "60")))
        limit = max(ema_slow * 4, 220)
        kl = trader_module.get_klines(symbol, interval, limit) or []
        highs, lows, closes, vols = _parse_klines(kl)
        if len(closes) < ema_slow * 3:
            return "unknown"
        ef = _ema(closes[-ema_fast * 3:], ema_fast)
        es = _ema(closes[-ema_slow * 3:], ema_slow)
        if ef > es and closes[-1] > es:
            return "up"
        if ef < es and closes[-1] < es:
            return "down"
        return "range"
    except Exception:
        return "error"


def _detect_regime(side: str, price: float, ef: float, es: float, ema200: float, adx_v: float, atr_pct: float) -> str:
    if atr_pct >= _env_float("SIGNAL_HIGH_VOL_PCT", 0.035):
        return "HIGH_VOL"
    if atr_pct <= _env_float("SIGNAL_LOW_VOL_PCT", 0.003):
        return "LOW_VOL"
    if adx_v < _env_float("SIGNAL_CHOP_ADX", 16.0):
        return "CHOP"
    if price > es and ef > es and (ema200 <= 0 or price > ema200):
        return "TREND_UP"
    if price < es and ef < es and (ema200 <= 0 or price < ema200):
        return "TREND_DOWN"
    return "RANGE"


def _score_side(side: str, price: float, ef: float, es: float, ema200: float, rsi_v: float, adx_v: float, atr_pct: float, ema_gap_pct: float, chase_atr: float, vol_ratio: float, htf: str) -> int:
    side = str(side or "LONG").upper()
    score = 0

    if side == "LONG":
        if price > es:
            score += 15
        if ef > es:
            score += 20
        if ema200 <= 0 or price > ema200:
            score += 8
        if 45.0 <= rsi_v <= 65.0:
            score += 22
        elif 40.0 <= rsi_v <= 72.0:
            score += 10
        if htf == "up":
            score += 8
        elif htf == "down":
            score -= 12
    else:
        if price < es:
            score += 15
        if ef < es:
            score += 20
        if ema200 <= 0 or price < ema200:
            score += 8
        if 35.0 <= rsi_v <= 55.0:
            score += 22
        elif 28.0 <= rsi_v <= 60.0:
            score += 10
        if htf == "down":
            score += 8
        elif htf == "up":
            score -= 12

    if adx_v >= 28:
        score += 12
    elif adx_v >= 22:
        score += 9
    elif adx_v >= 18:
        score += 5

    if _env_float("SIGNAL_MIN_ATR_PCT", 0.0040) <= atr_pct <= _env_float("SIGNAL_MAX_ATR_PCT", 0.035):
        score += 7
    if ema_gap_pct >= _env_float("SIGNAL_MIN_EMA_GAP_PCT", 0.0020):
        score += 5
    if chase_atr <= _env_float("SIGNAL_ANTI_CHASE_ATR", 1.7):
        score += 5
    if vol_ratio >= _env_float("SIGNAL_MIN_VOL_RATIO", 0.75):
        score += 3

    return int(max(0, min(100, round(score))))


def _fallback_result(symbol: str, side: str, price: float, mp: dict, note: str) -> SignalResult:
    atr_v = max(0.0, float(price or 0.0) * 0.005)
    stop_dist = atr_v * _safe_float(mp.get("stop_atr"), 1.5)
    tp_dist = stop_dist * _safe_float(mp.get("tp_r"), 1.5)
    if str(side).upper() == "LONG":
        sl = float(price) - stop_dist
        tp = float(price) + tp_dist
    else:
        sl = float(price) + stop_dist
        tp = float(price) - tp_dist
    reason = f"[{symbol} {side}] SIGNAL_ENGINE_BLOCK\n- note={note}\n- score=0\n"
    return SignalResult(False, symbol, str(side).upper(), float(price or 0.0), 0, reason, sl, tp, atr_v, "UNKNOWN", {"note": note})


def evaluate_signal_detail(symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False, trader_module: Any = None) -> SignalResult:
    """Evaluate one side of one symbol.

    `trader_module` is intentionally passed in by the runtime patch so this module
    remains testable and import-safe.
    """
    side = str(side or "LONG").upper()
    price = float(price or 0.0)
    if price <= 0:
        return _fallback_result(symbol, side, price, mp, "bad price")

    if trader_module is None:
        try:
            import trader as trader_module  # type: ignore
        except Exception:
            trader_module = None
    if trader_module is None or not hasattr(trader_module, "get_klines"):
        return _fallback_result(symbol, side, price, mp, "trader module unavailable")

    ema_fast = int(_get_attr(trader_module, "EMA_FAST", _env_int("EMA_FAST", 20)))
    ema_slow = int(_get_attr(trader_module, "EMA_SLOW", _env_int("EMA_SLOW", 50)))
    rsi_period = int(_get_attr(trader_module, "RSI_PERIOD", _env_int("RSI_PERIOD", 14)))
    atr_period = int(_get_attr(trader_module, "ATR_PERIOD", _env_int("ATR_PERIOD", 14)))
    entry_interval = str(_get_attr(trader_module, "ENTRY_INTERVAL", os.getenv("ENTRY_INTERVAL", "15")))
    kline_limit = max(int(_get_attr(trader_module, "KLINE_LIMIT", _env_int("KLINE_LIMIT", 240))), 240, ema_slow * 4)

    try:
        raw = trader_module.get_klines(symbol, entry_interval, kline_limit) or []
    except Exception as e:
        return _fallback_result(symbol, side, price, mp, f"kline error: {e}")

    highs, lows, closes, vols = _parse_klines(raw)
    min_needed = max(ema_slow * 3, atr_period + 30, 120)
    if len(closes) < min_needed:
        return _fallback_result(symbol, side, price, mp, f"kline 부족 {len(closes)}/{min_needed}")

    # Prefer exchange current price if passed price is stale but sane.
    close_price = float(closes[-1])
    if close_price > 0 and abs(close_price / price - 1.0) < 0.03:
        price = close_price

    ef = _ema(closes[-ema_fast * 3:], ema_fast)
    es = _ema(closes[-ema_slow * 3:], ema_slow)
    ema200 = _ema(closes[-240:], 200) if len(closes) >= 220 else 0.0
    rsi_v = _rsi(closes, rsi_period)
    atr_v = _atr(highs, lows, closes, atr_period)
    adx_v = _adx(highs, lows, closes, _env_int("SIGNAL_ADX_PERIOD", 14))
    atr_pct = atr_v / max(price, 1e-9)
    ema_gap_pct = abs(ef - es) / max(price, 1e-9)
    chase_atr = abs(price - ef) / max(atr_v, 1e-9)
    vol_ratio = 1.0
    if len(vols) >= 30:
        recent = _mean(vols[-5:])
        base = _mean(vols[-35:-5])
        vol_ratio = recent / max(base, 1e-9)

    htf = _htf_trend(symbol, trader_module, ema_fast, ema_slow)
    regime = _detect_regime(side, price, ef, es, ema200, adx_v, atr_pct)
    score = _score_side(side, price, ef, es, ema200, rsi_v, adx_v, atr_pct, ema_gap_pct, chase_atr, vol_ratio, htf)

    stop_dist = atr_v * _safe_float(mp.get("stop_atr"), 1.5)
    tp_dist = stop_dist * _safe_float(mp.get("tp_r"), 1.5)
    if side == "LONG":
        sl = price - stop_dist
        tp = price + tp_dist
        trend_ok = bool(price > es and ef > es)
        rsi_ok = bool(_env_float("SIGNAL_LONG_RSI_MIN", 40.0) <= rsi_v <= _env_float("SIGNAL_LONG_RSI_MAX", 72.0))
    else:
        sl = price + stop_dist
        tp = price - tp_dist
        trend_ok = bool(price < es and ef < es)
        rsi_ok = bool(_env_float("SIGNAL_SHORT_RSI_MIN", 28.0) <= rsi_v <= _env_float("SIGNAL_SHORT_RSI_MAX", 60.0))

    threshold = int(_safe_float(mp.get("enter_score"), _env_int("ENTER_SCORE_SAFE", 70)))
    blocks: list[str] = []

    if avoid_low_rsi and side == "LONG" and rsi_v < _env_float("SIGNAL_AVOID_LOW_RSI", 40.0):
        blocks.append(f"AVOID_LOW_RSI rsi={rsi_v:.2f}")
    if not trend_ok:
        blocks.append("TREND_FAIL")
    if not rsi_ok:
        blocks.append(f"RSI_RANGE_FAIL rsi={rsi_v:.2f}")
    if _env_bool("SIGNAL_HARD_ADX", True) and adx_v < _env_float("SIGNAL_MIN_ADX", 20.0):
        blocks.append(f"ADX_LOW {adx_v:.2f}<{_env_float('SIGNAL_MIN_ADX', 20.0):.2f}")
    if atr_pct < _env_float("SIGNAL_MIN_ATR_PCT", 0.0040):
        blocks.append(f"ATR_LOW {atr_pct:.4f}<{_env_float('SIGNAL_MIN_ATR_PCT', 0.0040):.4f}")
    if atr_pct > _env_float("SIGNAL_MAX_ATR_PCT", 0.035):
        blocks.append(f"ATR_HIGH {atr_pct:.4f}>{_env_float('SIGNAL_MAX_ATR_PCT', 0.035):.4f}")
    if ema_gap_pct < _env_float("SIGNAL_MIN_EMA_GAP_PCT", 0.0020):
        blocks.append(f"EMA_GAP_LOW {ema_gap_pct:.4f}<{_env_float('SIGNAL_MIN_EMA_GAP_PCT', 0.0020):.4f}")
    if chase_atr > _env_float("SIGNAL_ANTI_CHASE_ATR", 1.7):
        blocks.append(f"CHASE_BLOCK {chase_atr:.2f}ATR>{_env_float('SIGNAL_ANTI_CHASE_ATR', 1.7):.2f}")
    if _env_bool("SIGNAL_HARD_VOLUME", False) and vol_ratio < _env_float("SIGNAL_MIN_VOL_RATIO", 0.75):
        blocks.append(f"VOL_RATIO_LOW {vol_ratio:.2f}<{_env_float('SIGNAL_MIN_VOL_RATIO', 0.75):.2f}")
    if _env_bool("SIGNAL_HTF_HARD", False):
        if side == "LONG" and htf == "down":
            blocks.append("HTF_DOWN_BLOCK_LONG")
        if side == "SHORT" and htf == "up":
            blocks.append("HTF_UP_BLOCK_SHORT")
    if _env_bool("SIGNAL_EMA200_HARD", False) and ema200 > 0:
        if side == "LONG" and price < ema200:
            blocks.append("EMA200_BLOCK_LONG")
        if side == "SHORT" and price > ema200:
            blocks.append("EMA200_BLOCK_SHORT")
    if score < threshold:
        blocks.append(f"SCORE_LOW {score}<{threshold}")

    ok = not blocks
    meta = {
        "ema_fast": ef,
        "ema_slow": es,
        "ema200": ema200,
        "rsi": rsi_v,
        "atr": atr_v,
        "atr_pct": atr_pct,
        "adx": adx_v,
        "ema_gap_pct": ema_gap_pct,
        "chase_atr": chase_atr,
        "vol_ratio": vol_ratio,
        "htf": htf,
        "trend_ok": trend_ok,
        "rsi_ok": rsi_ok,
        "threshold": threshold,
        "blocks": blocks[:],
    }

    head = f"[{symbol} {side}] SIGNAL_ENGINE"
    reason = (
        f"{head}\n"
        f"- price={price:.6f}\n"
        f"- EMA{ema_fast}={ef:.6f}, EMA{ema_slow}={es:.6f}, EMA200={ema200:.6f}\n"
        f"- RSI{rsi_period}={rsi_v:.2f} | ATR{atr_period}={atr_v:.6f} ({atr_pct:.4%}) | ADX={adx_v:.2f}\n"
        f"- gap={ema_gap_pct:.4%} | chase={chase_atr:.2f}ATR | vol_ratio={vol_ratio:.2f} | htf={htf} | regime={regime}\n"
        f"- score={score} threshold={threshold}\n"
        f"- result={'PASS' if ok else 'BLOCK'}{' | ' + '; '.join(blocks[:4]) if blocks else ''}\n"
    )

    return SignalResult(ok, symbol, side, price, score, reason, sl, tp, atr_v, regime, meta)


def evaluate_signal(symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False, trader_module: Any = None):
    return evaluate_signal_detail(symbol, side, price, mp, avoid_low_rsi=avoid_low_rsi, trader_module=trader_module).as_tuple()
