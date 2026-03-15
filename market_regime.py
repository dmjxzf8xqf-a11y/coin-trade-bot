from typing import List, Dict, Any


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


def adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 2:
        return 0.0

    tr_list: List[float] = []
    plus_dm: List[float] = []
    minus_dm: List[float] = []

    for i in range(1, len(closes)):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)

    if len(tr_list) < period:
        return 0.0

    atr_sm = sum(tr_list[:period]) / period
    plus_sm = sum(plus_dm[:period]) / period
    minus_sm = sum(minus_dm[:period]) / period
    dx_values: List[float] = []

    for i in range(period, len(tr_list)):
        atr_sm = ((atr_sm * (period - 1)) + tr_list[i]) / period
        plus_sm = ((plus_sm * (period - 1)) + plus_dm[i]) / period
        minus_sm = ((minus_sm * (period - 1)) + minus_dm[i]) / period

        if atr_sm <= 0:
            continue
        plus_di = 100.0 * (plus_sm / atr_sm)
        minus_di = 100.0 * (minus_sm / atr_sm)
        denom = plus_di + minus_di
        if denom <= 0:
            dx_values.append(0.0)
        else:
            dx_values.append(100.0 * abs(plus_di - minus_di) / denom)

    if not dx_values:
        return 0.0
    tail = dx_values[-period:] if len(dx_values) >= period else dx_values
    return sum(tail) / len(tail)


def detect_market_regime_from_ohlcv(ohlcv) -> str:
    if not ohlcv or len(ohlcv) < 120:
        return "sideways"

    closes = [float(x[4]) for x in ohlcv]
    highs = [float(x[2]) for x in ohlcv]
    lows = [float(x[3]) for x in ohlcv]

    price = closes[-1]
    ef = ema(closes[-30:], 10)
    es = ema(closes[-60:], 20)
    el = ema(closes[-120:], 50)
    a = atr(highs, lows, closes, 14)
    d = adx(highs, lows, closes, 14)

    atr_pct = (a / price) if price > 0 else 0.0
    ema_gap_pct = (abs(ef - es) / price) if price > 0 else 0.0
    slope = ((es - el) / el) if el > 0 else 0.0

    if atr_pct >= 0.04:
        return "high_volatility"

    if d < 18 or atr_pct < 0.003 or ema_gap_pct < 0.0015:
        return "sideways"

    bullish = price > es and ef > es and es > el
    bearish = price < es and ef < es and es < el

    if bullish and slope > 0.003:
        return "bull_strong"
    if bearish and slope < -0.003:
        return "bear_strong"

    return "sideways"


def get_regime_metrics_from_ohlcv(ohlcv) -> Dict[str, Any]:
    if not ohlcv or len(ohlcv) < 120:
        return {
            "regime": "sideways",
            "adx": 0.0,
            "atr_pct": 0.0,
            "ema_gap_pct": 0.0,
            "slope": 0.0,
        }

    closes = [float(x[4]) for x in ohlcv]
    highs = [float(x[2]) for x in ohlcv]
    lows = [float(x[3]) for x in ohlcv]
    price = closes[-1]
    ef = ema(closes[-30:], 10)
    es = ema(closes[-60:], 20)
    el = ema(closes[-120:], 50)
    a = atr(highs, lows, closes, 14)
    d = adx(highs, lows, closes, 14)

    atr_pct = (a / price) if price > 0 else 0.0
    ema_gap_pct = (abs(ef - es) / price) if price > 0 else 0.0
    slope = ((es - el) / el) if el > 0 else 0.0

    return {
        "regime": detect_market_regime_from_ohlcv(ohlcv),
        "adx": round(d, 4),
        "atr_pct": round(atr_pct, 6),
        "ema_gap_pct": round(ema_gap_pct, 6),
        "slope": round(slope, 6),
    }
