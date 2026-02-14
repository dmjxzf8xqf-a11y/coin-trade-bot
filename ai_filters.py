# ai_filters.py

def atr_percent(atr, price):
    if price <= 0:
        return 0
    return (atr / price) * 100


def sideways_market(atr, price, rsi,
                    atr_min_pct=0.25,
                    rsi_low=45,
                    rsi_high=55):
    """
    횡보장 판단
    """
    atrp = atr_percent(atr, price)

    if atrp < atr_min_pct and rsi_low <= rsi <= rsi_high:
        return True

    return False


def mtf_bias(price, ema_fast, ema_slow):
    """
    상위 타임프레임 추세 방향
    """
    if ema_fast > ema_slow and price > ema_slow:
        return "LONG"

    if ema_fast < ema_slow and price < ema_slow:
        return "SHORT"

    return "BOTH"
