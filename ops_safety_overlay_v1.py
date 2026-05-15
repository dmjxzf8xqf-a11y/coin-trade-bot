"""ops_safety_overlay_v1.py

Conservative runtime safety overlay.

It does not add new alpha. It prevents the bot from increasing risk while the
observed edge is unproven, and caps leverage/order size from the outside.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Tuple


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


def risky_flags_on() -> Dict[str, bool]:
    keys = [
        "AI_AUTO_LEVERAGE",
        "DL_LITE_ON",
        "DCA_ON",
        "EXPERIMENTAL_MULTI_POS_ON",
        "EXPERIMENTAL_SCALP_MODE_ON",
    ]
    return {k: _env_bool(k, False) for k in keys}


def observed_stats(trader_obj: Any) -> Tuple[int, float, int, float]:
    try:
        win = int(getattr(trader_obj, "win", 0) or 0)
        loss = int(getattr(trader_obj, "loss", 0) or 0)
        total = win + loss
        wr = (win / total * 100.0) if total else 0.0
        consec = int(getattr(trader_obj, "consec_losses", 0) or 0)
        day = float(getattr(trader_obj, "day_profit", 0.0) or 0.0)
        return total, wr, consec, day
    except Exception:
        return 0, 0.0, 0, 0.0


def should_block_entry(trader_obj: Any) -> Tuple[bool, str]:
    if not _env_bool("OPS_SAFETY_ON", True):
        return False, "OPS_SAFETY_OFF"

    total, wr, consec, day = observed_stats(trader_obj)

    max_daily_loss = _env_float("OPS_MAX_DAILY_LOSS_USDT", 0.0)
    if max_daily_loss > 0 and day <= -abs(max_daily_loss):
        return True, f"OPS_DAILY_LOSS_FUSE day={day:.2f} <= -{abs(max_daily_loss):.2f}"

    max_consec = _env_int("OPS_MAX_CONSEC_LOSSES", 0)
    if max_consec > 0 and consec >= max_consec:
        return True, f"OPS_CONSEC_LOSS_FUSE {consec}>={max_consec}"

    block_after_loss_sec = _env_int("OPS_NO_TRADE_AFTER_LOSS_SEC", 0)
    if block_after_loss_sec > 0:
        last_loss_ts = float(getattr(trader_obj, "_ops_last_loss_ts", 0.0) or 0.0)
        left = int(last_loss_ts + block_after_loss_sec - time.time())
        if left > 0:
            return True, f"OPS_AFTER_LOSS_COOLDOWN left={left}s"

    if _env_bool("OPS_BLOCK_RISKY_UNTIL_PROVEN", True):
        on = [k for k, v in risky_flags_on().items() if v]
        if on:
            min_trades = _env_int("OPS_MIN_TRADES_FOR_RISKY", 50)
            min_wr = _env_float("OPS_MIN_WR_FOR_RISKY", 60.0)
            if total < min_trades:
                return True, f"OPS_RISKY_BLOCK sample={total}<{min_trades} risky={','.join(on)}"
            if wr < min_wr:
                return True, f"OPS_RISKY_BLOCK wr={wr:.1f}<{min_wr:.1f} risky={','.join(on)}"

    return False, "OPS_PASS"


def cap_mode_params(mp: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(mp or {})
    lev_cap = _env_float("OPS_LEVERAGE_CAP", 0.0)
    usdt_cap = _env_float("OPS_ORDER_USDT_CAP", 0.0)
    try:
        if lev_cap > 0 and float(out.get("lev", 0) or 0) > lev_cap:
            out["lev"] = lev_cap
            out["ops_lev_capped"] = True
    except Exception:
        pass
    try:
        if usdt_cap > 0 and float(out.get("order_usdt", 0) or 0) > usdt_cap:
            out["order_usdt"] = usdt_cap
            out["ops_order_capped"] = True
    except Exception:
        pass
    return out


def compact_status(trader_obj: Any) -> str:
    total, wr, consec, day = observed_stats(trader_obj)
    blocked, msg = should_block_entry(trader_obj)
    flags = [k for k, v in risky_flags_on().items() if v]
    lines = [
        "🛡️ OPS SAFETY",
        f"on={_env_bool('OPS_SAFETY_ON', True)} block_now={blocked}",
        f"reason={msg}",
        f"trades={total} wr={wr:.1f}% consec_losses={consec} day≈{day:.2f}",
        f"lev_cap={_env_float('OPS_LEVERAGE_CAP', 0.0)} order_cap={_env_float('OPS_ORDER_USDT_CAP', 0.0)}",
        "risky_on=" + (", ".join(flags) if flags else "none"),
    ]
    return "\n".join(lines)
