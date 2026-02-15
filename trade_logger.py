import json
from datetime import datetime

FILE = "trade_log.json"

def log_trade(data):
    try:
        with open(FILE, "r") as f:
            logs = json.load(f)
    except:
        logs = []

    logs.append(data)

    with open(FILE, "w") as f:
        json.dump(logs[-500:], f, indent=2)
