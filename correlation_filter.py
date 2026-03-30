# correlation_filter.py

CORRELATED = [
    ["BTCUSDT", "ETHUSDT"],
    ["BTCUSDT", "SOLUSDT"],
    ["ETHUSDT", "SOLUSDT"]
]

def is_correlated(symbol, open_positions):
    for group in CORRELATED:
        if symbol in group:
            for s in group:
                if s in open_positions:
                    return True
    return False
