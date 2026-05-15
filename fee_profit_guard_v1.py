"""fee_profit_guard_v1.py

Blocks trades where the target is too small after round-trip fee and slippage.
This directly addresses the common case: "the trade was technically right but PnL
is flat/negative after costs".
"""

from __future__ import annotations

import os
from typing import Any


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


FEE_PROFIT_GUARD_ON = _env_bool("FEE_PROFIT_GUARD_ON", True)
MIN_TP_AFTER_FEE_PCT = _env_float("MIN_TP_AFTER_FEE_PCT", 0.0060)  # 0.6%
MIN_NET_R_PCT = _env_float("MIN_NET_R_PCT", 0.0040)                # 0.4%
MIN_REWARD_RISK = _env_float("MIN_REWARD_RISK", 1.10)


def calc_cost_pct(trader_module: Any = None) -> float:
    fee = _env_float("FEE_RATE", _safe_float(getattr(trader_module, "FEE_RATE", 0.0006) if trader_module else 0.0006, 0.0006))
    slip_bps = _env_float("SLIPPAGE_BPS", _safe_float(getattr(trader_module, "SLIPPAGE_BPS", 5.0) if trader_module else 5.0, 5.0))
    slip = slip_bps / 10000.0
    # market entry + market/reduce close estimate
    return max(0.0, 2.0 * fee + 2.0 * slip)


def evaluate_profit_guard(side: str, price: float, sl: float | None, tp: float | None, trader_module: Any = None):
    if not FEE_PROFIT_GUARD_ON:
        return True, "FEE_GUARD_OFF", {"enabled": False}
    price = float(price or 0.0)
    if price <= 0 or sl is None or tp is None:
        return False, "FEE_GUARD_BAD_PRICE_OR_TARGET", {"enabled": True}

    side = str(side or "LONG").upper()
    tp_move = abs(float(tp) - price) / max(price, 1e-9)
    stop_move = abs(price - float(sl)) / max(price, 1e-9)
    cost = calc_cost_pct(trader_module)
    net_tp = tp_move - cost
    net_stop = stop_move + cost
    rr = net_tp / max(net_stop, 1e-9)

    metrics = {
        "enabled": True,
        "side": side,
        "tp_move_pct": tp_move,
        "stop_move_pct": stop_move,
        "roundtrip_cost_pct": cost,
        "net_tp_pct": net_tp,
        "net_stop_pct": net_stop,
        "reward_risk_after_cost": rr,
        "min_tp_after_fee_pct": MIN_TP_AFTER_FEE_PCT,
        "min_net_r_pct": MIN_NET_R_PCT,
        "min_reward_risk": MIN_REWARD_RISK,
    }

    blocks = []
    if net_tp < MIN_TP_AFTER_FEE_PCT:
        blocks.append(f"NET_TP_LOW {net_tp:.4%}<{MIN_TP_AFTER_FEE_PCT:.4%}")
    if net_tp < MIN_NET_R_PCT:
        blocks.append(f"NET_EDGE_LOW {net_tp:.4%}<{MIN_NET_R_PCT:.4%}")
    if rr < MIN_REWARD_RISK:
        blocks.append(f"RR_LOW {rr:.2f}<{MIN_REWARD_RISK:.2f}")

    if blocks:
        return False, "FEE_PROFIT_BLOCK " + "; ".join(blocks), metrics
    return True, f"FEE_PROFIT_PASS net_tp={net_tp:.4%} rr={rr:.2f}", metrics
