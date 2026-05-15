"""allin_guard_experimental_patch_v1.py

Runtime patch for coin-trade-bot-main (7).

Purpose
- Add account-protection pieces that are missing from the base bot:
  exchange-side SL/TP registration, real-position reconciliation, duplicate-entry locks,
  and decision logging.
- Include the high-risk features the user explicitly asked to include anyway:
  AI leverage boost, short reactivation support, multi-position continuation,
  scalping bias, DCA/averaging-down, and a no-dependency online model.

Important
- This file is intentionally a runtime patch, not a trader.py rewrite.
- Most risky features are controlled by .env flags.
- Default values keep destructive features OFF unless the user enables them.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

try:
    import trader as _t
    from trader import Trader
except Exception as _e:  # pragma: no cover
    print(f"[ALLIN_PATCH] boot failed: {_e}", flush=True)
    _t = None
    Trader = None  # type: ignore

try:
    from storage_utils import data_path, atomic_write_json, safe_read_json
except Exception:  # pragma: no cover
    def data_path(name: str) -> str:
        Path("data").mkdir(exist_ok=True)
        return str(Path("data") / name)

    def atomic_write_json(path: str, obj: Any) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def safe_read_json(path: str, default: Any = None) -> Any:
        try:
            p = Path(path)
            if not p.exists():
                return default
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default


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


def _now() -> float:
    return time.time()


def _side_to_exchange(side: str) -> str:
    return "Buy" if str(side).upper() == "LONG" else "Sell"


def _close_side_to_exchange(side: str) -> str:
    return "Sell" if str(side).upper() == "LONG" else "Buy"


def _position_idx_for_open(side: str):
    fn = getattr(_t, "_position_idx_for", None)
    if callable(fn):
        return fn(_side_to_exchange(side))
    return None


def _fmt_price(x: Any) -> str:
    try:
        return f"{float(x):.8f}".rstrip("0").rstrip(".")
    except Exception:
        return str(x)


def _jsonl_append(path: str, obj: dict[str, Any]) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


# Main flags
ALLIN_PATCH_ON = _env_bool("ALLIN_PATCH_ON", True)

# Account protection / observability
EXCHANGE_SLTP_ON = _env_bool("EXCHANGE_SLTP_ON", True)
POSITION_RECONCILE_ON = _env_bool("POSITION_RECONCILE_ON", True)
DECISION_LOG_ON = _env_bool("DECISION_LOG_ON", True)
ORDER_LOCK_SEC = _env_float("ORDER_LOCK_SEC", 20.0)
DUP_ENTRY_BLOCK_SEC = _env_float("DUP_ENTRY_BLOCK_SEC", 60.0)
DECISION_LOG_PATH = os.getenv("DECISION_LOG_PATH", data_path("decision_log.jsonl"))

# Experimental high-risk features. Defaults are intentionally conservative.
AI_AUTO_LEVERAGE = _env_bool("AI_AUTO_LEVERAGE", False)
AI_AUTO_LEV_MIN_TRADES = _env_int("AI_AUTO_LEV_MIN_TRADES", 50)
AI_AUTO_LEV_MIN_WR = _env_float("AI_AUTO_LEV_MIN_WR", 60.0)
AI_AUTO_LEV_MIN_SCORE = _env_float("AI_AUTO_LEV_MIN_SCORE", 85.0)
AI_AUTO_LEV_MAX = _env_int("AI_AUTO_LEV_MAX", 8)
AI_AUTO_LEV_SCORE_STEP = _env_float("AI_AUTO_LEV_SCORE_STEP", 7.5)

DL_LITE_ON = _env_bool("DL_LITE_ON", False)
DL_LITE_MODEL_PATH = os.getenv("DL_LITE_MODEL_PATH", data_path("dl_lite_model.json"))
DL_LITE_MIN_TRADES = _env_int("DL_LITE_MIN_TRADES", 30)
DL_LITE_BLOCK_PROB = _env_float("DL_LITE_BLOCK_PROB", 0.42)
DL_LITE_BOOST_MAX = _env_float("DL_LITE_BOOST_MAX", 12.0)
DL_LITE_LR = _env_float("DL_LITE_LR", 0.04)

EXPERIMENTAL_MULTI_POS_ON = _env_bool("EXPERIMENTAL_MULTI_POS_ON", False)
EXPERIMENTAL_SCALP_MODE_ON = _env_bool("EXPERIMENTAL_SCALP_MODE_ON", False)
SCALP_ENTER_SCORE_DELTA = _env_int("SCALP_ENTER_SCORE_DELTA", -3)
SCALP_STOP_ATR = _env_float("SCALP_STOP_ATR", 0.9)
SCALP_TP_R = _env_float("SCALP_TP_R", 1.0)
SCALP_MAX_HOLD_MIN = _env_float("SCALP_MAX_HOLD_MIN", 45.0)

DCA_ON = _env_bool("DCA_ON", False)
DCA_MAX = _env_int("DCA_MAX", 1)
DCA_DROP_PCT = _env_float("DCA_DROP_PCT", 0.012)
DCA_ATR_STEP = _env_float("DCA_ATR_STEP", 1.2)
DCA_ORDER_MULT = _env_float("DCA_ORDER_MULT", 0.45)
DCA_COOLDOWN_SEC = _env_float("DCA_COOLDOWN_SEC", 900.0)
DCA_REANCHOR_SL_ATR = _env_float("DCA_REANCHOR_SL_ATR", 1.8)

_order_lock_until: dict[str, float] = {}
_recent_entry_until: dict[str, float] = {}


def _lock_key(symbol: str, side: str) -> str:
    return f"{str(symbol).upper()}:{str(side).upper()}"


def _blocked_by_order_lock(symbol: str, side: str) -> tuple[bool, str]:
    key = _lock_key(symbol, side)
    now = _now()
    until1 = _order_lock_until.get(key, 0.0)
    until2 = _recent_entry_until.get(key, 0.0)
    until = max(until1, until2)
    if until > now:
        return True, f"ORDER_LOCK {key} left={int(until - now)}s"
    return False, ""


def _set_order_lock(symbol: str, side: str, order_sec: float | None = None, dup_sec: float | None = None) -> None:
    key = _lock_key(symbol, side)
    now = _now()
    _order_lock_until[key] = now + float(ORDER_LOCK_SEC if order_sec is None else order_sec)
    _recent_entry_until[key] = now + float(DUP_ENTRY_BLOCK_SEC if dup_sec is None else dup_sec)


def _winrate_snapshot(self: Any) -> tuple[int, float]:
    w = int(getattr(self, "win", 0) or 0)
    l = int(getattr(self, "loss", 0) or 0)
    n = w + l
    wr = (100.0 * w / n) if n else 0.0
    return n, wr


def _compute_auto_lev(self: Any, base_lev: int, score: float) -> int:
    if not AI_AUTO_LEVERAGE:
        return int(base_lev)
    n, wr = _winrate_snapshot(self)
    if n < AI_AUTO_LEV_MIN_TRADES or wr < AI_AUTO_LEV_MIN_WR or float(score or 0) < AI_AUTO_LEV_MIN_SCORE:
        return int(base_lev)
    extra = int(max(0.0, (float(score or 0) - AI_AUTO_LEV_MIN_SCORE) // max(1.0, AI_AUTO_LEV_SCORE_STEP)))
    target = int(base_lev) + extra
    return int(max(1, min(AI_AUTO_LEV_MAX, target)))


def _load_dl_model() -> dict[str, Any]:
    default = {"bias": 0.0, "weights": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "trades": 0}
    obj = safe_read_json(DL_LITE_MODEL_PATH, default)
    if not isinstance(obj, dict):
        return default
    w = obj.get("weights")
    if not isinstance(w, list) or len(w) != 6:
        obj["weights"] = default["weights"][:]
    obj["bias"] = _safe_float(obj.get("bias"), 0.0)
    obj["trades"] = int(_safe_float(obj.get("trades"), 0))
    return obj


def _save_dl_model(model: dict[str, Any]) -> None:
    try:
        atomic_write_json(DL_LITE_MODEL_PATH, model)
    except Exception:
        pass


def _sigmoid(x: float) -> float:
    if x > 35:
        return 1.0
    if x < -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _extract_features(symbol: str, side: str, price: float, atr_value: float | None = None) -> list[float]:
    """Small no-dependency feature vector. This is not real deep learning."""
    try:
        interval = str(getattr(_t, "ENTRY_INTERVAL", "15"))
        limit = max(120, int(getattr(_t, "EMA_SLOW", 50)) * 4)
        kl = _t.get_klines(symbol, interval, limit) or []
        if len(kl) < 60:
            return [0.0, 0.0, 0.0, 0.0, 1.0 if str(side).upper() == "LONG" else -1.0, 0.0]
        kl = list(reversed(kl))
        closes = [float(x[4]) for x in kl if _safe_float(x[4], 0) > 0]
        highs = [float(x[2]) for x in kl[-len(closes):]]
        lows = [float(x[3]) for x in kl[-len(closes):]]
        ef = float(_t.ema(closes[-int(getattr(_t, "EMA_FAST", 20)) * 3:], int(getattr(_t, "EMA_FAST", 20))))
        es = float(_t.ema(closes[-int(getattr(_t, "EMA_SLOW", 50)) * 3:], int(getattr(_t, "EMA_SLOW", 50))))
        rsi = _t.rsi(closes, int(getattr(_t, "RSI_PERIOD", 14))) or 50.0
        atr_v = atr_value or _t.atr(highs, lows, closes, int(getattr(_t, "ATR_PERIOD", 14))) or (price * 0.005)
        ret5 = (closes[-1] / closes[-6] - 1.0) if len(closes) >= 6 and closes[-6] else 0.0
        trend = (ef - es) / max(float(price), 1e-9)
        vol = float(atr_v) / max(float(price), 1e-9)
        rsi_n = (float(rsi) - 50.0) / 50.0
        side_n = 1.0 if str(side).upper() == "LONG" else -1.0
        chase = abs(float(price) - ef) / max(float(atr_v), 1e-9)
        return [max(-0.2, min(0.2, ret5)) * 5.0, max(-0.2, min(0.2, trend)) * 5.0, rsi_n, max(0.0, min(0.08, vol)) * 10.0, side_n, max(0.0, min(4.0, chase)) / 4.0]
    except Exception:
        return [0.0, 0.0, 0.0, 0.0, 1.0 if str(side).upper() == "LONG" else -1.0, 0.0]


def _dl_predict(features: list[float]) -> tuple[float, dict[str, Any]]:
    model = _load_dl_model()
    w = [float(x) for x in model.get("weights", [0.0] * 6)]
    z = float(model.get("bias", 0.0)) + sum(float(a) * float(b) for a, b in zip(w, features))
    return _sigmoid(z), model


def _dl_update(features: list[float], label: int) -> None:
    model = _load_dl_model()
    w = [float(x) for x in model.get("weights", [0.0] * 6)]
    pred = _sigmoid(float(model.get("bias", 0.0)) + sum(a * b for a, b in zip(w, features)))
    err = float(label) - pred
    lr = DL_LITE_LR
    model["bias"] = float(model.get("bias", 0.0)) + lr * err
    model["weights"] = [wi + lr * err * float(xi) for wi, xi in zip(w, features)]
    model["trades"] = int(model.get("trades", 0) or 0) + 1
    model["last_pred"] = pred
    model["last_label"] = int(label)
    model["updated_ts"] = int(_now())
    _save_dl_model(model)


def _register_exchange_sltp(symbol: str, side: str, sl: Any, tp: Any) -> tuple[bool, str]:
    if not EXCHANGE_SLTP_ON:
        return True, "SLTP_OFF"
    if bool(getattr(_t, "DRY_RUN", False)):
        return True, "DRY_RUN"
    if not symbol or sl is None or tp is None:
        return False, "SLTP_MISSING"
    try:
        body = {
            "category": getattr(_t, "CATEGORY", "linear"),
            "symbol": str(symbol).upper(),
            "tpslMode": "Full",
            "stopLoss": _fmt_price(sl),
            "takeProfit": _fmt_price(tp),
        }
        pidx = _position_idx_for_open(side)
        if pidx is not None:
            body["positionIdx"] = pidx
        resp = _t.http.request("POST", "/v5/position/trading-stop", body, auth=True)
        ok = (resp or {}).get("retCode") == 0
        return bool(ok), str(resp)
    except Exception as e:
        return False, repr(e)


def _real_positions() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if bool(getattr(_t, "DRY_RUN", False)):
        return out
    try:
        for p in _t.get_positions_all() or []:
            size = _safe_float(p.get("size"), 0.0)
            if size <= 0:
                continue
            sym = str(p.get("symbol") or "").upper()
            ex_side = str(p.get("side") or "")
            side = "LONG" if ex_side == "Buy" else "SHORT"
            entry = _safe_float(p.get("avgPrice") or p.get("entryPrice"), 0.0)
            mark = _safe_float(p.get("markPrice") or p.get("lastPrice"), entry)
            sl = _safe_float(p.get("stopLoss"), 0.0) or None
            tp = _safe_float(p.get("takeProfit"), 0.0) or None
            out.append({"symbol": sym, "side": side, "size": size, "entry_price": entry, "mark_price": mark, "stop_price": sl, "tp_price": tp})
    except Exception:
        return out
    return out


def _recover_missing_positions(self: Any) -> None:
    if not POSITION_RECONCILE_ON or bool(getattr(_t, "DRY_RUN", False)):
        return
    real = _real_positions()
    self.state["real_positions"] = real[:10]
    internal_keys = {_lock_key(p.get("symbol"), p.get("side")) for p in list(getattr(self, "positions", []) or [])}
    added = 0
    for rp in real:
        key = _lock_key(rp.get("symbol"), rp.get("side"))
        if key in internal_keys:
            continue
        entry = _safe_float(rp.get("entry_price"), 0.0)
        if entry <= 0:
            entry = _safe_float(rp.get("mark_price"), 0.0)
        if entry <= 0:
            continue
        price = _safe_float(rp.get("mark_price"), entry)
        atr_guess = max(entry * 0.006, abs(price - entry))
        side = str(rp.get("side") or "LONG").upper()
        stop = rp.get("stop_price")
        take = rp.get("tp_price")
        if not stop:
            stop = entry - atr_guess * 1.8 if side == "LONG" else entry + atr_guess * 1.8
        if not take:
            take = entry + atr_guess * 2.2 if side == "LONG" else entry - atr_guess * 2.2
        pos = {
            "symbol": rp["symbol"], "side": side, "entry_price": entry,
            "entry_ts": _now(), "stop_price": stop, "tp_price": take,
            "trail_price": None, "tp1_price": None, "tp1_done": False,
            "last_order_usdt": 0.0, "last_lev": 1.0,
            "recovered": True, "recovered_size": rp.get("size"),
        }
        self.positions.append(pos)
        internal_keys.add(key)
        added += 1
    if added:
        try:
            self.notify_throttled(f"🧭 실포지션 복구: {added}개 내부 상태에 추가", 120)
        except Exception:
            pass


def _position_qty(symbol: str, side: str | None = None) -> float:
    try:
        if bool(getattr(_t, "DRY_RUN", False)):
            return 0.0
        best = 0.0
        for p in _t.get_positions_all(symbol) or []:
            if str(p.get("symbol") or "").upper() != str(symbol).upper():
                continue
            if side:
                ex = "Buy" if str(side).upper() == "LONG" else "Sell"
                if str(p.get("side") or "") != ex:
                    continue
            best += abs(_safe_float(p.get("size"), 0.0))
        return best
    except Exception:
        return 0.0


def _norm_qty(self: Any, symbol: str, qty: float) -> float:
    try:
        step, min_qty = self._get_lot_size(symbol)
        q = math.floor(float(qty) / float(step)) * float(step)
        if q < float(min_qty):
            q = float(min_qty)
        return float(round(q, 8))
    except Exception:
        try:
            return float(_t.fix_qty(qty, symbol))
        except Exception:
            return float(qty)


def _dca_before_manage(self: Any, pos: dict[str, Any]) -> None:
    if not DCA_ON or bool(getattr(_t, "DRY_RUN", False)):
        return
    symbol = str(pos.get("symbol") or "").upper()
    side = str(pos.get("side") or "LONG").upper()
    if not symbol:
        return
    count = int(pos.get("dca_count", 0) or 0)
    if count >= DCA_MAX:
        return
    last_ts = _safe_float(pos.get("last_dca_ts"), 0.0)
    if _now() - last_ts < DCA_COOLDOWN_SEC:
        return
    price = float(_t.get_price(symbol))
    entry = _safe_float(pos.get("entry_price"), price)
    if entry <= 0 or price <= 0:
        return
    adverse_pct = (entry - price) / entry if side == "LONG" else (price - entry) / entry
    atr_v = _safe_float(pos.get("atr"), 0.0)
    if atr_v <= 0:
        try:
            mp = self._mp()
            _, _, _, _, _, atr_v = _t.compute_signal_and_exits(symbol, side, price, mp, avoid_low_rsi=False)
            atr_v = _safe_float(atr_v, price * 0.005)
        except Exception:
            atr_v = price * 0.005
    adverse_atr = abs(price - entry) / max(atr_v, 1e-9)
    if adverse_pct < DCA_DROP_PCT and adverse_atr < DCA_ATR_STEP:
        return

    blocked, msg = _blocked_by_order_lock(symbol, side)
    if blocked:
        self.state["last_dca_block"] = msg
        return

    mp = self._mp()
    lev = float(mp.get("lev", 1))
    order_usdt = max(0.0, float(mp.get("order_usdt", 0.0)) * DCA_ORDER_MULT)
    if order_usdt <= 0:
        return
    raw_qty = (order_usdt * lev) / price
    qty = _norm_qty(self, symbol, raw_qty)
    if qty <= 0:
        return
    _set_order_lock(symbol, side)
    _t.order_market(symbol, _side_to_exchange(side), qty, reduce_only=False)

    old_qty = _safe_float(pos.get("recovered_size"), 0.0) or _safe_float(pos.get("qty"), 0.0) or _position_qty(symbol, side) or qty
    new_qty = old_qty + qty
    new_entry = ((entry * old_qty) + (price * qty)) / max(new_qty, 1e-9)
    pos["entry_price"] = new_entry
    pos["dca_count"] = count + 1
    pos["last_dca_ts"] = _now()
    pos["recovered_size"] = new_qty
    if atr_v > 0:
        pos["stop_price"] = new_entry - atr_v * DCA_REANCHOR_SL_ATR if side == "LONG" else new_entry + atr_v * DCA_REANCHOR_SL_ATR
    self.state["last_dca"] = {"symbol": symbol, "side": side, "qty": qty, "price": price, "count": pos["dca_count"]}
    try:
        self.notify(f"⚠️ DCA 추가진입 {symbol} {side} qty={qty} price={price:.6f} count={pos['dca_count']}/{DCA_MAX}")
    except Exception:
        pass


def _log_decision(symbol: str, price: float, info: dict[str, Any]) -> None:
    if not DECISION_LOG_ON:
        return
    obj = {
        "ts": int(_now()), "symbol": str(symbol).upper(), "price": price,
        "ok": bool(info.get("ok")), "side": info.get("side"),
        "score": info.get("score"), "reason": str(info.get("reason", ""))[:500],
        "sl": info.get("sl"), "tp": info.get("tp"), "atr": info.get("atr"),
        "strategy": info.get("strategy"),
    }
    _jsonl_append(DECISION_LOG_PATH, obj)


def _try_extra_entry(self: Any) -> None:
    if not EXPERIMENTAL_MULTI_POS_ON:
        return
    if not getattr(self, "trading_enabled", False):
        return
    max_pos = int(getattr(_t, "MAX_POSITIONS", _env_int("MAX_POSITIONS", 1)) or 1)
    if len(getattr(self, "positions", []) or []) >= max_pos:
        return
    if _now() < _safe_float(getattr(self, "_cooldown_until", 0.0), 0.0):
        return
    if getattr(_t, "entry_allowed_now_utc", lambda: True)() is False:
        return
    if int(getattr(self, "_day_entries", 0) or 0) >= int(getattr(_t, "MAX_ENTRIES_PER_DAY", 999)):
        return
    existing = {_lock_key(p.get("symbol"), p.get("side")) for p in getattr(self, "positions", []) or []}
    pick = self.pick_best()
    if not pick or not pick.get("ok"):
        return
    key = _lock_key(pick.get("symbol"), pick.get("side"))
    if key in existing:
        self.state["last_extra_entry_block"] = f"DUP_POS {key}"
        return
    blocked, msg = _blocked_by_order_lock(pick["symbol"], pick["side"])
    if blocked:
        self.state["last_extra_entry_block"] = msg
        return
    self._enter(pick["symbol"], pick["side"], pick["price"], pick["reason"], pick["sl"], pick["tp"], str(pick.get("strategy") or ""), float(pick.get("score") or 0.0), float(pick.get("atr") or 0.0))
    self.state["last_event"] = f"EXTRA_ENTER {pick['symbol']} {pick['side']}"


if _t is not None and Trader is not None and ALLIN_PATCH_ON:
    # Patch mode parameters for scalping bias.
    _orig_mp = getattr(Trader, "_mp", None)
    if callable(_orig_mp):
        def _patched_mp(self):
            mp = dict(_orig_mp(self))
            if EXPERIMENTAL_SCALP_MODE_ON:
                mp["enter_score"] = max(1, int(mp.get("enter_score", 70)) + int(SCALP_ENTER_SCORE_DELTA))
                mp["stop_atr"] = float(SCALP_STOP_ATR)
                mp["tp_r"] = float(SCALP_TP_R)
                self.state["scalp_mode"] = True
            return mp
        Trader._mp = _patched_mp

    # Patch compute_signal for lightweight model scoring.
    _orig_compute = getattr(_t, "compute_signal_and_exits", None)
    if callable(_orig_compute):
        def _patched_compute_signal_and_exits(symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False):
            ok, reason, score, sl, tp, atr_v = _orig_compute(symbol, side, price, mp, avoid_low_rsi=avoid_low_rsi)
            if not DL_LITE_ON:
                return ok, reason, score, sl, tp, atr_v
            features = _extract_features(symbol, side, float(price), _safe_float(atr_v, 0.0))
            prob, model = _dl_predict(features)
            trades = int(model.get("trades", 0) or 0)
            boost = 0.0
            if trades >= DL_LITE_MIN_TRADES:
                boost = max(-DL_LITE_BOOST_MAX, min(DL_LITE_BOOST_MAX, (prob - 0.5) * DL_LITE_BOOST_MAX * 2.0))
                score = int(max(0, min(100, int(score or 0) + boost)))
                if prob < DL_LITE_BLOCK_PROB:
                    ok = False
                    reason = f"{reason}\n- dl_lite=BLOCK prob={prob:.3f} trades={trades} boost={boost:.1f}"
                else:
                    threshold = int(mp.get("enter_score", 70))
                    ok = bool(ok and int(score) >= threshold)
                    reason = f"{reason}\n- dl_lite=prob={prob:.3f} trades={trades} boost={boost:.1f} score2={score}"
            else:
                reason = f"{reason}\n- dl_lite=warming prob={prob:.3f} trades={trades}/{DL_LITE_MIN_TRADES}"
            return ok, reason, score, sl, tp, atr_v
        _t.compute_signal_and_exits = _patched_compute_signal_and_exits

    # Patch score logging.
    _orig_score_symbol = getattr(Trader, "_score_symbol", None)
    if callable(_orig_score_symbol):
        def _patched_score_symbol(self, symbol: str, price: float):
            info = _orig_score_symbol(self, symbol, price)
            if isinstance(info, dict):
                _log_decision(symbol, float(price or 0.0), info)
            return info
        Trader._score_symbol = _patched_score_symbol

    # Patch real-position sync/recovery.
    _orig_sync_real_positions = getattr(Trader, "_sync_real_positions", None)
    if callable(_orig_sync_real_positions):
        def _patched_sync_real_positions(self):
            result = _orig_sync_real_positions(self)
            _recover_missing_positions(self)
            return result
        Trader._sync_real_positions = _patched_sync_real_positions

    # Patch entry: duplicate lock, optional AI leverage, exchange SL/TP, feature capture.
    _orig_enter = getattr(Trader, "_enter", None)
    if callable(_orig_enter):
        def _patched_enter(self, symbol: str, side: str, price: float, reason: str, sl: float, tp: float, strategy: str = "", score: float = 0.0, atr: float = 0.0):
            blocked, msg = _blocked_by_order_lock(symbol, side)
            if blocked:
                raise Exception(msg)
            _set_order_lock(symbol, side, order_sec=ORDER_LOCK_SEC, dup_sec=DUP_ENTRY_BLOCK_SEC)

            restore = None
            if AI_AUTO_LEVERAGE:
                try:
                    mp = self._mp()
                    base_lev = int(float(mp.get("lev", 1)))
                    new_lev = _compute_auto_lev(self, base_lev, float(score or 0.0))
                    if new_lev != base_lev:
                        restore = dict(getattr(self, "tune", {}).get(self.mode, {}))
                        self.tune.setdefault(self.mode, {})["lev"] = int(new_lev)
                        self.state["ai_auto_leverage"] = {"base": base_lev, "target": new_lev, "score": float(score or 0.0), "mode": self.mode}
                except Exception:
                    restore = None
            try:
                before_len = len(getattr(self, "positions", []) or [])
                ret = _orig_enter(self, symbol, side, price, reason, sl, tp, strategy, score, atr)
                if DL_LITE_ON and len(getattr(self, "positions", []) or []) > before_len:
                    try:
                        self.positions[-1]["dl_features"] = _extract_features(symbol, side, float(price), _safe_float(atr, 0.0))
                    except Exception:
                        pass
                ok, detail = _register_exchange_sltp(symbol, side, sl, tp)
                self.state["exchange_sltp"] = {"symbol": symbol, "side": side, "ok": ok, "detail": detail[:500]}
                if not ok:
                    try:
                        self.notify_throttled(f"⚠️ 거래소 SL/TP 등록 실패: {symbol} {side} {detail[:300]}", 60)
                    except Exception:
                        pass
                return ret
            finally:
                if restore is not None:
                    try:
                        self.tune[self.mode] = restore
                    except Exception:
                        pass
        Trader._enter = _patched_enter

    # Patch close quantity normalization and side-aware qty.
    _orig_close_qty = getattr(Trader, "_close_qty", None)
    if callable(_orig_close_qty):
        def _patched_close_qty(self, symbol: str, side: str, close_qty: float):
            qty = _norm_qty(self, symbol, float(close_qty or 0.0))
            real_qty = _position_qty(symbol, side)
            if real_qty > 0:
                qty = min(qty, real_qty)
            return _orig_close_qty(self, symbol, side, qty)
        Trader._close_qty = _patched_close_qty

    # Patch manage: optional DCA and scalping time exit.
    _orig_manage_one = getattr(Trader, "_manage_one", None)
    if callable(_orig_manage_one):
        def _patched_manage_one(self, idx: int):
            if idx < 0 or idx >= len(getattr(self, "positions", []) or []):
                return None
            pos = self.positions[idx]
            if EXPERIMENTAL_SCALP_MODE_ON:
                try:
                    if _now() - _safe_float(pos.get("entry_ts"), _now()) > SCALP_MAX_HOLD_MIN * 60:
                        return self._exit_position(idx, "SCALP TIME EXIT")
                except Exception:
                    pass
            try:
                _dca_before_manage(self, pos)
            except Exception as e:
                try:
                    self.err_throttled(f"❌ DCA 실패: {e}")
                except Exception:
                    pass
            return _orig_manage_one(self, idx)
        Trader._manage_one = _patched_manage_one

    # Patch exit: train lightweight model from trade outcome.
    _orig_exit_position = getattr(Trader, "_exit_position", None)
    if callable(_orig_exit_position):
        def _patched_exit_position(self, idx: int, why: str, force: bool = False):
            pos = None
            before_day = _safe_float(getattr(self, "day_profit", 0.0), 0.0)
            try:
                if 0 <= idx < len(getattr(self, "positions", []) or []):
                    pos = dict(self.positions[idx])
            except Exception:
                pos = None
            ret = _orig_exit_position(self, idx, why, force=force)
            if DL_LITE_ON and pos:
                after_day = _safe_float(getattr(self, "day_profit", before_day), before_day)
                pnl_delta = after_day - before_day
                feats = pos.get("dl_features") or _extract_features(pos.get("symbol", ""), pos.get("side", "LONG"), _safe_float(pos.get("entry_price"), 0.0), _safe_float(pos.get("atr"), 0.0))
                if isinstance(feats, list) and len(feats) == 6:
                    _dl_update([float(x) for x in feats], 1 if pnl_delta > 0 else 0)
                    self.state["dl_lite_last_train"] = {"symbol": pos.get("symbol"), "side": pos.get("side"), "pnl_delta": pnl_delta, "label": 1 if pnl_delta > 0 else 0}
            return ret
        Trader._exit_position = _patched_exit_position

    # Patch tick: after base tick returns from managing existing positions, optionally add more positions.
    _orig_tick = getattr(Trader, "tick", None)
    if callable(_orig_tick):
        def _patched_tick(self):
            ret = _orig_tick(self)
            try:
                _try_extra_entry(self)
            except Exception as e:
                try:
                    self.err_throttled(f"❌ extra entry 실패: {e}")
                except Exception:
                    self.state["extra_entry_error"] = str(e)
            return ret
        Trader.tick = _patched_tick

    print(
        "[ALLIN_PATCH] loaded: exchange SLTP/reconcile/locks/logging + experimental leverage/DL-lite/multi/scalp/DCA hooks",
        flush=True,
    )
else:
    print("[ALLIN_PATCH] disabled", flush=True)
