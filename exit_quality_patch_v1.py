"""exit_quality_patch_v1.py

Improves SCORE DROP exits without rewriting trader.py.

The original bot may close immediately when score <= EXIT_SCORE_DROP. In noisy
low-volatility markets this can create many tiny DRY_RUN losses before the trade
has time to reach SL/TP. This patch makes SCORE DROP exits require:
- minimum hold time
- consecutive low-score confirmations
- optional suppression while the position is still near flat/loss in DRY_RUN

All controls are env-based.
"""

from __future__ import annotations

import os
import time
from typing import Any

try:
    import trader as _t
    from trader import Trader
except Exception as e:  # pragma: no cover
    print(f"[EXIT_QUALITY] boot failed: {e}", flush=True)
    _t = None
    Trader = None  # type: ignore


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


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


def _est_unrealized_pct(pos: dict[str, Any], price: float) -> float:
    entry = _safe_float(pos.get("entry_price"), 0.0)
    if entry <= 0 or price <= 0:
        return 0.0
    side = str(pos.get("side") or "LONG").upper()
    raw = (float(price) - entry) / entry
    return raw if side == "LONG" else -raw


EXIT_QUALITY_ON = _env_bool("EXIT_QUALITY_ON", True)

if _t is not None and Trader is not None and EXIT_QUALITY_ON:
    _orig_manage_one = getattr(Trader, "_manage_one", None)
    if callable(_orig_manage_one):
        def _patched_manage_one(self, idx: int):
            try:
                pos = self.positions[idx]
            except Exception:
                return _orig_manage_one(self, idx)

            try:
                symbol = str(pos.get("symbol") or "")
                side = str(pos.get("side") or "")
                price = float(_t.get_price(symbol))
                mp = self._mp() if hasattr(self, "_mp") else {}
                ok, reason, score, sl_new, tp_new, a = _t.compute_signal_and_exits(
                    symbol,
                    side,
                    price,
                    mp,
                    avoid_low_rsi=bool(getattr(self, "state", {}).get("avoid_low_rsi", False)),
                )
                exit_score_drop = _safe_float(getattr(_t, "EXIT_SCORE_DROP", _env_int("EXIT_SCORE_DROP", 10)), 10.0)
                hold_sec = time.time() - _safe_float(pos.get("entry_ts"), time.time())
                min_hold = _env_int("SCORE_DROP_MIN_HOLD_SEC", 180)
                confirm = max(1, _env_int("SCORE_DROP_CONFIRM_TICKS", 2))
                dry_ignore_loss = _env_bool("DRY_RUN_SCORE_DROP_IGNORE_WHILE_LOSS", True)
                dry_run = bool(getattr(_t, "DRY_RUN", False)) or _env_bool("DRY_RUN", False)
                pnl_pct = _est_unrealized_pct(pos, price)

                low = float(score or 0.0) <= exit_score_drop
                if low:
                    pos["score_drop_count"] = int(pos.get("score_drop_count", 0) or 0) + 1
                else:
                    pos["score_drop_count"] = 0

                suppress = False
                why = []
                if low and hold_sec < min_hold:
                    suppress = True
                    why.append(f"hold {hold_sec:.0f}<{min_hold}s")
                if low and int(pos.get("score_drop_count", 0) or 0) < confirm:
                    suppress = True
                    why.append(f"confirm {pos.get('score_drop_count')}/{confirm}")
                if low and dry_run and dry_ignore_loss and pnl_pct <= 0:
                    # For research, let SL/TP/time decide instead of instant score cut.
                    suppress = True
                    why.append(f"dry_loss {pnl_pct:.3%}")

                if suppress:
                    old = getattr(_t, "EXIT_SCORE_DROP", None)
                    try:
                        setattr(_t, "EXIT_SCORE_DROP", -10**9)
                        if isinstance(getattr(self, "state", None), dict):
                            self.state["exit_quality"] = {
                                "symbol": symbol,
                                "side": side,
                                "score": float(score or 0.0),
                                "suppressed": True,
                                "why": ", ".join(why),
                                "hold_sec": hold_sec,
                                "pnl_pct": pnl_pct,
                            }
                        return _orig_manage_one(self, idx)
                    finally:
                        if old is not None:
                            setattr(_t, "EXIT_SCORE_DROP", old)
            except Exception as e:
                try:
                    if isinstance(getattr(self, "state", None), dict):
                        self.state["exit_quality_error"] = str(e)
                except Exception:
                    pass
            return _orig_manage_one(self, idx)

        Trader._manage_one = _patched_manage_one
        print("[EXIT_QUALITY] loaded: score-drop hold/confirm guard", flush=True)
    else:
        print("[EXIT_QUALITY] disabled: _manage_one not found", flush=True)
else:
    print("[EXIT_QUALITY] disabled", flush=True)
