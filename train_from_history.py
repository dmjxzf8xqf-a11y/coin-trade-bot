# ===== file: train_from_history.py =====
# ✅ 실행: python train_from_history.py BTCUSDT 15
import json
import sys
import time

from data_store import update_cache, load_candles
from walkforward import walk_forward
from optimizer import pick_best_params
from make_signal import make_signal_fn

OUT_FILE = "learn_state.json"

def ema(vals, p):
    k = 2/(p+1)
    e = vals[0]
    for v in vals[1:]:
        e = v*k + e*(1-k)
    return e

def rsi(vals, p=14):
    if len(vals) < p + 1:
        return None
    gain=loss=0.0
    for i in range(-p,0):
        d = vals[i]-vals[i-1]
        if d>0: gain+=d
        else: loss-=d
    rs = gain/(loss+1e-9)
    return 100-(100/(1+rs))

def atr(high, low, close, p=14):
    if len(close) < p + 1:
        return None
    trs=[]
    for i in range(-p,0):
        trs.append(max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1])))
    return sum(trs)/p

def ai_score_like(price, ef, es, r, a):
    score=0
    if price>es: score+=25
    if price>ef: score+=20
    if r is not None and 45<r<65: score+=20
    if ef>es: score+=20
    if (a/price)<0.02: score+=15
    return int(score)

def build_indicator_fn(candles, EMA_FAST=20, EMA_SLOW=50, RSI_PERIOD=14, ATR_PERIOD=14):
    closes = [float(c["close"]) for c in candles]
    highs  = [float(c["high"]) for c in candles]
    lows   = [float(c["low"])  for c in candles]

    def indicator_fn(price_close: float, idx: int):
        if idx < max(EMA_SLOW*3, 120) or idx >= len(candles):
            return {"ok": False}

        cl = closes[:idx+1]
        hi = highs[:idx+1]
        lo = lows[:idx+1]

        ef = ema(cl[-EMA_FAST*3:], EMA_FAST)
        es = ema(cl[-EMA_SLOW*3:], EMA_SLOW)
        r  = rsi(cl, RSI_PERIOD)
        a  = atr(hi, lo, cl, ATR_PERIOD)

        if r is None: r = 50.0
        if a is None or price_close <= 0:
            return {"ok": False}

        score = ai_score_like(price_close, ef, es, r, a)
        trend_ok_long = (price_close > es) and (ef > es)
        trend_ok_short = (price_close < es) and (ef < es)

        return {
            "ok": True,
            "score": score,
            "atr": float(a),
            "trend_ok_long": bool(trend_ok_long),
            "trend_ok_short": bool(trend_ok_short),
        }

    return indicator_fn

def save_result(payload):
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def main():
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
    interval = (sys.argv[2] if len(sys.argv) > 2 else "15")

    print(f"[TRAIN] update cache {symbol} interval={interval}")
    n = update_cache(symbol, interval, chunk_limit=1000, max_chunks=12)
    candles = load_candles(symbol, interval)
    print(f"[OK] candles={len(candles)} (cache={n})")

    if len(candles) < 6000:
        print("[WARN] candles too small. 학습은 되지만 워크포워드 신뢰도가 떨어짐.")

    indicator_fn = build_indicator_fn(candles)

    def _make_signal(params):
        return make_signal_fn(params, indicator_fn)

    def optimizer_fn(train_candles):
        # 단순화(전체 indicator 재사용)
        return pick_best_params(train_candles, make_signal_fn=_make_signal)

    def backtest_fn(test_candles, signal_fn):
        from backtest_engine import run_backtest
        return run_backtest(
            candles=test_candles,
            signal_fn=signal_fn,
            notional_usdt=100.0,
            fee_rate=0.0006,
            slippage_bps=5.0,
            cooldown_bars=0,
        )

    print("[TRAIN] walk-forward running...")
    wf = walk_forward(
        candles=candles,
        train_bars=4000,
        test_bars=1000,
        optimizer_fn=optimizer_fn,
        make_signal_fn=_make_signal,
        backtest_fn=backtest_fn,
    )

    last_best = wf["windows"][-1]["best_params"] if wf["windows"] else {}
    out = {
        "symbol": symbol,
        "interval": str(interval),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "walkforward_summary": wf.get("summary", {}),
        "last_best_params": last_best,
        "windows_count": len(wf.get("windows", [])),
    }
    save_result(out)

    print("[DONE] saved -> learn_state.json")
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()


