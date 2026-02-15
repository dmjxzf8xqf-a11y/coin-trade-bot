# backtest_engine.py

from trader import compute_signal_and_exits, mode_params

def simulate(symbol, candles, mode="AGGRO"):
    mp = mode_params(mode)

    wins = 0
    losses = 0
    trades = 0

    position = None

    for candle in candles:
        price = candle["close"]

        # 포지션 없으면 진입 시도
        if position is None:
            ok, _, score, sl, tp, _ = compute_signal_and_exits(
                symbol, "LONG", price, mp
            )

            if ok:
                position = {
                    "entry": price,
                    "sl": sl,
                    "tp": tp
                }
                trades += 1
            continue

        # 포지션 보유 중
        if candle["low"] <= position["sl"]:
            losses += 1
            position = None
            continue

        if candle["high"] >= position["tp"]:
            wins += 1
            position = None
            continue

    total = wins + losses

    if total == 0:
        return {}

    return {
        "trades": trades,
        "winrate": round(wins / total * 100, 2),
        "wins": wins,
        "losses": losses,
    }
