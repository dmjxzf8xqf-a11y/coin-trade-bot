def detect_regime(price, ema50, ema200, atr):
    if atr / price < 0.003:
        return "SIDEWAYS"

    if atr / price > 0.04:
        return "HIGH_VOL"

    if ema50 > ema200:
        return "BULL"

    return "BEAR"
