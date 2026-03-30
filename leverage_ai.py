# leverage_ai.py
import math

def calc_leverage(volatility):
    """
    volatility: 0~1 값
    """

    if volatility < 0.008:
        return 20
    elif volatility < 0.015:
        return 15
    elif volatility < 0.025:
        return 10
    else:
        return 6