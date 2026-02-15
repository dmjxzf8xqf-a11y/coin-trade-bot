# ai_learn.py
import json
import os

FILE = "learn_state.json"


def load_state():
    if not os.path.exists(FILE):
        return {"wins": 0, "losses": 0, "enter_score": 60}
    with open(FILE, "r") as f:
        return json.load(f)


def save_state(state):
    with open(FILE, "w") as f:
        json.dump(state, f)


def update_result(win):
    state = load_state()

    if win:
        state["wins"] += 1
    else:
        state["losses"] += 1

    total = state["wins"] + state["losses"]

    if total >= 20:
        winrate = state["wins"] / total

        # 자동 튜닝
        if winrate < 0.50:
            state["enter_score"] += 2
        elif winrate > 0.65:
            state["enter_score"] -= 1

        state["enter_score"] = max(45, min(85, state["enter_score"]))

    save_state(state)
    return state["enter_score"]
# =========================
# AI PERFORMANCE TRACKER
# =========================

import json
from datetime import datetime

STATS_FILE = "ai_stats.json"


def _load_stats():
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except:
        return {
            "wins": 0,
            "losses": 0,
            "trades": 0,
            "winrate": 0,
            "last_update": None,
        }


def _save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def record_trade_result(pnl):
    stats = _load_stats()

    stats["trades"] += 1

    if pnl > 0:
        stats["wins"] += 1
    else:
        stats["losses"] += 1

    stats["winrate"] = round(
        stats["wins"] / max(1, stats["trades"]) * 100, 2
    )
    stats["last_update"] = datetime.utcnow().isoformat()

    _save_stats(stats)


def get_ai_stats():
    return _load_stats()
