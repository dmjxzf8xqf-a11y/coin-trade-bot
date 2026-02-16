# run_backtest_opt.py
# One-click:
# - Download Bybit klines for multiple symbols -> CSV
# - Backtest LONG/SHORT (optional), TP/SL (ATR based), TIME EXIT
# - Grid search over parameters (enter_score, sl_atr, tp_atr)
# - Optional: seed Supabase coin_stats via ai_coin_performance.record(symbol, pnl)
#
# Examples:
#   python run_backtest_opt.py --symbols BTCUSDT,ETHUSDT --interval 15 --days 180
#   python run_backtest_opt.py --symbols BTCUSDT,ETHUSDT --interval 15 --days 180 --short on
#   python run_backtest_opt.py --symbols BTCUSDT,ETHUSDT --interval 15 --days 180 --seed-db
#   python run_backtest_opt.py --symbols BTCUSDT --interval 15 --days 365 --grid on

import os, csv, time, math, argparse, datetime as dt
from itertools import product
import requests

BYBIT_BASE = "https://api.bybit.com"

# ---- optional Supabase seeding ----
try:
    from ai_coin_performance import record as record_coin_stat
except Exception:
    record_coin_stat = None


# =========================
# KLINE DOWNLOAD (Bybit v5)
# =========================
def now_ms() -> int:
    return int(time.time() * 1000)

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
    rows = []
    for it in lst:
        ts = int(float(it[0]))      # ms
        o = float(it[1]); h = float(it[2]); l = float(it[3]); c = float(it[4])
        v = float(it[5]) if len(it) > 5 else 0.0
        rows.append((ts, o, h, l, c, v))
    rows.sort(key=lambda x: x[0])
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
# Indicators
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

    if not trs:
        return [0.0] * len(candles)

    atrs = [trs[0]]
    for i in range(1, len(trs)):
        if i < period:
            atrs.append(trs[i])
        else:
            atrs.append(sum(trs[i-period:i]) / period)

    atrs = [atrs[0]] + atrs
    if len(atrs) < len(candles):
        atrs += [atrs[-1]] * (len(candles) - len(atrs))
    return atrs


# =========================
# Backtest (LONG/SHORT + TIME EXIT)
# =========================
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

def fill_price(side: str, price: float, slip: float, is_entry: bool) -> float:
    # slippage-only fill
    if side == "LONG":
        return price * (1 + slip) if is_entry else price * (1 - slip)
    else:
        return price * (1 - slip) if is_entry else price * (1 + slip)

def pnl_pct(side: str, entry: float, exit_: float) -> float:
    if side == "LONG":
        return (exit_ - entry) / entry
    else:
        return (entry - exit_) / entry

def compute_score(trend_up: bool, trend_dn: bool, rsi_val: float, atr_val: float, price: float) -> int:
    score = 0
    if trend_up or trend_dn:
        score += 40
    if 45 <= rsi_val <= 65:
        score += 30
    if atr_val > 0 and (atr_val / price) > 0.002:
        score += 20
    return score

