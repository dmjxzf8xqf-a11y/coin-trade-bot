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
