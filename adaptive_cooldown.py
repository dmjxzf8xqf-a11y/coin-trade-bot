# adaptive_cooldown.py

def adaptive_cooldown(loss_streak):
    if loss_streak >= 5:
        return 1800   # 30분
    elif loss_streak >= 3:
        return 600    # 10분
    else:
        return 0
