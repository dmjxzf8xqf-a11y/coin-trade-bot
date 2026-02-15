# ===== file: ai_learn.py =====
import json
import os
from datetime import datetime

# -------------------------
# 1) enter_score 자동 튜닝(너가 올린 기존 로직)
# -------------------------
FILE = "learn_state.json"

def load_state():
    if not os.path.exists(FILE):
        return {"wins": 0, "losses": 0, "enter_score": 60}
    with open(FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def update_result(win: bool):
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


# -------------------------
# 2) AI 성능 트래커 (승률/트레이드수 저장)
# -------------------------
STATS_FILE = "ai_stats.json"

def _load_stats():
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "wins": 0,
            "losses": 0,
            "trades": 0,
            "winrate": 0,
            "last_update": None,
        }

def _save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

def record_trade_result(pnl: float):
    stats = _load_stats()
    stats["trades"] += 1

    if pnl > 0:
        stats["wins"] += 1
    else:
        stats["losses"] += 1

    stats["winrate"] = round(stats["wins"] / max(1, stats["trades"]) * 100, 2)
    stats["last_update"] = datetime.utcnow().isoformat()
    _save_stats(stats)

def get_ai_stats():
    return _load_stats()

_last_notified_winrate = 0

def check_winrate_milestone():
    global _last_notified_winrate
    stats = get_ai_stats()
    wr = float(stats.get("winrate") or 0)

    # 5% 단위 상승 알림(승 20 이상일 때만)
    if wr >= _last_notified_winrate + 5 and int(stats.get("wins") or 0) >= 20:
        _last_notified_winrate = wr
        return f"🤖 AI 진화 감지\n승률 상승 → {wr}%"
    return None
