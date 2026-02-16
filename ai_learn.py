# ===== file: ai_learn.py (SUPABASE UPGRADE + JSON FALLBACK) =====
import json
import os
from datetime import datetime
import requests

# -------------------------
# Local fallback files
# -------------------------
LEARN_FILE = "learn_state.json"
STATS_FILE = "ai_stats.json"

# -------------------------
# Supabase settings
# -------------------------
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or ""
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "coin_stats")
GLOBAL_SYMBOL = "__GLOBAL__"

def _sb_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)

def _sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

def _sb_select_global():
    # GET /rest/v1/coin_stats?select=*&symbol=eq.__GLOBAL__&limit=1
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    params = {"select": "*", "symbol": f"eq.{GLOBAL_SYMBOL}", "limit": "1"}
    r = requests.get(url, headers=_sb_headers(), params=params, timeout=10)
    r.raise_for_status()
    arr = r.json() if isinstance(r.json(), list) else []
    return arr[0] if arr else None

def _sb_upsert_global(row: dict):
    # POST upsert with Prefer: resolution=merge-duplicates
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = _sb_headers()
    headers["Prefer"] = "resolution=merge-duplicates"
    r = requests.post(url, headers=headers, data=json.dumps([row]), timeout=10)
    r.raise_for_status()
    return True

def _sb_patch_global(patch: dict):
    # PATCH /rest/v1/coin_stats?symbol=eq.__GLOBAL__
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    params = {"symbol": f"eq.{GLOBAL_SYMBOL}"}
    headers = _sb_headers()
    headers["Prefer"] = "return=minimal"
    r = requests.patch(url, headers=headers, params=params, data=json.dumps(patch), timeout=10)
    r.raise_for_status()
    return True

# -------------------------
# JSON fallback (기존 방식)
# -------------------------
def _load_json(path, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# -------------------------
# Public APIs (trader.py가 쓰는 함수들)
# -------------------------

# 1) enter_score 자동 튜닝
def load_state():
    # Supabase 우선
    if _sb_enabled():
        try:
            row = _sb_select_global()
            if not row:
                _sb_upsert_global({"symbol": GLOBAL_SYMBOL})
                row = _sb_select_global() or {}
            return {
                "wins": int(row.get("wins") or 0),
                "losses": int(row.get("losses") or 0),
                "enter_score": int(row.get("enter_score") or 60),
            }
        except Exception:
            pass

    # fallback
    return _load_json(LEARN_FILE, {"wins": 0, "losses": 0, "enter_score": 60})

def save_state(state):
    # Supabase 우선
    if _sb_enabled():
        try:
            patch = {
                "wins": int(state.get("wins") or 0),
                "losses": int(state.get("losses") or 0),
                "enter_score": int(state.get("enter_score") or 60),
                "last_update": datetime.utcnow().isoformat(),
            }
            _sb_patch_global(patch)
            return
        except Exception:
            pass

    # fallback
    _save_json(LEARN_FILE, state)

def update_result(win: bool):
    state = load_state()

    if win:
        state["wins"] = int(state.get("wins") or 0) + 1
    else:
        state["losses"] = int(state.get("losses") or 0) + 1

    total = int(state.get("wins") or 0) + int(state.get("losses") or 0)

    # 자동 튜닝(원래 로직 유지)
    if total >= 20:
        winrate = (state["wins"] / total) if total else 0

        if winrate < 0.50:
            state["enter_score"] = int(state.get("enter_score") or 60) + 2
        elif winrate > 0.65:
            state["enter_score"] = int(state.get("enter_score") or 60) - 1

        state["enter_score"] = max(45, min(85, int(state["enter_score"])))

    save_state(state)
    return int(state["enter_score"])

# 2) AI 성능 트래커 (승률/트레이드수 저장)
def _load_stats():
    # Supabase 우선
    if _sb_enabled():
        try:
            row = _sb_select_global()
            if not row:
                _sb_upsert_global({"symbol": GLOBAL_SYMBOL})
                row = _sb_select_global() or {}
            return {
                "wins": int(row.get("wins") or 0),
                "losses": int(row.get("losses") or 0),
                "trades": int(row.get("trades") or 0),
                "winrate": float(row.get("winrate") or 0),
                "last_update": row.get("last_update"),
            }
        except Exception:
            pass

    # fallback
    return _load_json(
        STATS_FILE,
        {"wins": 0, "losses": 0, "trades": 0, "winrate": 0, "last_update": None},
    )

def _save_stats(stats):
    # Supabase 우선
    if _sb_enabled():
        try:
            patch = {
                "wins": int(stats.get("wins") or 0),
                "losses": int(stats.get("losses") or 0),
                "trades": int(stats.get("trades") or 0),
                "winrate": float(stats.get("winrate") or 0),
                "last_update": datetime.utcnow().isoformat(),
            }
            _sb_patch_global(patch)
            return
        except Exception:
            pass

    # fallback
    _save_json(STATS_FILE, stats)

def record_trade_result(pnl: float):
    stats = _load_stats()

    stats["trades"] = int(stats.get("trades") or 0) + 1
    if float(pnl) > 0:
        stats["wins"] = int(stats.get("wins") or 0) + 1
    else:
        stats["losses"] = int(stats.get("losses") or 0) + 1

    t = int(stats["trades"])
    w = int(stats["wins"])
    stats["winrate"] = round(w / max(1, t) * 100, 2)
    stats["last_update"] = datetime.utcnow().isoformat()

    _save_stats(stats)

def get_ai_stats():
    return _load_stats()

_last_notified_winrate = 0.0

def check_winrate_milestone():
    global _last_notified_winrate
    stats = get_ai_stats()
    wr = float(stats.get("winrate") or 0)
    wins = int(stats.get("wins") or 0)

    # 5% 단위 상승 알림(승 20 이상일 때만)
    if wins >= 20 and wr >= (_last_notified_winrate + 5):
        _last_notified_winrate = wr
        return f"🤖 AI 진화 감지\n승률 상승 → {wr}%"
    return None
