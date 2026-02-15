# ===== file: data_store.py =====
# ✅ Bybit 과거 캔들 수집 -> 로컬 캐시(json)로 저장
# - DRY_RUN에서도 학습/백테스트는 가능(과거캔들은 API 필요)
# - 저장 위치: data/{symbol}_{interval}.json

import os
import json
import time
import requests
from typing import List, Dict, Any

BYBIT_BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com").rstrip("/")
CATEGORY = os.getenv("CATEGORY", "linear")
PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

DATA_DIR = "data"

def _path(symbol: str, interval: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, f"{symbol.upper()}_{str(interval)}.json")

def _safe_json(r: requests.Response):
    try:
        return r.json()
    except Exception:
        return {}

def load_candles(symbol: str, interval: str) -> List[Dict[str, Any]]:
    p = _path(symbol, interval)
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_candles(symbol: str, interval: str, candles: List[Dict[str, Any]]):
    p = _path(symbol, interval)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(candles, f, ensure_ascii=False, indent=2)

def _fetch_kline(symbol: str, interval: str, limit: int = 1000, end: int = None):
    # Bybit v5: /v5/market/kline
    params = {
        "category": CATEGORY,
        "symbol": symbol.upper(),
        "interval": str(interval),
        "limit": int(limit),
    }
    if end:
        params["end"] = int(end)

    r = requests.get(f"{BYBIT_BASE_URL}/v5/market/kline", params=params, headers=HEADERS, timeout=15, proxies=PROXIES)
    j = _safe_json(r)
    lst = ((j.get("result") or {}).get("list") or [])

    # list format: [openTime, open, high, low, close, volume, turnover]
    out = []
    for row in lst:
        try:
            ts = int(row[0])
            out.append({
                "ts": ts,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            })
        except Exception:
            continue

    # Bybit는 보통 최신 -> 과거 순서로 옴. ts 오름차순으로 맞추기
    out.sort(key=lambda x: x["ts"])
    return out

def update_cache(symbol: str, interval: str, chunk_limit: int = 1000, max_chunks: int = 10):
    """
    - chunk_limit: 요청당 limit
    - max_chunks: 몇 번 이어서 땡길지 (1000 * 10 = 10000개 수준)
    """
    symbol = symbol.upper()
    interval = str(interval)

    existing = load_candles(symbol, interval)
    existing_ts = set(c["ts"] for c in existing)
    end = None

    merged = existing[:]
    for _ in range(max_chunks):
        chunk = _fetch_kline(symbol, interval, limit=chunk_limit, end=end)
        if not chunk:
            break

        # 다음 end는 "가장 오래된 캔들의 ts - 1"
        end = int(chunk[0]["ts"]) - 1

        added = 0
        for c in chunk:
            if c["ts"] not in existing_ts:
                merged.append(c)
                existing_ts.add(c["ts"])
                added += 1

        # 너무 안 늘어나면 중단
        if added == 0:
            break

        time.sleep(0.15)

    merged.sort(key=lambda x: x["ts"])
    save_candles(symbol, interval, merged)
    return len(merged)


# ===== file: walkforward.py =====
# ✅ 워크포워드: (train -> optimize -> test) 반복
from typing import Callable, Dict, Any, List

def walk_forward(
    candles: List[Dict[str, Any]],
    train_bars: int,
    test_bars: int,
    optimizer_fn: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    make_signal_fn: Callable[[Dict[str, Any]], Callable],
    backtest_fn: Callable[[List[Dict[str, Any]], Callable], Dict[str, Any]],
):
    windows = []
    start = 0

    while True:
        train = candles[start : start + train_bars]
        test = candles[start + train_bars : start + train_bars + test_bars]
        if len(train) < train_bars or len(test) < test_bars:
            break

        best = optimizer_fn(train) or {}
        best_params = best.get("params") or best

        signal_fn = make_signal_fn(best_params)
        test_res = backtest_fn(test, signal_fn)

        windows.append({
            "start": start,
            "train_bars": train_bars,
            "test_bars": test_bars,
            "best_params": best_params,
            "test_metrics": test_res,
        })

        start += test_bars

    # 요약
    total_trades = sum(int(w["test_metrics"].get("trades", 0) or 0) for w in windows)
    total_net = sum(float(w["test_metrics"].get("net_pnl", 0.0) or 0.0) for w in windows)
    avg_wr = 0.0
    if windows:
        avg_wr = sum(float(w["test_metrics"].get("winrate", 0.0) or 0.0) for w in windows) / len(windows)

    return {
        "windows": windows,
        "summary": {
            "windows": len(windows),
            "total_trades": total_trades,
            "total_net_pnl": round(total_net, 4),
            "avg_winrate": round(avg_wr, 2),
        }
    }