def backtest_one(
    symbol: str,
    candles,
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
    time_exit_bars: int,
    allow_short: bool,
    seed_db: bool,
):
    closes = [c["close"] for c in candles]
    if len(closes) < max(ema_slow_n, rsi_n, atr_n) + 50:
        return {"symbol": symbol, "error": "not enough candles", "trades": 0}

    ef = ema(closes, ema_fast_n)
    es = ema(closes, ema_slow_n)
    rs = rsi(closes, rsi_n)
    at = atr(candles, atr_n)

    pos = None
    # pos: dict(side, entry, sl, tp, entry_i)
    wins = losses = 0
    balance = 1.0
    warmup = max(ema_slow_n, rsi_n, atr_n) + 10

    for i in range(warmup, len(candles)):
        price = closes[i]
        atr_val = at[i]
        trend_up = ef[i] > es[i]
        trend_dn = ef[i] < es[i]

        # entry score
        score = compute_score(trend_up, trend_dn, rs[i], atr_val, price)

        if pos is None and score >= enter_score and atr_val > 0:
            # decide side
            side = "LONG"
            if allow_short and trend_dn and not trend_up:
                side = "SHORT"
            elif trend_up:
                side = "LONG"
            elif allow_short and trend_dn:
                side = "SHORT"
            else:
                # no clear trend side
                continue

            entry = fill_price(side, price, slip, True)
            if side == "LONG":
                sl = entry - atr_val * sl_atr
                tp = entry + atr_val * tp_atr
            else:
                sl = entry + atr_val * sl_atr
                tp = entry - atr_val * tp_atr

            pos = {"side": side, "entry": entry, "sl": sl, "tp": tp, "entry_i": i}

        if pos is not None:
            side = pos["side"]
            entry = pos["entry"]
            sl = pos["sl"]
            tp = pos["tp"]
            entry_i = pos["entry_i"]

            # time exit
            if time_exit_bars > 0 and (i - entry_i) >= time_exit_bars:
                exit_fill = fill_price(side, price, slip, False)
                pnl = pnl_pct(side, entry, exit_fill) - fee
                balance *= (1 + pnl)
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                if seed_db and record_coin_stat:
                    record_coin_stat(symbol, pnl)
                pos = None
                continue

            # SL/TP hit
            if side == "LONG":
                if candles[i]["low"] <= sl:
                    exit_fill = fill_price(side, sl, slip, False)
                    pnl = pnl_pct(side, entry, exit_fill) - fee
                    balance *= (1 + pnl)
                    losses += 1
                    if seed_db and record_coin_stat:
                        record_coin_stat(symbol, pnl)
                    pos = None
                elif candles[i]["high"] >= tp:
                    exit_fill = fill_price(side, tp, slip, False)
                    pnl = pnl_pct(side, entry, exit_fill) - fee
                    balance *= (1 + pnl)
                    wins += 1
                    if seed_db and record_coin_stat:
                        record_coin_stat(symbol, pnl)
                    pos = None
            else:  # SHORT
                if candles[i]["high"] >= sl:
                    exit_fill = fill_price(side, sl, slip, False)
                    pnl = pnl_pct(side, entry, exit_fill) - fee
                    balance *= (1 + pnl)
                    losses += 1
                    if seed_db and record_coin_stat:
                        record_coin_stat(symbol, pnl)
                    pos = None
                elif candles[i]["low"] <= tp:
                    exit_fill = fill_price(side, tp, slip, False)
                    pnl = pnl_pct(side, entry, exit_fill) - fee
                    balance *= (1 + pnl)
                    wins += 1
                    if seed_db and record_coin_stat:
                        record_coin_stat(symbol, pnl)
                    pos = None

    total = wins + losses
    winrate = (wins / total * 100) if total else 0.0
    return {
        "symbol": symbol,
        "trades": total,
        "wins": wins,
        "losses": losses,
        "winrate": round(winrate, 2),
        "balance_pct": round(balance * 100, 2),
        "params": {"enter_score": enter_score, "sl_atr": sl_atr, "tp_atr": tp_atr, "time_exit_bars": time_exit_bars, "short": allow_short},
    }


