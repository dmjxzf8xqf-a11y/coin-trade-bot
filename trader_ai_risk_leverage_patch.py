#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
from pathlib import Path

BEST_CONFIG_PATH = Path("best_config.json")
RISK_LIMITS_PATH = Path("risk_limits.json")

DEFAULT_BEST = {
    "selected": {
        "mode": "off",
        "symbol": "NONE",
        "enter_score": 70,
        "sl_atr": 1.5,
        "tp_atr": 1.0,
        "time_exit_bars": 0,
        "reason": "default_off",
    }
}

DEFAULT_RISK = {
    "daily_loss_stop_pct": -3.0,
    "max_consec_losses": 3,
    "cooldown_minutes_after_loss": 30,
    "resume_min_wins_last5": 3,
    "resume_need_trend_recovery": True,
    "regime_risk": {
        "bull":    {"risk_pct": 0.03,  "leverage": 4},
        "neutral": {"risk_pct": 0.015, "leverage": 2},
        "bear":    {"risk_pct": 0.008, "leverage": 1},
        "range":   {"risk_pct": 0.0,   "leverage": 1},
        "unknown": {"risk_pct": 0.01,  "leverage": 1},
    },
    "mode_risk_multipliers": {
        "long": 1.0,
        "short": 0.8,
        "off": 0.0
    }
}


