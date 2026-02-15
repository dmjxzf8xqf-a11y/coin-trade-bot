# backtest_engine.py
# ✅ 상급 백테스트 엔진(룩어헤드 방지 + 비용 반영 + 성과지표)
# candle 포맷: {"ts","open","high","low","close","volume"}
#
# 핵심 포인트:
# - 진입 신호가 i번째 캔들 close에서 나와도, "체결"은 다음 캔들 open에서 한다(룩어헤드 방지)
# - TP/SL 판정은 체결 이후 캔들의 high/low로 체크
# - fee/slippage 반영
#
# 사용 예:
#   from backtest_engine import run_backtest
#   result = run_backtest(candles, signal_fn=my_signal_fn, fee_rate=0.0006, slippage_bps=5)

from typing import Dict, List, Callable, Optional, Any
import math

Candle = Dict[str, float]

def _cost_frac(fee_rate: float, slippage_bps: float) -> float:
    # round-trip 비용(진입+청산)
    slip = (slippage_bps / 10000.0)
    return (2 * fee_rate) + (2 * slip)

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))

def compute_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"trades": 0, "winrate": 0.0, "profit_factor": 0.0, "expectancy": 0.0, "max_drawdown": 0.0}

    pnl = [t["pnl"] for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]

    winrate = (len(wins) / len(trades)) * 100.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    expectancy = sum(pnl) / len(trades)

    # max drawdown (equity curve)
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for x in pnl:
        eq += x
        peak = max(peak, eq)
        dd = peak - eq
        mdd = max(mdd, dd)

    return {
        "trades": len(trades),
        "winrate": round(winrate, 2),
        "wins": len(wins),
        "losses": len(losses),
        "profit_factor": (profit_factor if profit_factor != float("inf") else 9999.0),
        "expectancy": round(expectancy, 6),
        "net_pnl": round(sum(pnl), 6),
        "max_drawdown": round(mdd, 6),
    }

def run_backtest(
    candles: List[Candle],
    signal_fn: Callable[[float], Dict[str, Any]],
    # signal_fn(price_close) -> {"ok":bool,"side":"LONG"/"SHORT","sl":float,"tp":float,"score":int,"reason":str}
    notional_usdt: float = 100.0,
    fee_rate: float = 0.0006,
    slippage_bps: float = 5.0,
    cooldown_bars: int = 0,
) -> Dict[str, Any]:
    if not candles or len(candles) < 3:
        return {"trades": 0, "winrate": 0.0, "wins": 0, "losses": 0, "trades_detail": []}

    cost = _cost_frac(fee_rate, slippage_bps)
    trades: List[Dict[str, Any]] = []

    in_pos = False
    side = None
    entry = None
    sl = None
    tp = None
    cd = 0

    # i: 신호는 i의 close로 생성, 체결은 i+1 open
    for i in range(0, len(candles) - 2):
        c = candles[i]
        c_next = candles[i + 1]
        c_after = candles[i + 2]

        if cd > 0:
            cd -= 1

        if not in_pos:
            if cd > 0:
                continue

            price_close = float(c["close"])
            sig = signal_fn(price_close) or {}
            if not sig.get("ok"):
                continue

            side = sig.get("side", "LONG")
            sl = sig.get("sl")
            tp = sig.get("tp")
            if sl is None or tp is None:
                continue

            # 체결은 다음 캔들 open
            entry = float(c_next["open"])
            in_pos = True
            continue

        # 포지션 관리: 현재 캔들(c_next)의 high/low로 히트 체크 (체결 이후부터)
        hi = float(c_next["high"])
        lo = float(c_next["low"])

        hit_tp = False
        hit_sl = False

        if side == "LONG":
            if hi >= float(tp): hit_tp = True
            if lo <= float(sl): hit_sl = True
        else:
            if lo <= float(tp): hit_tp = True
            if hi >= float(sl): hit_sl = True

        # 같은 봉에서 TP/SL 둘 다 닿는 경우: 보수적으로 SL 우선
        exit_price = None
        outcome = None
        if hit_sl and hit_tp:
            exit_price = float(sl)
            outcome = "SL_FIRST"
        elif hit_sl:
            exit_price = float(sl)
            outcome = "SL"
        elif hit_tp:
            exit_price = float(tp)
            outcome = "TP"

        if exit_price is None:
            # 아직 미청산 -> 다음 루프로
            continue

        # pnl 계산 (notional 기준, 비용 반영)
        move = (exit_price - entry) / entry
        if side == "SHORT":
            move = -move
        gross = notional_usdt * move
        net = gross - (notional_usdt * cost)

        trades.append({
            "side": side,
            "entry": entry,
            "exit": exit_price,
            "outcome": outcome,
            "pnl": net,
        })

        # 포지션 리셋
        in_pos = False
        side = None
        entry = None
        sl = None
        tp = None
        cd = int(max(0, cooldown_bars))

    metrics = compute_metrics(trades)
    metrics["trades_detail"] = trades[-50:]  # 너무 길어지면 최근 50개만
    return metrics
