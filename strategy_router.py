def select_strategy(regime: str):
    """
    regime:
    bull  = 상승 추세
    bear  = 하락 추세
    range = 횡보
    volatile = 고변동
    """

    if regime == "bull":
        return "trend"

    if regime == "bear":
        return "short_trend"

    if regime == "range":
        return "mean_reversion"

    if regime == "volatile":
        return "low_risk"

    return "trend"
