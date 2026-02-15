# apply_to_trader_patch.py
import json
import os

LEARN_FILE = os.getenv("LEARN_FILE", "learn_state.json")

def load_learned_params():
    try:
        with open(LEARN_FILE, "r", encoding="utf-8") as f:
            j = json.load(f)
        return (j.get("last_best_params") or {})
    except Exception:
        return {}

def apply_to_trader(trader, target_mode="SAFE"):
    p = load_learned_params()
    if not p:
        return False, "no learned params"

    mode = str(target_mode).upper()
    if mode not in trader.tune:
        trader.tune[mode] = {}

    trader.tune[mode]["enter_score"] = int(p.get("enter_score", trader.tune[mode].get("enter_score", 65)))
    trader.tune[mode]["stop_atr"] = float(p.get("stop_atr", trader.tune[mode].get("stop_atr", 1.8)))
    trader.tune[mode]["tp_r"] = float(p.get("tp_r", trader.tune[mode].get("tp_r", 1.5)))

    try:
        trader._lev_set_cache = {}
    except Exception:
        pass

    return True, f"applied {mode}: enter_score={trader.tune[mode]['enter_score']} stop_atr={trader.tune[mode]['stop_atr']} tp_r={trader.tune[mode]['tp_r']}"
