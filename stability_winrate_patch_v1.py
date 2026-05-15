"""stability_winrate_patch_v1.py

Surgical runtime patch for the current coin-trade-bot codebase.

What it fixes without rewriting trader.py:
1) LONG/SHORT scoring split: the old base ai_score was LONG-biased.
2) Safer live signal gate: ADX / ATR% / EMA gap / anti-chase checks are applied in one place.
3) HEDGE reduce-only positionIdx fix in order_market.
4) Side-aware position-size lookup while managing/exiting HEDGE positions.
5) Partial-close quantity step normalization before reduce-only close.

Disable quickly with:
    STAB_PATCH_ON=false
"""

from __future__ import annotations

import math
import os
from typing import Any, Callable, Iterable

try:
    import trader as _t
    from trader import Trader
except Exception as _e:  # pragma: no cover
    print(f"[STAB_PATCH] boot failed: {_e}", flush=True)
    _t = None
    Trader = None  # type: ignore


def _env_bool(name: str, default: bool = False) -> bool:
    v = str(os.getenv(name, str(default))).strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
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


def _fmt_qty(qty: float, step: float | None = None) -> str:
    try:
        q = float(qty)
        if step and step > 0:
            # enough precision for Bybit lot steps such as 1, 0.1, 0.01, 0.001
            decimals = max(0, min(8, int(round(-math.log10(step))) if step < 1 else 0))
            return f"{q:.{decimals}f}".rstrip("0").rstrip(".") or "0"
        return (f"{q:.8f}".rstrip("0").rstrip(".")) or "0"
    except Exception:
        return str(qty)


def _ema(vals: Iterable[float], period: int) -> float:
    vals = [float(x) for x in vals]
    fn = getattr(_t, "ema", None) if _t is not None else None
    if callable(fn):
        return float(fn(vals, int(period)))
    if not vals:
        return 0.0
    k = 2.0 / (int(period) + 1.0)
    e = float(vals[0])
    for v in vals[1:]:
        e = float(v) * k + e * (1.0 - k)
    return e


