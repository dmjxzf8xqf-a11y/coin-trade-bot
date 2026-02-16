# run_backtest_suite.py
# One-click:
# - Download Bybit klines for multiple symbols -> CSV
# - Run simple backtest per symbol
# - Optional: seed Supabase coin_stats via ai_coin_performance.record(symbol, pnl)

import os
import csv
import time
import math
import argparse
import datetime as dt
import requests

BYBIT_BASE = "https://api.bybit.com"

# ---- optional Supabase seeding (uses your existing module) ----
try:
    from ai_coin_performance import record as record_coin_stat
except Exception:
    record_coin_stat = None


# =========================
# KLINE DOWNLOAD (Bybit v5)
# =========================
def now_ms() -> int:
    return int(time.time() * 1000)

def to_ms(s: str) -> int:
    s = s.strip().replace("T", " ")
    if len(s) == 10:
        s = s + " 00:00:00"
    t = dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return int(t.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

def fetch_kline_page(category: str, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000):
    url = f"{BYBIT_BASE}/v5/market/kline"
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
    # item: [openTime, open, high, low, close, volume, turnover]
    rows = []
    for it in lst:
        ts = int(float(it[0]))
        o = float(it[1]); h = float(it[2]); l = float(it[3]); c = float(it[4])
        v = float(it[5]) if len(it) > 5 else 0.0
        rows.append((ts, o, h, l, c, v))
    rows.sort(key=lambda x: x[0])  # ascending
    return rows

def download_klines(category: str, symbol: str, interval: str, start_ms: int, end_ms: int):
    all_rows = []
    cur = start_ms
    while cur < end_ms:
        rows = fetch_kline_page(category, symbol, interval, cur, end_ms, limit=1000)
        if not rows:
            break
        all_rows.extend(rows)
        last_ts = rows[-1][0]
        nxt = last_ts + 1
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.12)

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

