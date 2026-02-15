# ai_learn.py
import json
import os
from datetime import datetime

# =========================
# FILE PATH (Railway 재시작 대비)
# =========================
STATS_FILE = os.getenv("AI_STATS_PATH", "ai_stats.json")

# =========================
# 내부 로드/세이브
# =========================
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

# =========================
# 트레이드 결과 기록
# =========================
def record_trade_result(pnl):
    """
    pnl > 0 → win
    pnl <= 0 → loss
    """
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

# =========================
# 현재 AI 성능 반환
# =========================
def get_ai_stats():
    return _load_stats()

# =========================
# 승률 진화 알림
# =========================
_last_notified_winrate = 0

def check_winrate_milestone():
    global _last_notified_winrate
    stats = get_ai_stats()

    wr = stats["winrate"]

    if wr >= _last_notified_winrate + 5 and stats["wins"] >= 20:
        _last_notified_winrate = wr
        return f"🤖 AI 진화 감지\n승률 상승 → {wr}%"

    return None
