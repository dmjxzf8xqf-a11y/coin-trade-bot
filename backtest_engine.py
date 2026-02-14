# backtest_engine.py

def simulate(trader, candles):
    wins = 0
    losses = 0

    for candle in candles:
        price = candle["close"]

        ok, _, score, sl, tp, _ = trader.compute_signal_and_exits("LONG", price)

        if not ok:
            continue

        if candle["high"] >= tp:
            wins += 1
        elif candle["low"] <= sl:
            losses += 1

    total = wins + losses

    if total == 0:
        return {}

    return {
        "trades": total,
        "winrate": round(wins / total * 100, 2),
        "wins": wins,
        "losses": losses
    }
