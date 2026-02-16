# ===== file: ai_learn.py =====
import json
import os
from datetime import datetime, timezone

# -------------------------
# Storage (local fallback)
# -------------------------
DATA_DIR = os.getenv("DATA_DIR", "").strip() or "."
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = "."

LEARN_FILE = os.path.join(DATA_DIR, "learn_state.json")
STATS_FILE = os.path.join(DATA_DIR, "ai_stats.json")

def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

# -------------------------
# Supabase (primary if set)
# -------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

_sb = None
def _sb_client():
    global _sb
    if _sb is not None:
        return _sb
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        from supabase import create_client
        _sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        return _sb
    except Exception:
        return None

def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

# -------------------------
# 1) enter_score 자동 튜닝 (기존 로직 유지)
# -------------------------
def load_state():
    return _read_json(LEARN_FILE, {"wins": 0, "losses": 0, "enter_score": 60})

def save_state(state):
    _write_json(LEARN_FILE, state)

def _sb_get_stats_row():
    sb = _sb_client()
    if sb is None:
        return None
    try:
        # single row id=1
        return sb.table("ai_stats").select("wins,losses,trades,winrate,enter_score,last_update").eq("id", 1).single().execute().data
    except Exception:
        return None

def _sb_update_stats(payload: dict):
    sb = _sb_client()
    if sb is None:
        return False
    try:
        sb.table("ai_stats").update(payload).eq("id", 1).execute()
        return True
    except Exception:
        return False

def update_result(win: bool):
    """
    ✅ 기존 로직 그대로:
    - wins/losses 누적
    - total>=20부터 winrate 기준 enter_score 자동 튜닝
    - enter_score 45~85 clamp
    """
    # Supabase 있으면 Supabase를 "정본"으로, 없으면 로컬 JSON
    row = _sb_get_stats_row()
    if row is None:
        state = load_state()
        if win:
            state["wins"] += 1
        else:
            state["losses"] += 1

        total = state["wins"] + state["losses"]
        if total >= 20:
            wr = state["wins"] / total
            if wr < 0.50:
                state["enter_score"] += 2
            elif wr > 0.65:
                state["enter_score"] -= 1
            state["enter_score"] = max(45, min(85, state["enter_score"]))

        save_state(state)
        return state["enter_score"]

    # Supabase 기반
    wins = int(row.get("wins", 0))
    losses = int(row.get("losses", 0))
    enter_score = int(row.get("enter_score", 60))

    if win:
        wins += 1
    else:
        losses += 1

    total = wins + losses
    if total >= 20:
        wr = wins / total
        if wr < 0.50:
            enter_score += 2
        elif wr > 0.65:
            enter_score -= 1
        enter_score = max(45, min(85, enter_score))

    # stats 테이블에 저장
    _sb_update_stats({
        "wins": wins,
        "losses": losses,
        "enter_score": enter_score,
        "last_update": datetime.now(timezone.utc).isoformat(),
        "winrate": round((wins / max(1, total)) * 100.0, 2),
        "trades": int(row.get("trades", 0)),  # trades는 record_trade_result에서 증가
    })

    # 로컬도 같이 백업 저장(선택)
    try:
        state = {"wins": wins, "losses": losses, "enter_score": enter_score}
        save_state(state)
    except Exception:
        pass

    return enter_score

# -------------------------
# 2) AI 성능 트래커 (승률/트레이드수 저장)
# -------------------------
def _load_stats_local():
    return _read_json(
        STATS_FILE,
        {"wins": 0, "losses": 0, "trades": 0, "winrate": 0, "last_update": None, "enter_score": 60},
    )

def _save_stats_local(stats):
    _write_json(STATS_FILE, stats)

