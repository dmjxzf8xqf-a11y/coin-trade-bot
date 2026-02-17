from typing import Optional, Tuple

def spread_bps(bid: float, ask: float) -> Optional[float]:
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return ((ask - bid) / mid) * 10000.0

def is_liquid_ok(
    bid: float,
    ask: float,
    turnover24h: float,
    min_turnover24h_usdt: float,
    max_spread_bps: float,
) -> Tuple[bool, str]:
    """
    Bybit ticker 기반 간단한 유동성 필터.
    - turnover24h가 낮으면 스킵
    - 스프레드가 크면 스킵
    """
    if turnover24h <= 0 or turnover24h < float(min_turnover24h_usdt):
        return False, f"LIQ_BLOCK: turnover24h<{min_turnover24h_usdt}"
    sp = spread_bps(bid, ask)
    if sp is None:
        return False, "LIQ_BLOCK: spread NA"
    if sp > float(max_spread_bps):
        return False, f"LIQ_BLOCK: spread>{max_spread_bps}bps({sp:.1f})"
    return True, f"LIQ_OK: sp={sp:.1f}bps"