# =========================
# Grid search
# =========================
def grid_params(grid_on: bool, enter_score: int, sl_atr: float, tp_atr: float):
    if not grid_on:
        return [(enter_score, sl_atr, tp_atr)]
    enter_list = [50, 60, 70]
    sl_list = [1.0, 1.5, 2.0]
    tp_list = [1.5, 2.0, 2.5]
    return list(product(enter_list, sl_list, tp_list))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True, help="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--interval", default="15")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--category", default="linear", choices=["linear", "inverse", "spot"])
    ap.add_argument("--outdir", default="data")

    ap.add_argument("--fee", type=float, default=0.0006)
    ap.add_argument("--slip", type=float, default=0.0005)

    ap.add_argument("--enter_score", type=int, default=60)
    ap.add_argument("--ema_fast", type=int, default=9)
    ap.add_argument("--ema_slow", type=int, default=21)
    ap.add_argument("--rsi", type=int, default=14)
    ap.add_argument("--atr", type=int, default=14)
    ap.add_argument("--sl_atr", type=float, default=1.5)
    ap.add_argument("--tp_atr", type=float, default=2.0)

    ap.add_argument("--short", default="off", choices=["on", "off"])
    ap.add_argument("--time_exit_bars", type=int, default=0, help="0=disabled. ex) 48 for 12h on 15m")
    ap.add_argument("--seed-db", action="store_true")
    ap.add_argument("--grid", default="off", choices=["on", "off"])

    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    allow_short = (args.short == "on")
    grid_on = (args.grid == "on")

    if args.seed_db and record_coin_stat is None:
        print("⚠️ seed-db requested but ai_coin_performance.record import failed. Seeding will be skipped.")

    end_ms = now_ms()
    start_ms = end_ms - int(args.days) * 24 * 60 * 60 * 1000

    # download CSVs
    suffix = f"{args.interval}m" if args.interval.isdigit() else args.interval
    csv_paths = {}
    for sym in syms:
        out = os.path.join(args.outdir, f"{sym}_{suffix}.csv")
        try:
            rows = download_klines(args.category, sym, args.interval, start_ms, end_ms)
            if not rows:
                print(f"❌ {sym}: no data")
                continue
            write_csv(out, rows)
            csv_paths[sym] = out
            print(f"✅ {sym}: candles={len(rows)} -> {out}")
        except Exception as e:
            print(f"❌ {sym}: download error: {e}")

    # run backtests
    candidates = grid_params(grid_on, args.enter_score, args.sl_atr, args.tp_atr)
    print(f"\nBacktest grid: {len(candidates)} combos | short={allow_short} | time_exit_bars={args.time_exit_bars}")

    best_by_symbol = {}
    for sym, csv_path in csv_paths.items():
        candles = load_csv_candles(csv_path)
        best = None
        for (enter_score, sl_atr, tp_atr) in candidates:
            res = backtest_one(
                sym,
                candles,
                fee=args.fee,
                slip=args.slip,
                enter_score=int(enter_score),
                ema_fast_n=args.ema_fast,
                ema_slow_n=args.ema_slow,
                rsi_n=args.rsi,
                atr_n=args.atr,
                sl_atr=float(sl_atr),
                tp_atr=float(tp_atr),
                time_exit_bars=int(args.time_exit_bars),
                allow_short=allow_short,
                seed_db=args.seed_db,
            )
            if "error" in res:
                best = res
                break
            # choose best by balance then winrate
            if best is None or (res["balance_pct"], res["winrate"]) > (best["balance_pct"], best["winrate"]):
                best = res
        best_by_symbol[sym] = best
        if "error" in best:
            print(f"⚠️ {sym}: {best['error']}")
        else:
            p = best["params"]
            print(f"🏆 {sym}: trades={best['trades']} win={best['winrate']}% bal={best['balance_pct']}%  params={p}")

    print("\n===== TOP SUMMARY =====")
    ok = [v for v in best_by_symbol.values() if v and "error" not in v]
    ok.sort(key=lambda x: (x["balance_pct"], x["winrate"]), reverse=True)
    for r in ok:
        p = r["params"]
        print(f"{r['symbol']:10s} bal={r['balance_pct']:7.2f}% win={r['winrate']:6.2f}% trades={r['trades']:4d}  "
              f"enter={p['enter_score']} sl={p['sl_atr']} tp={p['tp_atr']} short={p['short']} timeBars={p['time_exit_bars']}")

    if args.seed_db:
        print("\n✅ seed-db ON: backtest trade outcomes were sent to coin_stats (wins/losses).")

if __name__ == "__main__":
    main()
