import json

FILE = "trade_log.json"

def analyze_patterns():
    try:
        with open(FILE) as f:
            logs = json.load(f)
    except:
        return None

    if len(logs) < 30:
        return None

    wins = [x for x in logs if x["pnl"] > 0]
    losses = [x for x in logs if x["pnl"] <= 0]

    if not wins or not losses:
        return None

    avg_win_score = sum(x.get("score", 0) for x in wins) / len(wins)
    avg_loss_score = sum(x.get("score", 0) for x in losses) / len(losses)

    avg_win_rsi = sum(x.get("rsi", 50) for x in wins) / len(wins)
    avg_loss_rsi = sum(x.get("rsi", 50) for x in losses) / len(losses)

    return {
        "best_score": avg_win_score,
        "danger_score": avg_loss_score,
        "best_rsi": avg_win_rsi,
        "danger_rsi": avg_loss_rsi,
    }
