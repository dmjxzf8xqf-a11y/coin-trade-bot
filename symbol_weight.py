# symbol_weight.py

def score_symbol(stats):
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    total = wins + losses

    if total < 5:
        return 0.5

    winrate = wins / total

    if winrate > 0.6:
        return 1.3
    elif winrate < 0.4:
        return 0.7
    else:
        return 1.0
