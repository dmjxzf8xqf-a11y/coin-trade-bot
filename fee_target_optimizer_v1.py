"""fee_target_optimizer_v1.py

Fee-aware target optimizer.

Problem it solves:
- signal_engine can PASS but fee_profit_guard can block because target is too close.
- Instead of immediately throwing the setup away, this patch tries a small TP/SL grid.
- If a safer TP/SL pair passes the existing fee guard, it allows the signal with adjusted SL/TP.

Safety:
- Default is DRY_RUN-only (`TARGET_OPT_DRY_RUN_ONLY=true`).
- It only activates when the previous reason contains SIGNAL_ENGINE result=PASS and FEE_PROFIT_BLOCK.
- It still uses fee_profit_guard_v1, so it does not bypass cost math.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import trader as _t
    from trader import Trader
except Exception as e:  # pragma: no cover
    print(f"[TARGET_OPT] boot failed: {e}", flush=True)
    _t = None
    Trader = None  # type: ignore

try:
    from fee_profit_guard_v1 import evaluate_profit_guard
except Exception:
    evaluate_profit_guard = None  # type: ignore


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


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _levels(raw: str | None, default: list[float]) -> list[float]:
    if not raw:
        return default
    vals: list[float] = []
    for part in str(raw).replace(";", ",").split(","):
        try:
            vals.append(float(part.strip()))
        except Exception:
            pass
    return vals or default


TARGET_OPT_ON = _env_bool("TARGET_OPTIMIZER_ON", True)
TARGET_OPT_DRY_RUN_ONLY = _env_bool("TARGET_OPT_DRY_RUN_ONLY", True)
TARGET_OPT_MIN_SCORE = _env_float("TARGET_OPT_MIN_SCORE", 70.0)
TARGET_OPT_MAX_TP_MOVE_PCT = _env_float("TARGET_OPT_MAX_TP_MOVE_PCT", 0.0120)  # 1.2%
TARGET_OPT_MAX_STOP_MOVE_PCT = _env_float("TARGET_OPT_MAX_STOP_MOVE_PCT", 0.0080)  # 0.8%

if _t is not None and callable(evaluate_profit_guard) and TARGET_OPT_ON:
    _orig_compute = getattr(_t, "compute_signal_and_exits", None)
    if callable(_orig_compute):
        def _patched_compute_signal_and_exits(symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False):
            ok, reason, score, sl, tp, atr_v = _orig_compute(symbol, side, price, mp, avoid_low_rsi=avoid_low_rsi)
            try:
                dry_run = bool(getattr(_t, "DRY_RUN", False)) or _env_bool("DRY_RUN", False)
                if TARGET_OPT_DRY_RUN_ONLY and not dry_run:
                    return ok, reason, score, sl, tp, atr_v
                reason_s = str(reason or "")
                if ok:
                    return ok, reason, score, sl, tp, atr_v
                if "FEE_PROFIT_BLOCK" not in reason_s:
                    return ok, reason, score, sl, tp, atr_v
                if "result=PASS" not in reason_s and "SIGNAL_ENGINE" not in reason_s:
                    return ok, reason, score, sl, tp, atr_v
                score_f = _safe_float(score, 0.0)
                if score_f < TARGET_OPT_MIN_SCORE:
                    return ok, reason, score, sl, tp, atr_v
                price_f = _safe_float(price, 0.0)
                atr_f = _safe_float(atr_v, 0.0)
                if price_f <= 0 or atr_f <= 0:
                    return ok, reason, score, sl, tp, atr_v

                base_stop_atr = _safe_float((mp or {}).get("stop_atr"), 1.0)
                base_tp_r = _safe_float((mp or {}).get("tp_r"), 1.0)
                stop_levels = _levels(os.getenv("TARGET_OPT_STOP_ATR_LEVELS"), [max(0.45, base_stop_atr * 0.70), base_stop_atr * 0.85, base_stop_atr])
                tp_levels = _levels(os.getenv("TARGET_OPT_TP_R_LEVELS"), [base_tp_r, 1.15, 1.30, 1.50, 1.80, 2.00])

                side_u = str(side or "LONG").upper()
                best = None
                for stop_atr in stop_levels:
                    stop_dist = atr_f * max(0.05, float(stop_atr))
                    if stop_dist / price_f > TARGET_OPT_MAX_STOP_MOVE_PCT:
                        continue
                    for tp_r in tp_levels:
                        tp_dist = stop_dist * max(0.05, float(tp_r))
                        if tp_dist / price_f > TARGET_OPT_MAX_TP_MOVE_PCT:
                            continue
                        if side_u == "LONG":
                            sl2 = price_f - stop_dist
                            tp2 = price_f + tp_dist
                        else:
                            sl2 = price_f + stop_dist
                            tp2 = price_f - tp_dist
                        pass_fee, fee_msg, metrics = evaluate_profit_guard(side_u, price_f, sl2, tp2, trader_module=_t)
                        if not pass_fee:
                            continue
                        rr = _safe_float((metrics or {}).get("reward_risk_after_cost"), 0.0)
                        net = _safe_float((metrics or {}).get("net_tp_pct"), 0.0)
                        cand = (rr, net, stop_atr, tp_r, sl2, tp2, fee_msg)
                        if best is None or cand[:2] > best[:2]:
                            best = cand
                if best is None:
                    return ok, reason, score, sl, tp, atr_v
                rr, net, stop_atr, tp_r, sl2, tp2, fee_msg = best
                reason2 = (
                    reason_s
                    + f"- TARGET_OPT_PASS stop_atr={stop_atr:.2f} tp_r={tp_r:.2f} net_tp={net:.4%} rr={rr:.2f}\n"
                    + f"- {fee_msg}\n"
                )
                return True, reason2, score, sl2, tp2, atr_v
            except Exception as e:
                try:
                    print(f"[TARGET_OPT] error: {e}", flush=True)
                except Exception:
                    pass
                return ok, reason, score, sl, tp, atr_v

        _t.compute_signal_and_exits = _patched_compute_signal_and_exits

        if Trader is not None:
            def _method_compute_signal_and_exits(self, symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False):
                return _t.compute_signal_and_exits(symbol, side, price, mp, avoid_low_rsi=avoid_low_rsi)
            Trader.compute_signal_and_exits = _method_compute_signal_and_exits
        print("[TARGET_OPT] loaded: fee-aware TP/SL optimizer", flush=True)
    else:
        print("[TARGET_OPT] disabled: compute_signal_and_exits not found", flush=True)
else:
    print("[TARGET_OPT] disabled", flush=True)