def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _sf(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _si(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def detect_regime_from_state(state: dict) -> str:
    """
    state 기반 시장 상태 추정
    반환: bull / bear / neutral / range / unknown
    """
    if not isinstance(state, dict):
        return "unknown"

    last_skip = str(state.get("last_skip_reason", "") or "").lower()
    raw_regime = str(state.get("regime", "") or state.get("last_regime", "") or "").lower()

    slope_bps = _sf(state.get("slope_bps", state.get("slope", 0.0)), 0.0)
    adx = _sf(state.get("adx", 0.0), 0.0)

    if "range" in last_skip or "side" in last_skip or "chop" in last_skip:
        return "range"

    if "range" in raw_regime or "side" in raw_regime or "chop" in raw_regime:
        return "range"

    if adx > 0 and adx < 18:
        return "range"

    if slope_bps >= 6.0:
        return "bull"
    if slope_bps <= -6.0:
        return "bear"

    if "bull" in raw_regime or "up" in raw_regime or "trend_up" in raw_regime:
        return "bull"
    if "bear" in raw_regime or "down" in raw_regime or "trend_down" in raw_regime:
        return "bear"

    if adx >= 18:
        return "neutral"

    return "unknown"


def apply_trader_ai_risk_leverage_patch(Trader):
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
        self.state.setdefault("ai_regime", "unknown")

        self.state.setdefault("daily_pnl_pct", 0.0)
        self.state.setdefault("consec_losses", 0)
        self.state.setdefault("recent_trade_results", [])
        self.state.setdefault("risk_paused_until", 0.0)
        self.state.setdefault("risk_active", True)

        self.state.setdefault("risk_pct", 0.0)
        self.state.setdefault("ai_leverage", 1)

    def _append_recent_trade_result(self, pnl_pct: float):
        arr = self.state.get("recent_trade_results", [])
        if not isinstance(arr, list):
            arr = []
        arr.append(float(pnl_pct))
        arr = arr[-5:]
        self.state["recent_trade_results"] = arr

    def _after_close_update_risk_ai(self, pnl_pct: float):
        risk = _load_json(RISK_LIMITS_PATH, DEFAULT_RISK)
        now = time.time()

        if not hasattr(self, "state") or not isinstance(self.state, dict):
            self.state = {}

        self._append_recent_trade_result(pnl_pct)

        self.state["daily_pnl_pct"] = _sf(self.state.get("daily_pnl_pct", 0.0), 0.0) + float(pnl_pct)

        if pnl_pct > 0:
            self.state["consec_losses"] = 0
            self.state["ai_reason"] = "win_reset"
            return

        self.state["consec_losses"] = _si(self.state.get("consec_losses", 0), 0) + 1

        if self.state["consec_losses"] >= int(risk["max_consec_losses"]):
            mins = int(risk["cooldown_minutes_after_loss"])
            self.state["risk_paused_until"] = now + mins * 60
            self.state["ai_reason"] = f"loss_pause:{mins}m"

    def _apply_best_config_ai(self):
        data = _load_json(BEST_CONFIG_PATH, DEFAULT_BEST)
        selected = dict(data.get("selected") or {})

        mode = str(selected.get("mode", "off")).lower()
        symbol = str(selected.get("symbol", "NONE")).upper()

        self.state["ai_mode"] = mode
        self.state["ai_symbol"] = symbol
        self.state["ai_reason"] = selected.get("reason", "best_config")

        if hasattr(self, "trading_enabled"):
            self.trading_enabled = mode != "off"

        self.state["fixed_symbol"] = symbol
        self.state["auto_symbol"] = False

        mapping = {
            "enter_score": selected.get("enter_score"),
            "sl_atr": selected.get("sl_atr"),
            "tp_atr": selected.get("tp_atr"),
            "time_exit_bars": selected.get("time_exit_bars"),
        }

        attr_candidates = {
            "enter_score": ["enter_score", "ENTER_SCORE"],
            "sl_atr": ["sl_atr", "STOP_ATR", "stop_atr"],
            "tp_atr": ["tp_atr", "TP_R", "tp_r"],
            "time_exit_bars": ["time_exit_bars", "TIME_EXIT_BARS"],
        }

        for k, v in mapping.items():
            if v is None:
                continue
            for attr in attr_candidates[k]:
                if hasattr(self, attr):
                    try:
                        setattr(self, attr, v)
                    except Exception:
                        pass

    def _apply_dynamic_risk_and_leverage(self):
        risk = _load_json(RISK_LIMITS_PATH, DEFAULT_RISK)
        regime = detect_regime_from_state(self.state)
        self.state["ai_regime"] = regime

        mode = str(self.state.get("ai_mode", "off")).lower()
        base_cfg = risk["regime_risk"].get(regime, risk["regime_risk"]["unknown"])
        mult = _sf(risk["mode_risk_multipliers"].get(mode, 1.0), 1.0)

        risk_pct = round(_sf(base_cfg.get("risk_pct", 0.0), 0.0) * mult, 6)
        leverage = max(1, _si(base_cfg.get("leverage", 1), 1))

        self.state["risk_pct"] = risk_pct
        self.state["ai_leverage"] = leverage

        symbol = self.state.get("ai_symbol", "NONE")
        if symbol and symbol != "NONE":
            try:
                if hasattr(self, "_ensure_leverage"):
                    self._ensure_leverage(symbol, leverage)
            except Exception as e:
                self.state["ai_reason"] = f"lev_set_fail:{e}"

    def _recovery_ready(self):
        risk = _load_json(RISK_LIMITS_PATH, DEFAULT_RISK)
        now = time.time()

        paused_until = _sf(self.state.get("risk_paused_until", 0.0), 0.0)
        cooldown_ok = now >= paused_until

        regime = detect_regime_from_state(self.state)
        trend_ok = True
        if risk.get("resume_need_trend_recovery", True):
            trend_ok = regime in ("bull", "neutral")

        recent = self.state.get("recent_trade_results", [])
        if not isinstance(recent, list):
            recent = []
        wins = sum(1 for x in recent[-5:] if _sf(x, 0.0) > 0)
        perf_ok = wins >= int(risk.get("resume_min_wins_last5", 3))

        self.state["recovery_cooldown_ok"] = cooldown_ok
        self.state["recovery_trend_ok"] = trend_ok
        self.state["recovery_perf_ok"] = perf_ok

        return cooldown_ok and trend_ok and perf_ok

    def _risk_guard_ai(self):
        risk = _load_json(RISK_LIMITS_PATH, DEFAULT_RISK)

        daily_pnl_pct = _sf(self.state.get("daily_pnl_pct", 0.0), 0.0)
        if daily_pnl_pct <= _sf(risk["daily_loss_stop_pct"], -3.0):
            self.state["ai_reason"] = f"daily_stop:{daily_pnl_pct}"
            if hasattr(self, "trading_enabled"):
                self.trading_enabled = False
            self.state["risk_active"] = False
            return False

        paused_until = _sf(self.state.get("risk_paused_until", 0.0), 0.0)
        if paused_until > 0:
            if not self._recovery_ready():
                self.state["risk_active"] = False
                self.state["ai_reason"] = "recovery_wait"
                return False

            self.state["risk_paused_until"] = 0.0
            self.state["consec_losses"] = 0
            self.state["risk_active"] = True
            self.state["ai_reason"] = "recovered"

        self.state["risk_active"] = True
        return True

    def tick_patched(self, *args, **kwargs):
        try:
            self._apply_best_config_ai()
            self._apply_dynamic_risk_and_leverage()

            if not self._risk_guard_ai():
                return

            for attr in ["risk_pct", "RISK_PCT", "position_risk_pct"]:
                if hasattr(self, attr):
                    try:
                        setattr(self, attr, self.state.get("risk_pct", 0.0))
                    except Exception:
                        pass

        except Exception as e:
            try:
                self.state["ai_reason"] = f"ai_patch_err:{e}"
            except Exception:
                pass

        return orig_tick(self, *args, **kwargs)

    def status_text_patched(self, *args, **kwargs):
        base = orig_status_text(self, *args, **kwargs) if callable(orig_status_text) else ""
        extra = [
            f"🤖 ai_mode={self.state.get('ai_mode', '-')}",
            f"🎯 ai_symbol={self.state.get('ai_symbol', '-')}",
            f"🧠 ai_reason={self.state.get('ai_reason', '-')}",
            f"🌊 ai_regime={self.state.get('ai_regime', '-')}",
            f"📉 consec_losses={self.state.get('consec_losses', '-')}",
            f"💸 daily_pnl_pct={self.state.get('daily_pnl_pct', '-')}",
            f"🛡️ risk_pct={self.state.get('risk_pct', '-')}",
            f"⚙️ ai_leverage={self.state.get('ai_leverage', '-')}",
            f"🔄 recovery_ok={self.state.get('recovery_cooldown_ok', '-')}/{self.state.get('recovery_trend_ok', '-')}/{self.state.get('recovery_perf_ok', '-')}",
        ]
        if base:
            return base + "\n" + "\n".join(extra)
        return "\n".join(extra)

    Trader.__init__ = __init__patched
    if callable(orig_tick):
        Trader._append_recent_trade_result = _append_recent_trade_result
        Trader._after_close_update_risk_ai = _after_close_update_risk_ai
        Trader._apply_best_config_ai = _apply_best_config_ai
        Trader._apply_dynamic_risk_and_leverage = _apply_dynamic_risk_and_leverage
        Trader._recovery_ready = _recovery_ready
        Trader._risk_guard_ai = _risk_guard_ai
        Trader.tick = tick_patched
    Trader.status_text = status_text_patched
