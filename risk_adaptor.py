# risk_adaptor.py

def adjust_risk(base_risk, daily_pnl):
    if daily_pnl > 0:
        return base_risk * 1.2
    elif daily_pnl < -2:
        return base_risk * 0.7
    return base_risk
