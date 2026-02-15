import json

FILE = "coin_stats.json"

def _load():
    try:
        with open(FILE) as f:
            return json.load(f)
    except:
        return {}

def _save(data):
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2)

def record(symbol, pnl):
    data = _load()

    if symbol not in data:
        data[symbol] = {"wins":0, "losses":0}

    if pnl > 0:
        data[symbol]["wins"] += 1
    else:
        data[symbol]["losses"] += 1

    _save(data)

def winrate(symbol):
    data = _load()
    if symbol not in data:
        return 50

    w = data[symbol]["wins"]
    l = data[symbol]["losses"]

    total = w + l
    if total == 0:
        return 50

    return round(w/total*100,1)
