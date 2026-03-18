#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
from pathlib import Path

BEST_CONFIG_PATH = Path("best_config.json")
RISK_LIMITS_PATH = Path("risk_limits.json")

DEFAULT_RISK = {
    "daily_loss_stop_pct": -3.0,
    "max_consec_losses": 3,
    "cooldown_minutes_after_loss": 30,
    "force_off_if_no_config": True
}

def _load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def apply_trader_ai_runtime_patch(Trader):
    orig_init = getattr(Trader, "__init__", None)
    orig_tick = getattr(Trader, "tick", None)
    orig_status_text = getattr(Trader, "status_text", None)

    def __init__patched(self, *args, **kwargs):
        if callable(orig_init):
            orig_init(self, *args, **kwargs)
        if not hasattr(self, "state") or not isinstance(self.state, dict):
            self.state = {}
        self.state.setdefault("ai_mode", "unknown")
        self.state.setdefault("ai_symbol", "unknown")
        self.state.setdefault("ai_reason", "-")
        self.state.setdefault("consec_losses", 0)
        self.state.setdefault("daily_pnl_pct", 0.0)
        self.state.setdefault("risk_paused_until", 0.0)

    def _apply_best_config(self):
        data = _load_json(BEST_CONFIG_PATH, {})
        selected = data.get("selected", {})
        mode = str(selected.get("mode", "off")).lower()
        symbol = str(selected.get("symbol", "NONE")).upper()
        self.state["ai_mode"] = mode
        self.state["ai_symbol"] = symbol
        self.state["ai_reason"] = selected.get("reason", "best_config")

        if hasattr(self, "trading_enabled"):
            self.trading_enabled = mode != "off"

        if hasattr(self, "state") and isinstance(self.state, dict):
            self.state["fixed_symbol"] = symbol
            self.state["auto_symbol"] = False

        for key, val in {
            "enter_score": selected.get("enter_score"),
            "sl_atr": selected.get("sl_atr"),
            "tp_atr": selected.get("tp_atr"),
            "time_exit_bars": selected.get("time_exit_bars"),
        }.items():
            if val is None:
                continue
            for attr in [key, key.upper()]:
                if hasattr(self, attr):
                    try:
                        setattr(self, attr, val)
                    except Exception:
                        pass

    def _risk_guard(self):
        risk = _load_json(RISK_LIMITS_PATH, DEFAULT_RISK)
        now = time.time()

        paused_until = float(self.state.get("risk_paused_until", 0.0) or 0.0)
        if now < paused_until:
            self.state["ai_reason"] = f"risk_cooldown:{int(paused_until-now)}s"
            return False

        daily_pnl_pct = float(self.state.get("daily_pnl_pct", 0.0) or 0.0)
        if daily_pnl_pct <= float(risk["daily_loss_stop_pct"]):
            self.state["ai_reason"] = f"daily_loss_stop:{daily_pnl_pct}"
            if hasattr(self, "trading_enabled"):
                self.trading_enabled = False
            return False

        consec = int(self.state.get("consec_losses", 0) or 0)
        if consec >= int(risk["max_consec_losses"]):
            mins = int(risk["cooldown_minutes_after_loss"])
            self.state["risk_paused_until"] = now + mins * 60
            self.state["ai_reason"] = f"consec_loss_pause:{mins}m"
            return False

        return True

    def tick_patched(self, *args, **kwargs):
        try:
            self._apply_best_config()
            if not self._risk_guard():
                return
        except Exception as e:
            try:
                self.state["ai_reason"] = f"patch_err:{e}"
            except Exception:
                pass
        return orig_tick(self, *args, **kwargs)

    def status_text_patched(self, *args, **kwargs):
        base = orig_status_text(self, *args, **kwargs) if callable(orig_status_text) else ""
        extra = [
            f"ð¤ ai_mode={self.state.get('ai_mode', '-')}",
            f"ð¯ ai_symbol={self.state.get('ai_symbol', '-')}",
            f"ð§  ai_reason={self.state.get('ai_reason', '-')}",
            f"ð consec_losses={self.state.get('consec_losses', '-')}",
            f"ð¸ daily_pnl_pct={self.state.get('daily_pnl_pct', '-')}",
        ]
        if base:
            return base + "\n" + "\n".join(extra)
        return "\n".join(extra)

    Trader.__init__ = __init__patched
    if callable(orig_tick):
        Trader._apply_best_config = _apply_best_config
        Trader._risk_guard = _risk_guard
        Trader.tick = tick_patched
    Trader.status_text = status_text_patched
