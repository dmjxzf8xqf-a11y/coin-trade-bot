# ===== file: make_signal.py =====
from typing import Dict, Any

def make_signal_fn(params: Dict[str, Any], indicator_fn):
    enter_score = int(params.get("enter_score", 65))
    stop_atr = float(params.get("stop_atr", 1.8))
    tp_r = float(params.get("tp_r", 1.5))
    side_mode = str(params.get("side", "BOTH")).upper()

    def signal_fn(price_close: float, idx: int):
        ind = indicator_fn(price_close, idx) or {}
        if not ind.get("ok"):
            return {"ok": False}

        atr = float(ind.get("atr") or 0.0)
        if atr <= 0 or price_close <= 0:
            return {"ok": False}

        score = int(ind.get("score") or 0)

        if side_mode in ("LONG", "SHORT"):
            side = side_mode
        else:
            side = "SHORT" if (ind.get("trend_ok_short") and not ind.get("trend_ok_long")) else "LONG"

        if score < enter_score:
            return {"ok": False, "score": score}

        if side == "LONG" and not ind.get("trend_ok_long"):
            return {"ok": False, "score": score}
        if side == "SHORT" and not ind.get("trend_ok_short"):
            return {"ok": False, "score": score}

        stop_dist = atr * stop_atr
        tp_dist = stop_dist * tp_r

        if side == "LONG":
            sl = price_close - stop_dist
            tp = price_close + tp_dist
        else:
            sl = price_close + stop_dist
            tp = price_close - tp_dist

        return {"ok": True, "side": side, "sl": float(sl), "tp": float(tp), "score": score}

    return signal_fn


# ===== file: optimizer.py =====
from typing import List, Dict, Any, Callable
from backtest_engine import run_backtest

def pick_best_params(
    train_candles: List[Dict],
    make_signal_fn: Callable[[Dict[str, Any]], Callable],
    fee_rate: float = 0.0006,
    slippage_bps: float = 5.0,
    notional_usdt: float = 100.0,
) -> Dict[str, Any]:
    enter_scores = [55, 60, 65, 70]
    stop_atrs    = [1.3, 1.6, 1.8, 2.0]
    tp_rs        = [1.2, 1.5, 2.0]
    sides        = ["BOTH"]

    best = None
    best_obj = -1e18

    for es in enter_scores:
        for sa in stop_atrs:
            for tr in tp_rs:
                for sd in sides:
                    params = {"enter_score": es, "stop_atr": sa, "tp_r": tr, "side": sd}
                    sig = make_signal_fn(params)

                    res = run_backtest(
                        candles=train_candles,
                        signal_fn=sig,
                        notional_usdt=notional_usdt,
                        fee_rate=fee_rate,
                        slippage_bps=slippage_bps,
                        cooldown_bars=0,
                    )

                    trades = int(res.get("trades", 0) or 0)
                    if trades < 30:
                        continue

                    net = float(res.get("net_pnl", 0.0) or 0.0)
                    wr = float(res.get("winrate", 0.0) or 0.0)
                    mdd = float(res.get("max_drawdown", 0.0) or 0.0)

                    obj = net + (wr * 0.3) - (mdd * 0.2)

                    if obj > best_obj:
                        best_obj = obj
                        best = {"params": params, "train_metrics": res, "objective": obj}

    return best or {"params": {"enter_score": 65, "stop_atr": 1.8, "tp_r": 1.5, "side": "BOTH"}, "train_metrics": {}, "objective": 0.0}


# ===== file: apply_to_trader_patch.py =====
import json
import os

LEARN_FILE = os.getenv("LEARN_FILE", "learn_state.json")

def load_learned_params():
    try:
        with open(LEARN_FILE, "r", encoding="utf-8") as f:
            j = json.load(f)
        return (j.get("last_best_params") or {})
    except Exception:
        return {}

def apply_to_trader(trader, target_mode="SAFE"):
    p = load_learned_params()
    if not p:
        return False, "no learned params"

    mode = str(target_mode).upper()
    if mode not in trader.tune:
        trader.tune[mode] = {}

    trader.tune[mode]["enter_score"] = int(p.get("enter_score", trader.tune[mode].get("enter_score", 65)))
    trader.tune[mode]["stop_atr"] = float(p.get("stop_atr", trader.tune[mode].get("stop_atr", 1.8)))
    trader.tune[mode]["tp_r"] = float(p.get("tp_r", trader.tune[mode].get("tp_r", 1.5)))

    try:
        trader._lev_set_cache = {}
    except Exception:
        pass

    return True, f"applied {mode}: enter_score={trader.tune[mode]['enter_score']} stop_atr={trader.tune[mode]['stop_atr']} tp_r={trader.tune[mode]['tp_r']}"