def write_csv(out_path: str, rows):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for ts_ms, o, h, l, c, v in rows:
            w.writerow([int(ts_ms // 1000), o, h, l, c, v])


# =========================
# SIMPLE BACKTEST (LONG ONLY)
# (same style as your quick backtest.py)
# =========================
def ema(values, period):
    k = 2 / (period + 1)
    out = []
    e = values[0]
    for v in values:
        e = v * k + e * (1 - k)
        out.append(e)
    return out

def rsi(values, period=14):
    gains = []
    losses = []
    rsis = [50]
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
        if i < period:
            rsis.append(50)
            continue
        avg_gain = sum(gains[i-period:i]) / period
        avg_loss = sum(losses[i-period:i]) / period
        if avg_loss == 0:
            rsis.append(100)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100 - (100 / (1 + rs)))
    return rsis

def atr(candles, period=14):
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)

    atrs = [trs[0] if trs else 0.0]
    for i in range(1, len(trs)):
        if i < period:
            atrs.append(trs[i])
        else:
            atrs.append(sum(trs[i-period:i]) / period)
    # align length to candles
    atrs = [atrs[0]] + atrs
    if len(atrs) < len(candles):
        atrs += [atrs[-1]] * (len(candles) - len(atrs))
    return atrs

def load_csv_candles(path):
    candles = []
    with open(path, "r", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            candles.append({
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
    return candles

def run_backtest_one(
    symbol: str,
    csv_path: str,
    *,
    fee: float,
    slip: float,
    enter_score: int,
    ema_fast_n: int,
    ema_slow_n: int,
    rsi_n: int,
    atr_n: int,
    sl_atr: float,
    tp_atr: float,
    seed_db: bool,
):
    candles = load_csv_candles(csv_path)
    closes = [c["close"] for c in candles]
    if len(closes) < max(ema_slow_n, rsi_n, atr_n) + 50:
        return {"symbol": symbol, "error": "not enough candles", "trades": 0}

    ef = ema(closes, ema_fast_n)
    es = ema(closes, ema_slow_n)
    rs = rsi(closes, rsi_n)
    at = atr(candles, atr_n)

    position = None  # (entry, sl, tp)
    wins = 0
    losses = 0
    balance = 1.0
    trades = 0

    warmup = max(ema_slow_n, rsi_n, atr_n) + 10

    for i in range(warmup, len(candles)):
        price = closes[i]
        atr_val = at[i] if at[i] > 0 else 0.0

        trend_up = ef[i] > es[i]

        score = 0
        if trend_up:
            score += 40
        if 45 <= rs[i] <= 65:
            score += 30
        if atr_val > 0 and (atr_val / price) > 0.002:
            score += 20

        # entry
        if position is None and score >= enter_score and atr_val > 0:
            entry = price * (1 + slip)
            sl = entry - atr_val * sl_atr
            tp = entry + atr_val * tp_atr
            position = (entry, sl, tp)

        # exit
        if position is not None:
            entry, sl, tp = position

            # SL hit
            if candles[i]["low"] <= sl:
                pnl = (sl - entry) / entry
                balance *= (1 + pnl - fee)
                losses += 1
                trades += 1
                if seed_db and record_coin_stat:
                    record_coin_stat(symbol, pnl)
                position = None

            # TP hit
            elif candles[i]["high"] >= tp:
                pnl = (tp - entry) / entry
                balance *= (1 + pnl - fee)
                wins += 1
                trades += 1
                if seed_db and record_coin_stat:
                    record_coin_stat(symbol, pnl)
                position = None

    total = wins + losses
    winrate = (wins / total * 100) if total else 0.0
    return {
        "symbol": symbol,
        "trades": total,
        "wins": wins,
        "losses": losses,
        "winrate": round(winrate, 2),
        "balance_pct": round(balance * 100, 2),
        "csv": csv_path,
    }


# =========================
# MAIN
# =========================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True, help="ex) BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--interval", default="15", help="1,3,5,15,30,60,120,240,360,720,D,W,M")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--category", default="linear", choices=["linear", "inverse", "spot"])
    ap.add_argument("--outdir", default="data")

    # backtest params
    ap.add_argument("--fee", type=float, default=0.0006)
    ap.add_argument("--slip", type=float, default=0.0005)
    ap.add_argument("--enter_score", type=int, default=60)
    ap.add_argument("--ema_fast", type=int, default=9)
    ap.add_argument("--ema_slow", type=int, default=21)
    ap.add_argument("--rsi", type=int, default=14)
    ap.add_argument("--atr", type=int, default=14)
    ap.add_argument("--sl_atr", type=float, default=1.5)
    ap.add_argument("--tp_atr", type=float, default=2.0)

    ap.add_argument("--seed-db", action="store_true", help="seed Supabase coin_stats via ai_coin_performance.record")
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not syms:
        raise SystemExit("No symbols")

    if args.seed_db and record_coin_stat is None:
        print("⚠️ seed-db requested but ai_coin_performance.record import failed. Seeding will be skipped.")

    end_ms = now_ms()
    start_ms = end_ms - int(args.days) * 24 * 60 * 60 * 1000

    print(f"Downloading {args.days}d of {args.interval} for {len(syms)} symbols...")

    results = []
    for sym in syms:
        suffix = f"{args.interval}m" if args.interval.isdigit() else args.interval
        csv_path = os.path.join(args.outdir, f"{sym}_{suffix}.csv")

        try:
            rows = download_klines(args.category, sym, args.interval, start_ms, end_ms)
            if not rows:
                print(f"❌ {sym}: no data")
                continue
            write_csv(csv_path, rows)
            print(f"✅ {sym}: wrote {len(rows)} candles -> {csv_path}")
        except Exception as e:
            print(f"❌ {sym}: download error: {e}")
            continue

        try:
            res = run_backtest_one(
                sym,
                csv_path,
                fee=args.fee,
                slip=args.slip,
                enter_score=args.enter_score,
                ema_fast_n=args.ema_fast,
                ema_slow_n=args.ema_slow,
                rsi_n=args.rsi,
                atr_n=args.atr,
                sl_atr=args.sl_atr,
                tp_atr=args.tp_atr,
                seed_db=args.seed_db,
            )
            results.append(res)
            if "error" in res:
                print(f"⚠️ {sym}: {res['error']}")
            else:
                print(f"📈 {sym}: trades={res['trades']} winrate={res['winrate']}% balance={res['balance_pct']}%")
        except Exception as e:
            print(f"❌ {sym}: backtest error: {e}")

    print("\n===== SUMMARY =====")
    results_ok = [r for r in results if "error" not in r]
    results_ok.sort(key=lambda x: (x["winrate"], x["balance_pct"]), reverse=True)
    for r in results_ok:
        print(f"{r['symbol']:10s}  trades={r['trades']:4d}  winrate={r['winrate']:6.2f}%  balance={r['balance_pct']:7.2f}%")

    if args.seed_db:
        print("\n✅ seed-db ON: backtest trade outcomes were sent to coin_stats (wins/losses).")

if __name__ == "__main__":
    main()