def _rsi(closes: list[float], period: int) -> float:
    fn = getattr(_t, "rsi", None) if _t is not None else None
    if callable(fn):
        v = fn(closes, int(period))
        if v is not None:
            return float(v)
    p = int(period)
    if len(closes) < p + 1:
        return 50.0
    gain = loss = 0.0
    for i in range(-p, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gain += diff
        else:
            loss -= diff
    rs = gain / (loss + 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    fn = getattr(_t, "atr", None) if _t is not None else None
    if callable(fn):
        v = fn(highs, lows, closes, int(period))
        if v is not None:
            return float(v)
    p = int(period)
    if len(closes) < p + 1:
        return max(0.0, closes[-1] * 0.005) if closes else 0.0
    trs = []
    for i in range(-p, 0):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return sum(trs) / max(1, len(trs))


def _adx(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    fn = getattr(_t, "_hard_adx", None) if _t is not None else None
    if callable(fn):
        try:
            return float(fn(highs, lows, closes, int(period)))
        except Exception:
            pass

    p = int(period)
    if len(closes) < p + 2:
        return 0.0
    plus_dm = []
    minus_dm = []
    tr = []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    dx_vals = []
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


STAB_PATCH_ON = _env_bool("STAB_PATCH_ON", True)
STAB_MIN_ADX = _env_float("STAB_MIN_ADX", _safe_float(getattr(_t, "HARD_MIN_ADX", 18.0) if _t else 18.0, 18.0))
STAB_MIN_ATR_PCT = _env_float("STAB_MIN_ATR_PCT", _safe_float(getattr(_t, "HARD_MIN_ATR_PCT", 0.0035) if _t else 0.0035, 0.0035))
STAB_MAX_ATR_PCT = _env_float("STAB_MAX_ATR_PCT", _safe_float(getattr(_t, "HARD_MAX_ATR_PCT", 0.045) if _t else 0.045, 0.045))
STAB_MIN_EMA_GAP_PCT = _env_float("STAB_MIN_EMA_GAP_PCT", _safe_float(getattr(_t, "HARD_MIN_EMA_GAP_PCT", 0.0018) if _t else 0.0018, 0.0018))
STAB_ANTI_CHASE_ATR = _env_float("STAB_ANTI_CHASE_ATR", 1.7)
STAB_LONG_RSI_MIN = _env_float("STAB_LONG_RSI_MIN", 42.0)
STAB_LONG_RSI_MAX = _env_float("STAB_LONG_RSI_MAX", 72.0)
STAB_SHORT_RSI_MIN = _env_float("STAB_SHORT_RSI_MIN", 28.0)
STAB_SHORT_RSI_MAX = _env_float("STAB_SHORT_RSI_MAX", 58.0)
STAB_SCORE_LONG_RSI_LO = _env_float("STAB_SCORE_LONG_RSI_LO", 45.0)
STAB_SCORE_LONG_RSI_HI = _env_float("STAB_SCORE_LONG_RSI_HI", 65.0)
STAB_SCORE_SHORT_RSI_LO = _env_float("STAB_SCORE_SHORT_RSI_LO", 35.0)
STAB_SCORE_SHORT_RSI_HI = _env_float("STAB_SCORE_SHORT_RSI_HI", 55.0)
STAB_FORCE_NATIVE_ORDER = _env_bool("STAB_FORCE_NATIVE_ORDER", False)


def _side_score(side: str, price: float, ef: float, es: float, r: float, a: float) -> int:
    side = str(side or "LONG").upper()
    score = 0
    if side == "SHORT":
        if price < es:
            score += 25
        if price < ef:
            score += 20
        if ef < es:
            score += 20
        if STAB_SCORE_SHORT_RSI_LO < r < STAB_SCORE_SHORT_RSI_HI:
            score += 20
        if price > 0 and (a / price) < 0.02:
            score += 15
    else:
        if price > es:
            score += 25
        if price > ef:
            score += 20
        if ef > es:
            score += 20
        if STAB_SCORE_LONG_RSI_LO < r < STAB_SCORE_LONG_RSI_HI:
            score += 20
        if price > 0 and (a / price) < 0.02:
            score += 15
    return int(max(0, min(100, score)))


def _reason(symbol: str, side: str, price: float, ef: float, es: float, r: float, a: float, score: int, trend_ok: bool, enter_ok: bool, extra: str = "") -> str:
    label_fn = getattr(_t, "confidence_label", None) if _t is not None else None
    label = label_fn(score) if callable(label_fn) else str(score)
    return (
        f"[{symbol} {side}] 근거\n"
        f"- price={price:.6f}\n"
        f"- EMA{getattr(_t, 'EMA_FAST', 20)}={ef:.6f}, EMA{getattr(_t, 'EMA_SLOW', 50)}={es:.6f}\n"
        f"- RSI{getattr(_t, 'RSI_PERIOD', 14)}={r:.2f}\n"
        f"- ATR{getattr(_t, 'ATR_PERIOD', 14)}={a:.6f}\n"
        f"- score={score} ({label})\n"
        f"- trend_ok={trend_ok} | enter_ok={enter_ok}\n"
        f"- stab={extra}\n"
    )


if _t is not None and STAB_PATCH_ON:
    _orig_compute_signal_and_exits = getattr(_t, "compute_signal_and_exits", None)

    def compute_signal_and_exits(symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False):
        try:
            price = float(price or 0.0)
            side = str(side or "LONG").upper()
            if price <= 0:
                return False, "STAB_BAD_PRICE", 0, None, None, None

            entry_interval = str(getattr(_t, "ENTRY_INTERVAL", "15"))
            kline_limit = max(_env_int("STAB_KLINE_LIMIT", int(getattr(_t, "KLINE_LIMIT", 240))), int(getattr(_t, "EMA_SLOW", 50)) * 4)
            kl = _t.get_klines(symbol, entry_interval, kline_limit) or []
            if len(kl) < max(120, int(getattr(_t, "EMA_SLOW", 50)) * 3):
                a = price * 0.005
                stop_dist = a * float(mp.get("stop_atr", 1.5))
                tp_dist = stop_dist * float(mp.get("tp_r", 1.5))
                sl = price - stop_dist if side == "LONG" else price + stop_dist
                tp = price + tp_dist if side == "LONG" else price - tp_dist
                return False, f"STAB_NO_KLINE len={len(kl)}", 0, sl, tp, a

            kl = list(reversed(kl))
            closes = [float(x[4]) for x in kl if float(x[4] or 0) > 0]
            highs = [float(x[2]) for x in kl[-len(closes):]]
            lows = [float(x[3]) for x in kl[-len(closes):]]
            if len(closes) < max(120, int(getattr(_t, "EMA_SLOW", 50)) * 3):
                return False, "STAB_BAD_KLINE", 0, None, None, None

            ema_fast = int(getattr(_t, "EMA_FAST", 20))
            ema_slow = int(getattr(_t, "EMA_SLOW", 50))
            rsi_period = int(getattr(_t, "RSI_PERIOD", 14))
            atr_period = int(getattr(_t, "ATR_PERIOD", 14))
            ef = _ema(closes[-ema_fast * 3:], ema_fast)
            es = _ema(closes[-ema_slow * 3:], ema_slow)
            r = _rsi(closes, rsi_period)
            a = _atr(highs, lows, closes, atr_period)
            if a <= 0:
                a = price * 0.005

            atr_pct = a / price
            ema_gap_pct = abs(ef - es) / max(price, 1e-9)
            adx_v = _adx(highs, lows, closes, atr_period)
            dist_atr = abs(price - ef) / max(a, 1e-9)

            trend_ok = (price > es and ef > es) if side == "LONG" else (price < es and ef < es)
            score = _side_score(side, price, ef, es, r, a)
            need = int(float(mp.get("enter_score", 70)))
            enter_ok = score >= need

            stop_dist = a * float(mp.get("stop_atr", 1.5))
            tp_dist = stop_dist * float(mp.get("tp_r", 1.5))
            sl = price - stop_dist if side == "LONG" else price + stop_dist
            tp = price + tp_dist if side == "LONG" else price - tp_dist

            if side == "LONG" and (r < STAB_LONG_RSI_MIN or r > STAB_LONG_RSI_MAX):
                return False, _reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok, f"RSI_BLOCK long rsi={r:.2f}"), score, sl, tp, a
            if side == "SHORT" and (r < STAB_SHORT_RSI_MIN or r > STAB_SHORT_RSI_MAX):
                return False, _reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok, f"RSI_BLOCK short rsi={r:.2f}"), score, sl, tp, a
            if avoid_low_rsi and side == "LONG" and r < STAB_LONG_RSI_MIN:
                return False, "AI AVOID LOSS RSI ZONE", score, sl, tp, a
            if avoid_low_rsi and side == "SHORT" and r > STAB_SHORT_RSI_MAX:
                return False, "AI AVOID SHORT RSI BOUNCE ZONE", score, sl, tp, a
            if STAB_MIN_ATR_PCT > 0 and atr_pct < STAB_MIN_ATR_PCT:
                return False, _reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok, f"ATR_LOW {atr_pct:.4f}<{STAB_MIN_ATR_PCT:.4f}"), score, sl, tp, a
            if STAB_MAX_ATR_PCT > 0 and atr_pct > STAB_MAX_ATR_PCT:
                return False, _reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok, f"ATR_HIGH {atr_pct:.4f}>{STAB_MAX_ATR_PCT:.4f}"), score, sl, tp, a
            if STAB_MIN_ADX > 0 and adx_v < STAB_MIN_ADX:
                return False, _reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok, f"ADX_BLOCK {adx_v:.2f}<{STAB_MIN_ADX:.2f}"), score, sl, tp, a
            if STAB_MIN_EMA_GAP_PCT > 0 and ema_gap_pct < STAB_MIN_EMA_GAP_PCT:
                return False, _reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok, f"EMA_GAP_BLOCK {ema_gap_pct:.4f}<{STAB_MIN_EMA_GAP_PCT:.4f}"), score, sl, tp, a
            if STAB_ANTI_CHASE_ATR > 0 and dist_atr > STAB_ANTI_CHASE_ATR:
                return False, _reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok, f"ANTI_CHASE dist_atr={dist_atr:.2f}>{STAB_ANTI_CHASE_ATR:.2f}"), score, sl, tp, a

            ok = bool(trend_ok and enter_ok)
            extra = f"ADX={adx_v:.2f} ATR%={atr_pct:.4f} GAP%={ema_gap_pct:.4f} distATR={dist_atr:.2f} need={need}"
            return ok, _reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok, extra), score, sl, tp, a
        except Exception as e:
            # Fail closed. The old function can be used only if user disables STAB_PATCH_ON.
            return False, f"STAB_SIGNAL_ERR {e}", 0, None, None, None

    _t.compute_signal_and_exits = compute_signal_and_exits

    def ai_score(price, ef, es, r, a, side: str = "LONG"):
        # Backward-compatible replacement. Old code can still call ai_score(price, ef, es, r, a).
        return _side_score(side, float(price or 0.0), float(ef or 0.0), float(es or 0.0), _safe_float(r, 50.0), _safe_float(a, 0.0))

    _t.ai_score = ai_score

    _orig_order_market = getattr(_t, "order_market", None)

    def _position_idx_for_order(exchange_side: str, reduce_only: bool) -> int | None:
        if str(getattr(_t, "POSITION_MODE", "ONEWAY")).upper() != "HEDGE":
            return None
        s = str(exchange_side or "").lower()
        if reduce_only:
            # Closing LONG uses Sell with positionIdx=1. Closing SHORT uses Buy with positionIdx=2.
            return 1 if s == "sell" else 2
        # Opening LONG uses Buy/1. Opening SHORT uses Sell/2.
        return 1 if s == "buy" else 2

    def order_market(symbol: str, side: str, qty: float, reduce_only: bool = False):
        if bool(getattr(_t, "DRY_RUN", False)):
            return {"retCode": 0, "retMsg": "DRY_RUN"}
        q = qty
        try:
            q = _t.fix_qty(float(qty), symbol)
        except Exception:
            q = qty
        body = {
            "category": getattr(_t, "CATEGORY", "linear"),
            "symbol": str(symbol).upper(),
            "side": side,
            "orderType": "Market",
            "qty": _fmt_qty(float(q)),
            "timeInForce": "IOC",
        }
        pidx = _position_idx_for_order(side, bool(reduce_only))
        if pidx is not None:
            body["positionIdx"] = pidx
        if reduce_only:
            body["reduceOnly"] = True
        resp = _t.http.request("POST", "/v5/order/create", body, auth=True)
        if (resp or {}).get("retCode") != 0:
            raise Exception(f"ORDER FAILED: {resp}")
        return resp

    _t.order_market = order_market

    _orig_get_position_size = getattr(_t, "get_position_size", None)

    def get_position_size_by_side(symbol: str, side: str | None = None) -> float:
        if bool(getattr(_t, "DRY_RUN", False)):
            return 0.0
        side_norm = str(side or "").upper()
        want_exchange_side = "Buy" if side_norm == "LONG" else "Sell" if side_norm == "SHORT" else ""
        try:
            plist = _t.get_positions_all(symbol=str(symbol).upper()) or []
            total = 0.0
            for p in plist:
                if str(p.get("symbol") or "").upper() != str(symbol).upper():
                    continue
                if want_exchange_side and str(p.get("side") or "") != want_exchange_side:
                    continue
                total += abs(_safe_float(p.get("size"), 0.0))
            return float(total)
        except Exception:
            if callable(_orig_get_position_size):
                return float(_orig_get_position_size(symbol) or 0.0)
            return 0.0

    _t.get_position_size_by_side = get_position_size_by_side

    def _quantize_with_lot(self, symbol: str, qty: float) -> float:
        q = float(qty or 0.0)
        if q <= 0:
            return 0.0
        try:
            step, min_qty = self._get_lot_size(symbol)
            step = float(step or 0.0)
            min_qty = float(min_qty or 0.0)
            if step > 0:
                floored = math.floor(q / step) * step
                if floored > 0:
                    q = floored
            if min_qty > 0 and q < min_qty:
                # Do not oversize a reduce-only close. Let original tiny qty through if needed.
                return float(qty)
            return float(round(q, 8))
        except Exception:
            return q

    _orig_close_qty = getattr(Trader, "_close_qty", None) if Trader is not None else None
    if callable(_orig_close_qty):
        def _close_qty_stab(self, symbol: str, side: str, close_qty: float):
            q = _quantize_with_lot(self, symbol, close_qty)
            if q <= 0:
                return None
            if STAB_FORCE_NATIVE_ORDER:
                if bool(getattr(_t, "DRY_RUN", False)):
                    return None
                side_ex = "Sell" if str(side).upper() == "LONG" else "Buy"
                return _t.order_market(symbol, side_ex, q, reduce_only=True)
            return _orig_close_qty(self, symbol, side, q)
        Trader._close_qty = _close_qty_stab

    def _run_with_side_size(symbol: str, side: str, fn: Callable, *args, **kwargs):
        if not callable(_orig_get_position_size):
            return fn(*args, **kwargs)
        old = _t.get_position_size
        def _side_size(sym: str):
            if str(sym).upper() == str(symbol).upper():
                return get_position_size_by_side(sym, side)
            return old(sym)
        _t.get_position_size = _side_size
        try:
            return fn(*args, **kwargs)
        finally:
            _t.get_position_size = old

    _orig_exit_position = getattr(Trader, "_exit_position", None) if Trader is not None else None
    if callable(_orig_exit_position):
        def _exit_position_stab(self, idx: int, why: str, force: bool = False):
            try:
                pos = (self.positions or [])[idx]
                return _run_with_side_size(pos.get("symbol", ""), pos.get("side", ""), _orig_exit_position, self, idx, why, force)
            except Exception:
                return _orig_exit_position(self, idx, why, force)
        Trader._exit_position = _exit_position_stab

    _orig_manage_one = getattr(Trader, "_manage_one", None) if Trader is not None else None
    if callable(_orig_manage_one):
        def _manage_one_stab(self, idx: int):
            try:
                pos = (self.positions or [])[idx]
                return _run_with_side_size(pos.get("symbol", ""), pos.get("side", ""), _orig_manage_one, self, idx)
            except Exception:
                return _orig_manage_one(self, idx)
        Trader._manage_one = _manage_one_stab

    _orig_status_text = getattr(Trader, "status_text", None) if Trader is not None else None
    if callable(_orig_status_text):
        def _status_text_stab(self, *args, **kwargs):
            base = _orig_status_text(self, *args, **kwargs)
            try:
                return str(base) + f"\n🧱 STAB=ON adx>={STAB_MIN_ADX:g} atr%={STAB_MIN_ATR_PCT:g}-{STAB_MAX_ATR_PCT:g} chase<={STAB_ANTI_CHASE_ATR:g}ATR"
            except Exception:
                return base
        Trader.status_text = _status_text_stab

    print("[STAB_PATCH] loaded: side-aware signal + safer close/order patches", flush=True)
