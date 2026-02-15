# backtest_engine.py
import math
from backstage_logger import BackstageLogger

# ===== Indicators (trader.py와 동일 계열) =====
def ema(data, p):
    k = 2/(p+1)
    e = data[0]
    for v in data[1:]:
        e = v*k + e*(1-k)
    return e

def rsi(data, p=14):
    if len(data) < p + 1:
        return None
    gain=loss=0.0
    for i in range(-p,0):
        diff=data[i]-data[i-1]
        if diff>0: gain+=diff
        else: loss-=diff
    rs=gain/(loss+1e-9)
    return 100-(100/(1+rs))

def atr(high, low, close, p=14):
    if len(close) < p + 1:
        return None
    trs=[]
    for i in range(-p,0):
        trs.append(max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1])))
    return sum(trs)/p

def ai_score(price, ef, es, r, a):
    score=0
    if price>es: score+=25
    if price>ef: score+=20
    if r is not None and 45<r<65: score+=20
    if ef>es: score+=20
    if (a/price)<0.02: score+=15
    return int(score)

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))

def max_drawdown(equity_curve):
    peak = -1e18
    mdd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd

def compute_signal_from_candles(candles, i, side, cfg):
    """
    candles[0..i]까지 보고 진입 시그널 계산
    cfg: EMA_FAST, EMA_SLOW, RSI_PERIOD, ATR_PERIOD, enter_score, stop_atr, tp_r
    """
    EMA_FAST = cfg["EMA_FAST"]
    EMA_SLOW = cfg["EMA_SLOW"]
    RSI_PERIOD = cfg["RSI_PERIOD"]
    ATR_PERIOD = cfg["ATR_PERIOD"]

    # 최소 데이터
    need = max(EMA_SLOW*3, 120)
    if i < need:
        return {"ok": False, "reason": "kline 부족"}

    closes = [c["close"] for c in candles[:i+1]]
    highs  = [c["high"]  for c in candles[:i+1]]
    lows   = [c["low"]   for c in candles[:i+1]]

    price = closes[-1]
    ef = ema(closes[-EMA_FAST*3:], EMA_FAST)
    es = ema(closes[-EMA_SLOW*3:], EMA_SLOW)
    r = rsi(closes, RSI_PERIOD) or 50.0
    a = atr(highs, lows, closes, ATR_PERIOD) or (price * 0.005)

    # 변동성 필터
    if a/price < cfg["VOL_MIN"]:
        return {"ok": False, "reason": "LOW VOL", "score": 0, "atr": a}
    if a/price > cfg["VOL_MAX"]:
        return {"ok": False, "reason": "EXTREME VOL", "score": 0, "atr": a}

    score = ai_score(price, ef, es, r, a)
    enter_ok = score >= cfg["enter_score"]

    if side == "LONG":
        trend_ok = (price > es) and (ef > es)
    else:
        trend_ok = (price < es) and (ef < es)

    ok = enter_ok and trend_ok

    stop_dist = a * cfg["stop_atr"]
    tp_dist   = stop_dist * cfg["tp_r"]
    sl = price - stop_dist if side == "LONG" else price + stop_dist
    tp = price + tp_dist   if side == "LONG" else price - tp_dist

    return {
        "ok": ok,
        "score": score,
        "price": price,
        "sl": sl,
        "tp": tp,
        "atr": a,
        "reason": f"score={score} enter_ok={enter_ok} trend_ok={trend_ok} ef={ef:.6f} es={es:.6f} rsi={r:.2f} atr={a:.6f}",
    }

