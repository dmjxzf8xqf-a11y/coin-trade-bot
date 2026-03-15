from typing import List


def ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    out = values[0]
    for v in values[1:]:
        out = (v * k) + (out * (1 - k))
    return out


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return 0.0
    return sum(trs[-period:]) / period


def detect_market_regime_from_ohlcv(ohlcv):
    if not ohlcv or len(ohlcv) < 80:
        return "sideways"

    closes = [float(x[4]) for x in ohlcv]
    highs = [float(x[2]) for x in ohlcv]
    lows = [float(x[3]) for x in ohlcv]

    price = closes[-1]
    ef = ema(closes[-30:], 10)
    es = ema(closes[-60:], 20)
    el = ema(closes[-80:], 50)
    a = atr(highs, lows, closes, 14)
    atr_pct = (a / price) if price > 0 else 0.0

    if atr_pct >= 0.03:
        return "high_volatility"

    bullish = price > es and ef > es and es > el
    bearish = price < es and ef < es and es < el
    slope = ((es - el) / el) if el > 0 else 0.0

    if bullish and slope > 0.003:
        return "bull_strong"
    if bearish and slope < -0.003:
        return "bear_strong"
    return "sideways"
