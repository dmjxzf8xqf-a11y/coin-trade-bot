# online_learning.py
import json
import os

STATE_FILE = "online_learning_state.json"

def _load():
    if not os.path.exists(STATE_FILE):
        return {"trades": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"trades": []}
        if "trades" not in data or not isinstance(data["trades"], list):
            data["trades"] = []
        return data
    except Exception:
        return {"trades": []}

def _save(data):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass

def record_trade(pnl_usdt):
    data = _load()
    trades = data.get("trades", [])
    trades.append(float(pnl_usdt))
    if len(trades) > 50:
        trades = trades[-50:]
    data["trades"] = trades
    _save(data)

def get_learning_factor():
    data = _load()
    trades = data.get("trades", [])

    if len(trades) < 5:
        return 1.0

    wins = sum(1 for x in trades if x > 0)
    losses = sum(1 for x in trades if x <= 0)
    winrate = wins / max(len(trades), 1)
    avg_pnl = sum(trades) / max(len(trades), 1)

    recent5 = trades[-5:]
    recent5_sum = sum(recent5)

    # 강한 방어
    if recent5_sum < -10:
        return 0.60
    if losses >= 4 and winrate < 0.40:
        return 0.75

    # 약한 방어
    if winrate < 0.45:
        return 0.90

    # 공격 강화
    if winrate >= 0.60 and avg_pnl > 0:
        return 1.10
    if winrate >= 0.70 and avg_pnl > 1:
        return 1.20

    return 1.0

def get_learning_state():
    data = _load()
    trades = data.get("trades", [])
    if not trades:
        return {
            "count": 0,
            "winrate": 0.0,
            "avg_pnl": 0.0,
            "factor": 1.0,
        }

    wins = sum(1 for x in trades if x > 0)
    winrate = wins / len(trades)
    avg_pnl = sum(trades) / len(trades)

    return {
        "count": len(trades),
        "winrate": round(winrate, 4),
        "avg_pnl": round(avg_pnl, 4),
        "factor": round(get_learning_factor(), 4),
    }