def simulate(
    symbol: str,
    candles: list,
    *,
    mode="AGGRO",
    # strategy params
    EMA_FAST=20, EMA_SLOW=50, RSI_PERIOD=14, ATR_PERIOD=14,
    enter_score=55,
    stop_atr=1.3,
    tp_r=2.0,
    allow_long=True,
    allow_short=True,
    # exits
    trail_on=True,
    trail_atr_mult=1.0,
    partial_tp_on=True,
    partial_tp_pct=0.5,      # 0.5 = 50% 청산
    tp1_fraction=0.5,        # tp까지 거리의 50% 지점이 TP1
    move_stop_to_be=True,
    time_exit_bars=24*4,     # 15m 기준 24h=96 bars, 여기 기본 6h=24*4=96? (원하면 바꿔)
    # risk/cost
    initial_equity=1000.0,
    order_usdt=12.0,
    leverage=8,
    fee_rate=0.0006,         # side
    slippage_bps=5.0,        # side
    # filters
    VOL_MIN=0.002,
    VOL_MAX=0.06,
    # logging
    backstage_path="backstage_log.jsonl",
    backstage=True,
):
    """
    단일심볼 백테스트 (캔들 기반)
    - 포지션 1개만 보유(실제 멀티포지션까지 확장 가능)
    """
    log = BackstageLogger(backstage_path, enabled=backstage)

    cfg = {
        "EMA_FAST": EMA_FAST, "EMA_SLOW": EMA_SLOW,
        "RSI_PERIOD": RSI_PERIOD, "ATR_PERIOD": ATR_PERIOD,
        "enter_score": enter_score,
        "stop_atr": stop_atr,
        "tp_r": tp_r,
        "VOL_MIN": VOL_MIN,
        "VOL_MAX": VOL_MAX,
    }

    def round_trip_cost_frac():
        slip = (slippage_bps / 10000.0)
        return (2*fee_rate) + (2*slip)

    equity = float(initial_equity)
    eq_curve = [equity]

    pos = None
    trades = []
    wins = losses = 0

    for i in range(len(candles)):
        c = candles[i]
        price = float(c["close"])

        # ===== manage position =====
        if pos is not None:
            side = pos["side"]
            entry = pos["entry_price"]

            # trailing
            if trail_on and pos["atr"] is not None:
                dist = pos["atr"] * trail_atr_mult
                if side == "LONG":
                    cand_stop = price - dist
                    if pos["trail"] is None or cand_stop > pos["trail"]:
                        pos["trail"] = cand_stop
                else:
                    cand_stop = price + dist
                    if pos["trail"] is None or cand_stop < pos["trail"]:
                        pos["trail"] = cand_stop

            eff_stop = pos["sl"]
            if pos["trail"] is not None:
                eff_stop = max(eff_stop, pos["trail"]) if side == "LONG" else min(eff_stop, pos["trail"])

            # time exit
            if (i - pos["entry_i"]) >= int(time_exit_bars):
                exit_price = price
                why = "TIME_EXIT"
                pnl = _pnl_usdt(side, entry, exit_price, order_usdt, leverage, fee_rate, slippage_bps)
                equity += pnl
                trades.append({"side": side, "entry": entry, "exit": exit_price, "pnl": pnl, "why": why})
                log.log("EXIT", {"symbol": symbol, "i": i, "why": why, "side": side, "entry": entry, "exit": exit_price, "pnl": pnl, "equity": equity})
                pos = None
                eq_curve.append(equity)
                continue

            # partial TP
            if partial_tp_on and (not pos["tp1_done"]) and (pos["tp1"] is not None):
                hit_tp1 = (c["high"] >= pos["tp1"]) if side == "LONG" else (c["low"] <= pos["tp1"])
                if hit_tp1:
                    # 부분익절: 포지션 크기의 partial_tp_pct만큼 실현
                    # 단순화: notional의 partial만큼 pnl 실현
                    exit_price = pos["tp1"]
                    pnl_part = _pnl_usdt(side, entry, exit_price, order_usdt*partial_tp_pct, leverage, fee_rate, slippage_bps)
                    equity += pnl_part
                    pos["tp1_done"] = True
                    log.log("TP1", {"symbol": symbol, "i": i, "side": side, "entry": entry, "tp1": exit_price, "pnl_part": pnl_part, "equity": equity})
                    if move_stop_to_be:
                        pos["sl"] = max(pos["sl"], entry) if side == "LONG" else min(pos["sl"], entry)

            # stop / trail hit
            stop_hit = (c["low"] <= eff_stop) if side == "LONG" else (c["high"] >= eff_stop)
            if stop_hit:
                exit_price = eff_stop
                why = "STOP/TRAIL"
                # 남은 물량: (1 - partial_tp_pct) if tp1_done else 1.0
                remain_frac = (1.0 - partial_tp_pct) if (partial_tp_on and pos["tp1_done"]) else 1.0
                pnl = _pnl_usdt(side, entry, exit_price, order_usdt*remain_frac, leverage, fee_rate, slippage_bps)
                equity += pnl
                trades.append({"side": side, "entry": entry, "exit": exit_price, "pnl": pnl, "why": why})
                log.log("EXIT", {"symbol": symbol, "i": i, "why": why, "side": side, "entry": entry, "exit": exit_price, "pnl": pnl, "equity": equity})
                wins += 1 if pnl >= 0 else 0
                losses += 1 if pnl < 0 else 0
                pos = None
                eq_curve.append(equity)
                continue

            # take profit hit
            tp_hit = (c["high"] >= pos["tp"]) if side == "LONG" else (c["low"] <= pos["tp"])
            if tp_hit:
                exit_price = pos["tp"]
                why = "TAKE_PROFIT"
                remain_frac = (1.0 - partial_tp_pct) if (partial_tp_on and pos["tp1_done"]) else 1.0
                pnl = _pnl_usdt(side, entry, exit_price, order_usdt*remain_frac, leverage, fee_rate, slippage_bps)
                equity += pnl
                trades.append({"side": side, "entry": entry, "exit": exit_price, "pnl": pnl, "why": why})
                log.log("EXIT", {"symbol": symbol, "i": i, "why": why, "side": side, "entry": entry, "exit": exit_price, "pnl": pnl, "equity": equity})
                wins += 1 if pnl >= 0 else 0
                losses += 1 if pnl < 0 else 0
                pos = None
                eq_curve.append(equity)
                continue

            # hold
            log.log("HOLD", {"symbol": symbol, "i": i, "side": side, "price": price, "stop": eff_stop, "tp": pos["tp"], "equity": equity})
            continue

        # ===== no position: entry =====
        best = None

        if allow_long:
            sL = compute_signal_from_candles(candles, i, "LONG", cfg)
            if sL.get("ok"):
                best = {"side": "LONG", **sL}

        if allow_short:
            sS = compute_signal_from_candles(candles, i, "SHORT", cfg)
            if sS.get("ok"):
                if (best is None) or (sS.get("score", 0) > best.get("score", 0)):
                    best = {"side": "SHORT", **sS}

        if best is None:
            log.log("SKIP", {"symbol": symbol, "i": i, "price": price})
            continue

        side = best["side"]
        sl = float(best["sl"])
        tp = float(best["tp"])
        a  = float(best.get("atr") or (price*0.005))

        tp1 = None
        if partial_tp_on:
            if side == "LONG":
                tp1 = price + (tp - price) * float(tp1_fraction)
            else:
                tp1 = price - (price - tp) * float(tp1_fraction)

        pos = {
            "side": side,
            "entry_price": price,
            "entry_i": i,
            "sl": sl,
            "tp": tp,
            "tp1": tp1,
            "tp1_done": False,
            "trail": None,
            "atr": a,
        }
        log.log("ENTER", {"symbol": symbol, "i": i, "side": side, "entry": price, "sl": sl, "tp": tp, "tp1": tp1, "score": best.get("score"), "reason": best.get("reason")})

    # ===== summary =====
    total = len([t for t in trades if t.get("why") in ("STOP/TRAIL","TAKE_PROFIT","TIME_EXIT")])
    winrate = (wins / total * 100.0) if total else 0.0
    mdd = max_drawdown(eq_curve)
    ret = (equity - initial_equity) / max(1e-9, initial_equity)

    return {
        "symbol": symbol,
        "mode": mode,
        "trades": total,
        "wins": wins,
        "losses": losses,
        "winrate": round(winrate, 2),
        "equity_start": initial_equity,
        "equity_end": round(equity, 4),
        "return_pct": round(ret * 100.0, 2),
        "max_drawdown_pct": round(mdd * 100.0, 2),
        "avg_pnl": round(sum(t["pnl"] for t in trades)/max(1, len(trades)), 4) if trades else 0.0,
        "params": {
            "EMA_FAST": EMA_FAST, "EMA_SLOW": EMA_SLOW, "RSI_PERIOD": RSI_PERIOD, "ATR_PERIOD": ATR_PERIOD,
            "enter_score": enter_score, "stop_atr": stop_atr, "tp_r": tp_r,
            "trail_on": trail_on, "trail_atr_mult": trail_atr_mult,
            "partial_tp_on": partial_tp_on, "partial_tp_pct": partial_tp_pct, "tp1_fraction": tp1_fraction,
            "time_exit_bars": int(time_exit_bars),
            "order_usdt": order_usdt, "leverage": leverage,
            "fee_rate": fee_rate, "slippage_bps": slippage_bps,
        },
    }

def _pnl_usdt(side, entry_price, exit_price, order_usdt, lev, fee_rate, slippage_bps):
    """
    단순 PnL (수수료+슬리피지 포함)
    - order_usdt: 증거금(USDT)
    - notional = order_usdt * lev
    """
    entry_price = float(entry_price)
    exit_price = float(exit_price)
    if entry_price <= 0 or order_usdt <= 0:
        return 0.0

    notional = float(order_usdt) * float(lev)
    move = (exit_price - entry_price) / entry_price
    if side == "SHORT":
        move = -move

    gross = notional * move
    slip = (float(slippage_bps) / 10000.0)
    cost = notional * ((2*float(fee_rate)) + (2*slip))
    return gross - cost
