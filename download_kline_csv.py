# download_kline_csv.py
# Bybit v5 Kline -> CSV downloader
# Example:
#   python download_kline_csv.py --symbol BTCUSDT --interval 15 --days 90 --category linear --out data/BTCUSDT_15m.csv
#   python download_kline_csv.py --symbol ETHUSDT --interval 60 --start "2025-11-01" --end "2026-02-01" --out data/ETHUSDT_1h.csv

import argparse, csv, time, datetime as dt
import requests

BASE = "https://api.bybit.com"

def to_ms(s: str) -> int:
    # "YYYY-mm-dd" or "YYYY-mm-dd HH:MM:SS" (UTC assumed)
    s = s.strip().replace("T", " ")
    if len(s) == 10:
        s = s + " 00:00:00"
    t = dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    # treat as UTC
    return int(t.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

def now_ms() -> int:
    return int(time.time() * 1000)

def fetch_page(category: str, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000):
    url = f"{BASE}/v5/market/kline"
    params = {
        "category": category,
        "symbol": symbol,
        "interval": interval,
        "start": start_ms,
        "end": end_ms,
        "limit": limit,
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    j = r.json()
    if str(j.get("retCode")) != "0":
        raise RuntimeError(f"Bybit retCode={j.get('retCode')} retMsg={j.get('retMsg')}")

    lst = j.get("result", {}).get("list", []) or []
    # Bybit returns list items like:
    # [ openTime, open, high, low, close, volume, turnover ]
    # openTime is ms string
    rows = []
    for it in lst:
        try:
            ts = int(float(it[0]))
            o, h, l, c = float(it[1]), float(it[2]), float(it[3]), float(it[4])
            v = float(it[5]) if len(it) > 5 else 0.0
            rows.append((ts, o, h, l, c, v))
        except Exception:
            pass
    # Sort ascending
    rows.sort(key=lambda x: x[0])
    return rows

def download(category: str, symbol: str, interval: str, start_ms: int, end_ms: int):
    all_rows = []
    cur = start_ms
    # paginate forward by using last candle openTime + 1ms
    while cur < end_ms:
        rows = fetch_page(category, symbol, interval, cur, end_ms, limit=1000)
        if not rows:
            break
        all_rows.extend(rows)
        last_ts = rows[-1][0]
        nxt = last_ts + 1
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.12)  # gentle

    # de-dup by timestamp
    seen = set()
    dedup = []
    for r in all_rows:
        if r[0] in seen:
            continue
        seen.add(r[0])
        dedup.append(r)
    dedup.sort(key=lambda x: x[0])
    return dedup

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="linear", choices=["linear", "inverse", "spot"])
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--interval", required=True, help="1,3,5,15,30,60,120,240,360,720,D,W,M")
    ap.add_argument("--days", type=int, default=0, help="If set, downloads last N days (UTC). Overrides --start/--end.")
    ap.add_argument("--start", default="", help='UTC "YYYY-mm-dd" or "YYYY-mm-dd HH:MM:SS"')
    ap.add_argument("--end", default="", help='UTC "YYYY-mm-dd" or "YYYY-mm-dd HH:MM:SS"')
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    symbol = args.symbol.upper().strip()
    interval = str(args.interval).strip()
    category = args.category

    if args.days and args.days > 0:
        end_ms = now_ms()
        start_ms = end_ms - args.days * 24 * 60 * 60 * 1000
    else:
        if not args.start or not args.end:
            raise SystemExit("Either use --days N  OR provide both --start and --end")
        start_ms = to_ms(args.start)
        end_ms = to_ms(args.end)

    out = args.out.strip()
    if not out:
        suffix = f"{interval}m" if interval.isdigit() else interval
        out = f"{symbol}_{suffix}.csv"

    rows = download(category, symbol, interval, start_ms, end_ms)

    if not rows:
        raise SystemExit("No data returned. Check symbol/category/interval and date range.")

    # write CSV
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for ts, o, h, l, c, v in rows:
            w.writerow([int(ts // 1000), o, h, l, c, v])  # seconds timestamp

    print(f"OK: wrote {len(rows)} candles -> {out}")

if __name__ == "__main__":
    main()
