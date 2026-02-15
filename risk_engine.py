def calc_position_size(balance, risk_pct, entry, stop, leverage):
    """
    balance   : 계좌 잔고
    risk_pct  : 한 거래 리스크 (%)
    entry     : 진입 가격
    stop      : 손절 가격
    leverage  : 레버리지
    """

    risk_amount = balance * (risk_pct / 100)

    stop_distance = abs(entry - stop)
    if stop_distance == 0:
        return 0

    qty = risk_amount / stop_distance

    # 레버리지 반영
    qty = qty * leverage

    return qty
