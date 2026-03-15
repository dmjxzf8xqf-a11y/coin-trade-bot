# risk_adaptor.py
def adapt_position_size(base_usdt: float, enter_score: float, regime: str) -> float:
    size = float(base_usdt)
    if regime == "high_volatility":
        size *= 0.45
    elif regime == "sideways":
        size *= 0.80
    elif regime in ("bull_strong", "bear_strong"):
        size *= 1.00
    if enter_score >= 85:
        size *= 1.20
    elif enter_score >= 75:
        size *= 1.00
    elif enter_score >= 68:
        size *= 0.75
    else:
        size *= 0.50
    size = max(size, base_usdt * 0.30)
    size = min(size, base_usdt * 1.25)
    return round(size, 4)
