"""freqstyle_next_patch_v1.py

Next-stage research patch:
- Opposite side re-check: do not miss SHORT just because blocked LONG had higher score.
- Fee-aware target optimizer: if a PASS signal is blocked only by fee/RR, try adjusted TP/SL in DRY_RUN.
- Extra DB commands: /blocks, /signals, /quality.
"""

from __future__ import annotations

import os

try:
    import opposite_recheck_patch_v1  # noqa: F401
except Exception as e:
    print(f"[NEXT_PATCH] opposite recheck skipped: {e}", flush=True)

try:
    import fee_target_optimizer_v1  # noqa: F401
except Exception as e:
    print(f"[NEXT_PATCH] target optimizer skipped: {e}", flush=True)

try:
    import research_tools_v2 as tools
except Exception as e:
    print(f"[NEXT_PATCH] research tools skipped: {e}", flush=True)
    tools = None  # type: ignore

try:
    from trader import Trader
except Exception as e:
    print(f"[NEXT_PATCH] Trader import failed: {e}", flush=True)
    Trader = None  # type: ignore


def _notify(self, msg: str) -> None:
    try:
        if hasattr(self, "notify"):
            self.notify(msg)
        elif hasattr(self, "notify_throttled"):
            self.notify_throttled(msg, 60)
        else:
            print(msg, flush=True)
    except Exception:
        try:
            print(msg, flush=True)
        except Exception:
            pass


def _hours_arg(parts: list[str], default: int = 24) -> int:
    if len(parts) >= 2:
        try:
            return max(1, min(24 * 90, int(float(parts[1]))))
        except Exception:
            return default
    return default


if Trader is not None and tools is not None:
    _orig_handle_command = getattr(Trader, "handle_command", None)
    if callable(_orig_handle_command):
        def _patched_handle_command(self, text: str):
            parts = (text or "").strip().split()
            c0 = parts[0].lower() if parts else ""
            if c0 in ("/blocks", "/block", "/차단"):
                _notify(self, tools.blocks(_hours_arg(parts, 24)))
                return
            if c0 in ("/signals", "/signalstats", "/신호"):
                _notify(self, tools.signals(_hours_arg(parts, 24)))
                return
            if c0 in ("/quality", "/sample", "/샘플"):
                _notify(self, tools.quality(_hours_arg(parts, 24)))
                return
            return _orig_handle_command(self, text)
        Trader.handle_command = _patched_handle_command
    print("[NEXT_PATCH] loaded: opposite recheck + target optimizer + /blocks /signals /quality", flush=True)
else:
    print("[NEXT_PATCH] disabled", flush=True)
