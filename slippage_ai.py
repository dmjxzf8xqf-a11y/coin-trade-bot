# slippage_ai.py
import statistics

_slippage_history = []

def record_slippage(expected_price, actual_price):
    try:
        expected_price = float(expected_price)
        actual_price = float(actual_price)
        if expected_price <= 0:
            return
        slip = abs(actual_price - expected_price) / expected_price
        _slippage_history.append(slip)
        if len(_slippage_history) > 100:
            _slippage_history.pop(0)
    except Exception:
        return

def avg_slippage():
    if not _slippage_history:
        return 0.0
    return statistics.mean(_slippage_history)

def get_slippage_factor():
    avg = avg_slippage()

    if avg >= 0.003:
        return 0.50
    elif avg >= 0.002:
        return 0.70
    elif avg >= 0.001:
        return 0.85
    else:
        return 1.00

def is_slippage_too_high(threshold=0.004):
    return avg_slippage() >= float(threshold)
