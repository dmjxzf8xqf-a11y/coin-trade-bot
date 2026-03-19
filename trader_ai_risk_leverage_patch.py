# trader_ai_risk_leverage_patch.py
# FINAL SAFE VERSION

def apply_trader_ai_risk_leverage_patch(Trader):
    orig_tick = getattr(Trader, "tick", None)

    def patched_tick(self, *args, **kwargs):
        try:
            state = getattr(self, "state", {})

            if state.get("ai_confidence", 0) == 0:
                state["ai_confidence"] = 0.7

            if state.get("ai_regime") in (None, "unknown"):
                state["ai_regime"] = "trend"

            conf = float(state.get("ai_confidence", 0))

            if conf >= 0.8:
                lev = 12
                risk = 0.03
            elif conf >= 0.6:
                lev = 8
                risk = 0.02
            else:
                lev = 5
                risk = 0.01

            state["ai_leverage"] = lev
            state["risk_pct"] = risk

            if state.get("ai_symbol") in (None, "unknown"):
                state["ai_symbol"] = "ETHUSDT"
                state["ai_mode"] = "long"

            self.state = state

        except Exception as e:
            print("AI PATCH ERROR:", e)

        return orig_tick(self, *args, **kwargs)

    Trader.tick = patched_tick
