# data_store.py
# ✅ 과거 캔들 수집/캐시 모듈
# - data/{SYMBOL}_{INTERVAL}.json 로 저장
# - 이미 저장된 데이터가 있으면 이어받기(append) 가능
#
# candle 포맷(통일):
#   {"ts": int(ms), "open": float, "high": float, "low": float, "close": float, "volume": float}

import os
import json
import time
import requests
from typing import List, Dict, Optional

DATA_DIR = os.getenv("DATA_DIR", "data")
BYBIT_BASE_URL = (os.getenv("BYBIT_BASE_URL") or "https://api.bybit.com").rstrip("/")
CATEGORY = os.getenv("CATEGORY", "linear")

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def _ensure_dir():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        pass

def _path(symbol: str, interval: str) -> str:
    sym = (symbol or "").upper()
    itv = str(interval)
    return os.path.join(DATA_DIR, f"{sym}_{itv}.json")

def load_candles(symbol: str, interval: str) -> List[Dict]:
    p = _path(symbol, interval)
    try:
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except Exception:
        return []

def save_candles(symbol: str, interval: str, candles: List[Dict]):
    _ensure_dir()
    p = _path(symbol, interval)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(candles, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _normalize_bybit_kline_row(row) -> Optional[Dict]:
    # Bybit v5 kline list: [startTime, open, high, low, close, volume, turnover]
    try:
        ts = int(row[0])
        o = float(row[1]); h = float(row[2]); l = float(row[3]); c = float(row[4])
        v = float(row[5]) if len(row) > 5 else 0.0
        return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}
    except Exception:
        return None

def fetch_bybit_klines(symbol: str, interval: str, limit: int = 1000, start_ms: Optional[int] = None) -> List[Dict]:
    """
    interval 예: "1","3","5","15","30","60","240","D"...
    start_ms: 특정 시점부터 가져오기(없으면 최신부터 limit)
    """
    params = {
        "category": CATEGORY,
        "symbol": (symbol or "").upper(),
        "interval": str(interval),
        "limit": int(limit),
    }
    if start_ms is not None:
        params["start"] = int(start_ms)

    url = f"{BYBIT_BASE_URL}/v5/market/kline"
    r = requests.get(url, params=params, headers=HEADERS, timeout=15)
    j = r.json()
    lst = ((j.get("result") or {}).get("list") or [])
    out = []
    for row in lst:
        c = _normalize_bybit_kline_row(row)
        if c:
            out.append(c)
    # Bybit는 최신->과거로 주는 경우가 많아서 정렬
    out.sort(key=lambda x: x["ts"])
    return out

def update_cache(symbol: str, interval: str, chunk_limit: int = 1000) -> List[Dict]:
    """
    캐시에 있던 마지막 ts 이후로 이어받아 업데이트.
    """
    cur = load_candles(symbol, interval)
    last_ts = cur[-1]["ts"] if cur else None

    # start는 last_ts+1ms
    start = (last_ts + 1) if last_ts else None
    try:
        fresh = fetch_bybit_klines(symbol, interval, limit=chunk_limit, start_ms=start)
    except Exception:
        fresh = []

    # 중복 제거 + 이어붙이기
    seen = set()
    merged = []
    for c in cur:
        seen.add(int(c["ts"]))
        merged.append(c)
    for c in fresh:
        t = int(c["ts"])
        if t in seen:
            continue
        merged.append(c)
        seen.add(t)

    merged.sort(key=lambda x: x["ts"])
    save_candles(symbol, interval, merged)
    return merged
