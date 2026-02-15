# bybit_data.py
# - Bybit v5 kline 다운로드
# - 로컬 캐시(jsonl) 저장/로딩
import os, time, json, requests
from datetime import datetime

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

BYBIT_BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com").rstrip("/")
CATEGORY = os.getenv("CATEGORY", "linear")  # linear
DATA_DIR = os.getenv("BT_DATA_DIR", "bt_data")

os.makedirs(DATA_DIR, exist_ok=True)

def _safe_json(r: requests.Response):
    try:
        return r.json()
    except Exception:
        return {"_non_json": True, "status": r.status_code, "raw": (r.text or "")[:600]}

def _kline_path(symbol: str, interval: str):
    sym = symbol.upper()
    return os.path.join(DATA_DIR, f"{sym}_{interval}.jsonl")

def fetch_klines(symbol: str, interval: str, limit: int = 1000, end_ms: int | None = None, sleep_sec=0.12):
    """
    Bybit /v5/market/kline
    - end_ms: 특정 시점 이전 데이터 당겨올 때 사용
    - limit: 1회 최대 1000 정도로 쓰는게 안정적
    return: list of dict candles (oldest -> newest)
    """
    params = {"category": CATEGORY, "symbol": symbol.upper(), "interval": str(interval), "limit": int(limit)}
    if end_ms is not None:
        params["end"] = int(end_ms)

    url = f"{BYBIT_BASE_URL}/v5/market/kline"
    r = requests.get(url, params=params, headers=HEADERS, timeout=15)
    j = _safe_json(r)
    if j.get("_non_json"):
        raise RuntimeError(f"Bybit non-json: {j}")
    if str(j.get("retCode", "0")) != "0":
        raise RuntimeError(f"Bybit error: {j.get('retCode')} {j.get('retMsg')}")

    lst = (j.get("result") or {}).get("list") or []
    # Bybit는 보통 최신->과거 순으로 내려주는 케이스가 많아서 뒤집어줌
    out = []
    for row in reversed(lst):
        # row: [startTime, open, high, low, close, volume, turnover]
        out.append({
            "ts": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]) if len(row) > 5 else 0.0,
        })

    time.sleep(sleep_sec)
    return out

def download_history(symbol: str, interval: str, days: int, chunk_limit: int = 1000):
    """
    days 만큼 과거로 내려가며 캔들 수집 -> bt_data/ 에 jsonl로 누적 저장
    """
    path = _kline_path(symbol, interval)
    existing = load_cached(symbol, interval)
    if existing:
        oldest_ts = existing[0]["ts"]
    else:
        oldest_ts = int(time.time() * 1000)

    target_oldest = int(time.time() * 1000) - days * 24 * 60 * 60 * 1000

    candles = existing[:]  # oldest->newest
    end_ms = oldest_ts
    while True:
        if candles and candles[0]["ts"] <= target_oldest:
            break

        batch = fetch_klines(symbol, interval, limit=chunk_limit, end_ms=end_ms)
        if not batch:
            break

        # batch도 oldest->newest
        # end_ms 업데이트: 더 과거로
        end_ms = batch[0]["ts"] - 1

        # 앞에 붙이기
        if candles:
            # 중복 제거
            have = set(c["ts"] for c in candles[:2000])  # 앞쪽만 대충
            batch = [c for c in batch if c["ts"] not in have]
        candles = batch + candles

        if batch[0]["ts"] <= target_oldest:
            break

    save_cached(symbol, interval, candles)
    return candles

def save_cached(symbol: str, interval: str, candles: list[dict]):
    path = _kline_path(symbol, interval)
    with open(path, "w", encoding="utf-8") as f:
        for c in candles:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

def load_cached(symbol: str, interval: str):
    path = _kline_path(symbol, interval)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except:
                pass
    # 정렬 보장
    out.sort(key=lambda x: x["ts"])
    return out

def human_ts(ms: int):
    return datetime.utcfromtimestamp(ms/1000).strftime("%Y-%m-%d %H:%M:%S")
