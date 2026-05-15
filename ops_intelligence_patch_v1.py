"""ops_intelligence_patch_v1.py

Final operations/intelligence patch:
- /selftest, /audit, /ops, /journal, /explain commands
- risk fuse before entries via _score_symbol
- leverage/order size caps via _mp
- after-loss cooldown tracking

This patch is intentionally conservative. It should be imported after the
winrate patch so it can sit as the last safety layer.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from trader import Trader
except Exception as _e:  # pragma: no cover
    print(f"[OPS_PATCH] boot failed: {_e}", flush=True)
    Trader = None  # type: ignore

try:
    import ops_reality_check_v1 as reality
    import ops_safety_overlay_v1 as safety
except Exception as _e:  # pragma: no cover
    print(f"[OPS_PATCH] dependency import failed: {_e}", flush=True)
    reality = None  # type: ignore
    safety = None  # type: ignore


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _notify(self: Any, msg: str) -> None:
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


OPS_PATCH_ON = _env_bool("OPS_PATCH_ON", True)

if Trader is not None and OPS_PATCH_ON and reality is not None and safety is not None:
    # 1) Cap leverage/order_usdt returned by mode params without editing trader.py.
    _orig_mp = getattr(Trader, "_mp", None)
    if callable(_orig_mp):
        def _ops_mp(self):
            mp = _orig_mp(self)
            try:
                capped = safety.cap_mode_params(mp)
                if isinstance(capped, dict) and (capped.get("ops_lev_capped") or capped.get("ops_order_capped")):
                    self.state["ops_caps"] = {
                        "lev": capped.get("lev"),
                        "order_usdt": capped.get("order_usdt"),
                        "lev_capped": bool(capped.get("ops_lev_capped")),
                        "order_capped": bool(capped.get("ops_order_capped")),
                    }
                return capped
            except Exception as e:
                try:
                    self.state["ops_cap_error"] = str(e)
                except Exception:
                    pass
                return mp
        Trader._mp = _ops_mp

    # 2) Global pre-entry fuse at scoring layer. Safer than blocking inside _enter.
    _orig_score = getattr(Trader, "_score_symbol", None)
    if callable(_orig_score):
        def _ops_score_symbol(self, symbol: str, price: float):
            try:
                blocked, msg = safety.should_block_entry(self)
                self.state["ops_safety"] = {"blocked": blocked, "reason": msg}
                if blocked:
                    return {"ok": False, "reason": msg, "strategy": "ops_safety"}
            except Exception as e:
                try:
                    self.state["ops_safety_error"] = str(e)
                except Exception:
                    pass
            return _orig_score(self, symbol, price)
        Trader._score_symbol = _ops_score_symbol

    # 3) Track losing exits for after-loss cooldown.
    _orig_exit = getattr(Trader, "_exit_position", None)
    if callable(_orig_exit):
        def _ops_exit_position(self, idx: int, why: str, force: bool = False):
            before = 0.0
            try:
                before = float(getattr(self, "day_profit", 0.0) or 0.0)
            except Exception:
                before = 0.0
            ret = _orig_exit(self, idx, why, force=force)
            try:
                after = float(getattr(self, "day_profit", before) or before)
                if after - before < 0:
                    import time
                    self._ops_last_loss_ts = time.time()
                    self.state["ops_last_loss_ts"] = self._ops_last_loss_ts
            except Exception:
                pass
            return ret
        Trader._exit_position = _ops_exit_position

    # 4) Telegram commands.
    _orig_handle = getattr(Trader, "handle_command", None)
    if callable(_orig_handle):
        def _ops_handle_command(self, text: str):
            parts = (text or "").strip().split(maxsplit=1)
            c0 = parts[0].lower() if parts else ""
            arg = parts[1] if len(parts) > 1 else ""

            if c0 in ("/selftest", "/audit", "/점검"):
                _notify(self, reality.format_audit(self))
                return
            if c0 in ("/ops", "/risk", "/안전"):
                _notify(self, safety.compact_status(self))
                return
            if c0 in ("/journal", "/decisions", "/판단로그"):
                _notify(self, reality.format_decision_summary())
                return
            if c0 in ("/explain", "/whyblock", "/차단이유"):
                target = arg or str(self.state.get("last_event") or self.state.get("entry_reason") or "")
                _notify(self, "🧠 차단 사유 설명\n" + reality.explain_block(target))
                return
            if c0 in ("/safeenv", "/추천env"):
                _notify(self,
                    "🧷 안정 우선 추천 ENV\n"
                    "AI_AUTO_LEVERAGE=false\n"
                    "DL_LITE_ON=false\n"
                    "DCA_ON=false\n"
                    "EXPERIMENTAL_MULTI_POS_ON=false\n"
                    "EXPERIMENTAL_SCALP_MODE_ON=false\n"
                    "MAX_POSITIONS=1\n"
                    "DIVERSIFY=false\n"
                    "ALLOW_SHORT=false\n"
                    "OPS_SAFETY_ON=true\n"
                    "OPS_LEVERAGE_CAP=8\n"
                    "OPS_ORDER_USDT_CAP=30\n"
                    "OPS_BLOCK_RISKY_UNTIL_PROVEN=true"
                )
                return
            return _orig_handle(self, text)
        Trader.handle_command = _ops_handle_command

    # 5) Public state hint.
    _orig_public_state = getattr(Trader, "public_state", None)
    if callable(_orig_public_state):
        def _ops_public_state(self):
            ps = _orig_public_state(self)
            try:
                if isinstance(ps, dict):
                    ps["ops_patch"] = {
                        "on": True,
                        "safety": self.state.get("ops_safety"),
                        "caps": self.state.get("ops_caps"),
                        "last_loss_ts": self.state.get("ops_last_loss_ts"),
                    }
            except Exception:
                pass
            return ps
        Trader.public_state = _ops_public_state

    print("[OPS_PATCH] loaded: selftest/journal + risk fuse + caps", flush=True)
else:
    print("[OPS_PATCH] disabled", flush=True)
