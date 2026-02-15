import os
import requests

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
TABLE = "coin_stats"

def _headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

def _ok():
    return bool(SUPABASE_URL and SUPABASE_KEY)

def record(symbol, pnl):
    if not _ok():
        return  # DB 미설정이면 조용히 무시(봇 안 죽게)

    symbol = symbol.upper().strip()
    # 1) 현재 값 가져오기
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABLE}",
        headers=_headers(),
        params={"select": "wins,losses", "symbol": f"eq.{symbol}"},
        timeout=10,
    )
    r.raise_for_status()
    rows = r.json()
    wins = rows[0]["wins"] if rows else 0
    losses = rows[0]["losses"] if rows else 0

    # 2) 업데이트 값 계산
    if pnl > 0:
        wins += 1
    else:
        losses += 1

    payload = {"symbol": symbol, "wins": wins, "losses": losses}

    # 3) upsert (있으면 update, 없으면 insert)
    u = requests.post(
        f"{SUPABASE_URL}/rest/v1/{TABLE}",
        headers=_headers(),
        params={"on_conflict": "symbol"},
        json=payload,
        timeout=10,
    )
    u.raise_for_status()

def winrate(symbol):
    if not _ok():
        return 50

    symbol = symbol.upper().strip()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABLE}",
        headers=_headers(),
        params={"select": "wins,losses", "symbol": f"eq.{symbol}", "limit": "1"},
        timeout=10,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return 50

    w = int(rows[0]["wins"])
    l = int(rows[0]["losses"])
    total = w + l
    if total == 0:
        return 50
    return round(w / total * 100, 1)
