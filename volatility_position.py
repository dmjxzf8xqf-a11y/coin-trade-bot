# volatility_position.py

def adjust_position_size(base_usdt, atr_pct):
    """
    변동성 기반 포지션 크기 조절
    atr_pct: ATR / price
    """

    if atr_pct >= 0.03:      # 매우 위험
        return base_usdt * 0.5
    elif atr_pct >= 0.02:
        return base_usdt * 0.7
    elif atr_pct <= 0.008:   # 매우 안정
        return base_usdt * 1.3
    else:
        return base_usdt
