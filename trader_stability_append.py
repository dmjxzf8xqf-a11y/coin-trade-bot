#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trader_stability_append.py

한 파일만 GitHub에 추가해서 쓰는 안정화 패치.
기존 trader.py 전체를 덮어쓰지 않고, Trader 클래스에 기능을 주입한다.

필수 적용:
trader.py 맨 아래 2줄만 추가
from trader_stability_append import apply_trader_stability_patch
apply_trader_stability_patch(Trader)

선택 적용:
손익 확정 직후에 아래 1줄을 넣으면 쿨다운 정확도가 올라간다.
self._after_close_update_risk(pnl_pct)
"""

import time
from typing import Any


def apply_trader_stability_patch(Trader: Any) -> None:
    def _set_skip(self, reason: str):
        try:
            if not hasattr(self, "state") or not isinstance(self.state, dict):
                self.state = {}
            self.state["last_skip_reason"] = str(reason)
        except Exception:
            pass

    def _detect_regime_safe(self, symbol: str) -> str:
        try:
            if hasattr(self, "market_regime") and callable(getattr(self, "market_regime")):
                return str(self.market_regime(symbol)).lower()
            if hasattr(self, "_detect_regime") and callable(getattr(self, "_detect_regime")):
                return str(self._detect_regime(symbol)).lower()
            try:
                from market_regime import detect_regime
                return str(detect_regime(symbol)).lower()
            except Exception:
                pass
        except Exception:
            pass
        return "unknown"

    def _after_close_update_risk(self, pnl_pct: float):
        now = time.time()
        try:
            if not hasattr(self, "state") or not isinstance(self.state, dict):
                self.state = {}
        except Exception:
            self.state = {}

        if not hasattr(self, "consec_losses"):
            self.consec_losses = 0
        if not hasattr(self, "cooldown_until_ts"):
            self.cooldown_until_ts = 0

        if pnl_pct > 0:
            self.consec_losses = 0
            self.cooldown_until_ts = 0
            self.state["last_risk_event"] = "win_reset"
            return

        self.consec_losses += 1
        if self.consec_losses == 1:
            cd = 15 * 60
        elif self.consec_losses == 2:
            cd = 60 * 60
        else:
            cd = 4 * 60 * 60

        self.cooldown_until_ts = now + cd
        self.state["last_risk_event"] = f"loss_cooldown:{cd}s"

    def _can_trade_now_stability(self, symbol: str):
        now_ts = time.time()

        if not hasattr(self, "state") or not isinstance(self.state, dict):
            self.state = {}
        if not hasattr(self, "consec_losses"):
            self.consec_losses = 0
        if not hasattr(self, "cooldown_until_ts"):
            self.cooldown_until_ts = 0

        if hasattr(self, "trading_enabled") and not self.trading_enabled:
            self._set_skip("trading_disabled")
            return False

        if now_ts < getattr(self, "cooldown_until_ts", 0):
            left = int(self.cooldown_until_ts - now_ts)
            self._set_skip(f"loss_cooldown_left:{left}s")
            return False

        regime = self._detect_regime_safe(symbol)
        try:
            self.state["last_regime"] = regime
        except Exception:
            pass

        if regime in ("range", "chop", "sideways"):
            self._set_skip(f"regime_block:{regime}")
            return False

        return True

    def _status_text_stability_suffix(self) -> str:
        try:
            last_skip = getattr(self, "state", {}).get("last_skip_reason", "-")
            last_regime = getattr(self, "state", {}).get("last_regime", "-")
            last_risk = getattr(self, "state", {}).get("last_risk_event", "-")
            cooldown_left = max(0, int(getattr(self, "cooldown_until_ts", 0) - time.time()))
            consec_losses = int(getattr(self, "consec_losses", 0))
            extra = [
                f"⏭️ last_skip={last_skip}",
                f"🌊 regime={last_regime}",
                f"🧯 risk={last_risk}",
                f"📉 consec_losses={consec_losses}",
                f"⏳ cooldown_left={cooldown_left}s",
            ]
            return "\n".join(extra)
        except Exception:
            return ""

    # attach methods
    Trader._set_skip = _set_skip
    Trader._detect_regime_safe = _detect_regime_safe
    Trader._after_close_update_risk = _after_close_update_risk
    Trader._can_trade_now_stability = _can_trade_now_stability
    Trader._status_text_stability_suffix = _status_text_stability_suffix

    # __init__ patch
    orig_init = getattr(Trader, "__init__", None)

    def __init__patched(self, *args, **kwargs):
        if callable(orig_init):
            orig_init(self, *args, **kwargs)
        if not hasattr(self, "state") or not isinstance(self.state, dict):
            self.state = {}
        if not hasattr(self, "consec_losses"):
            self.consec_losses = 0
        if not hasattr(self, "cooldown_until_ts"):
            self.cooldown_until_ts = 0

    Trader.__init__ = __init__patched

    # status_text patch
    orig_status_text = getattr(Trader, "status_text", None)
    if callable(orig_status_text):
        def status_text_patched(self, *args, **kwargs):
            base = orig_status_text(self, *args, **kwargs)
            try:
                suffix = self._status_text_stability_suffix()
                if suffix:
                    return f"{base}\n{suffix}"
            except Exception:
                pass
            return base
        Trader.status_text = status_text_patched

    # tick patch: run stability gate before original tick if symbol can be inferred
    orig_tick = getattr(Trader, "tick", None)
    if callable(orig_tick):
        def tick_patched(self, *args, **kwargs):
            try:
                symbol = None

                for attr in ("symbol", "fixed_symbol", "current_symbol", "selected_symbol"):
                    if hasattr(self, attr):
                        v = getattr(self, attr)
                        if isinstance(v, str) and v:
                            symbol = v
                            break

                if symbol is None:
                    try:
                        if hasattr(self, "_mp") and callable(getattr(self, "_mp")):
                            mp = self._mp()
                            if isinstance(mp, dict):
                                for key in ("symbol", "fixed_symbol", "selected_symbol"):
                                    v = mp.get(key)
                                    if isinstance(v, str) and v:
                                        symbol = v
                                        break
                    except Exception:
                        pass

                if symbol:
                    if not self._can_trade_now_stability(symbol):
                        return
            except Exception:
                pass

            return orig_tick(self, *args, **kwargs)

        Trader.tick = tick_patched

    Trader._stability_patch_loaded = True


if __name__ == "__main__":
    print("Import this from trader.py; do not run directly.")