def record_trade_result(pnl: float):
    """
    trader.py에서 EXIT 시 pnl_est를 넘겨주면:
    - trades +1
    - pnl>0 => win, else loss
    - winrate 업데이트
    """
    try:
        pnl = float(pnl)
    except Exception:
        return

    sb = _sb_client()
    if sb is not None:
        try:
            # 1) trades 로그 저장
            sb.table("ai_trades").insert({"pnl": pnl}).execute()

            # 2) stats 업데이트
            row = _sb_get_stats_row() or {}
            wins = int(row.get("wins", 0))
            losses = int(row.get("losses", 0))
            trades = int(row.get("trades", 0))

            trades += 1
            if pnl > 0:
                wins += 1
                win = True
            else:
                losses += 1
                win = False

            winrate = round((wins / max(1, trades)) * 100.0, 2)

            # enter_score 자동 튜닝은 update_result 로직 사용(정확히 기존 로직 유지)
            # 다만 update_result가 wins/losses를 또 올리면 안되므로:
            # 여기서는 "튜닝만" 재계산해서 stats에 반영한다.
            enter_score = int(row.get("enter_score", 60))
            total = wins + losses
            if total >= 20:
                wr01 = wins / total
                if wr01 < 0.50:
                    enter_score = min(85, enter_score + 2)
                elif wr01 > 0.65:
                    enter_score = max(45, enter_score - 1)

            _sb_update_stats({
                "wins": wins,
                "losses": losses,
                "trades": trades,
                "winrate": winrate,
                "enter_score": enter_score,
                "last_update": _utc_now_iso(),
            })

            # 로컬 백업도 같이
            try:
                _save_stats_local({
                    "wins": wins,
                    "losses": losses,
                    "trades": trades,
                    "winrate": winrate,
                    "last_update": _utc_now_iso(),
                    "enter_score": enter_score,
                })
                save_state({"wins": wins, "losses": losses, "enter_score": enter_score})
            except Exception:
                pass

            return
        except Exception:
            # Supabase 실패 시 로컬로 폴백
            pass

    # ---- local fallback ----
    stats = _load_stats_local()
    stats["trades"] = int(stats.get("trades", 0)) + 1

    if pnl > 0:
        stats["wins"] = int(stats.get("wins", 0)) + 1
    else:
        stats["losses"] = int(stats.get("losses", 0)) + 1

    stats["winrate"] = round(int(stats["wins"]) / max(1, int(stats["trades"])) * 100, 2)
    stats["last_update"] = _utc_now_iso()

    # 로컬 enter_score 튜닝도 반영
    total = int(stats["wins"]) + int(stats["losses"])
    enter_score = int(stats.get("enter_score", 60))
    if total >= 20:
        wr01 = int(stats["wins"]) / total
        if wr01 < 0.50:
            enter_score += 2
        elif wr01 > 0.65:
            enter_score -= 1
        enter_score = max(45, min(85, enter_score))
    stats["enter_score"] = enter_score

    _save_stats_local(stats)
    save_state({"wins": stats["wins"], "losses": stats["losses"], "enter_score": enter_score})

def get_ai_stats():
    """
    trader.py의 /status가 기대하는 형태:
    {winrate: %, wins: int, losses: int}
    """
    row = _sb_get_stats_row()
    if row is not None:
        return {
            "wins": int(row.get("wins", 0)),
            "losses": int(row.get("losses", 0)),
            "trades": int(row.get("trades", 0)),
            "winrate": float(row.get("winrate", 0)),
            "enter_score": int(row.get("enter_score", 60)),
            "last_update": row.get("last_update"),
        }

    # local fallback
    return _load_stats_local()

_last_notified_winrate = 0.0

def check_winrate_milestone():
    """
    기존 로직 유지:
    - 5% 단위 상승 알림
    - wins >= 20일 때만
    """
    global _last_notified_winrate
    stats = get_ai_stats()
    wr = float(stats.get("winrate") or 0)
    wins = int(stats.get("wins") or 0)

    if wr >= _last_notified_winrate + 5 and wins >= 20:
        _last_notified_winrate = wr
        return f"🤖 AI 진화 감지\n승률 상승 → {wr}%"
    return None
