import json

def analyze():
    with open("trade_log.json") as f:
        logs = json.load(f)

    wins = [x for x in logs if x["pnl"] > 0]
    losses = [x for x in logs if x["pnl"] <= 0]

    avg_win_score = sum(x["score"] for x in wins)/len(wins)
    avg_loss_score = sum(x["score"] for x in losses)/len(losses)

    return {
        "best_score_zone": avg_win_score,
        "danger_score_zone": avg_loss_score,
    }
