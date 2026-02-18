# ai_learn.py (FINAL: SUPABASE + DATA_DIR JSON FALLBACK + ATOMIC SAVE)
import json
import os
from datetime import datetime
import requests

from storage_utils import data_path, safe_read_json, atomic_write_json

# -------------------------
# Local fallback files (DATA_DIR 아래로 고정)
# -------------------------
LEARN_FILE = data_path("learn_state.json")
STATS_FILE = data_path("ai_stats.json")

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
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    params = {"select": "*", "symbol": f"eq.{GLOBAL_SYMBOL}", "limit": "1"}
    r = requests.get(url, headers=_sb_headers(), params=params, timeout=10)
    r.raise_for_status()
    arr = r.json() if isinstance(r.json(), list) else []
    return arr[0] if arr else None

def _sb_upsert_global(row: dict):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = _sb_headers()
    headers["Prefer"] = "resolution=merge-duplicates"
    r = requests.post(url, headers=headers, data=json.dumps([row]), timeout=10)
    r.raise_for_status()
    return True

def _sb_patch_global(patch: dict):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    params = {"symbol": f"eq.{GLOBAL_SYMBOL}"}
    headers = _sb_headers()
    headers["Prefer"] = "return=minimal"
    r = requests.patch(url, headers=headers, params=params, data=json.dumps(patch), timeout=10)
    r.raise_for_status()
    return True

# -------------------------
# JSON fallback (ATOMIC + RECOVERY)
# -------------------------
def _load_json(path, default):
    return safe_read_json(path, default)

def _save_json(path, obj):
    try:
        atomic_write_json(path, obj, backup=True)
    except Exception:
        pass

# -------------------------
# Public APIs
# -------------------------
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
        except Exception as e:
    print("❌ Supabase load error:", e)

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
        except Exception as e:
    print("❌ Supabase load error:", e)

    _save_json(LEARN_FILE, state)

def update_result(win: bool):
    state = load_state()

    if win:
        state["wins"] = int(state.get("wins") or 0) + 1
    else:
        state["losses"] = int(state.get("losses") or 0) + 1

    total = int(state.get("wins") or 0) + int(state.get("losses") or 0)

    # 자동 튜닝 (원래 의도 유지)
    if total >= 20:
        winrate = (state["wins"] / max(total, 1)) * 100.0
        # 너무 공격적/보수적으로 튀지 않게 완만 조정
        if winrate >= 60:
            state["enter_score"] = min(75, int(state.get("enter_score") or 60) + 1)
        elif winrate <= 45:
            state["enter_score"] = max(45, int(state.get("enter_score") or 60) - 1)

    save_state(state)
    return state

def get_ai_stats():
    # wins/losses 기반으로 항상 일관되게 표시
    st = load_state()
    wins = int(st.get("wins") or 0)
    losses = int(st.get("losses") or 0)
    total = wins + losses
    winrate = round((wins / total) * 100, 2) if total > 0 else 0.0
    return {"wins": wins, "losses": losses, "winrate": winrate, "enter_score": int(st.get("enter_score") or 60)}

def record_trade_result(pnl: float):
    # pnl>0 win, pnl<=0 loss
    return update_result(bool(pnl > 0))

def check_winrate_milestone():
    # 필요하면 너가 기존에 쓰던 알림 로직 붙일 자리 (현재는 None)
    return None
