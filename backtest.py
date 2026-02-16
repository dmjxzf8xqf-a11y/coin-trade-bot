import csv
import math
import argparse

# ========= CSV 로드 =========
def load_csv(path):
    candles = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            candles.append({
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"])
            })
    return candles


# ========= EMA =========
def ema(values, period):
    result = []
    k = 2 / (period + 1)
    ema_prev = values[0]
    for v in values:
        ema_prev = v * k + ema_prev * (1 - k)
        result.append(ema_prev)
    return result


# ========= RSI =========
def rsi(values, period=14):
    gains = []
    losses = []
    rsis = []

    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

        if i < period:
            rsis.append(50)
            continue

        avg_gain = sum(gains[i-period:i]) / period
        avg_loss = sum(losses[i-period:i]) / period

        if avg_loss == 0:
            rsis.append(100)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100 - (100 / (1 + rs)))

    rsis.insert(0, 50)
    return rsis


# ========= ATR =========
def atr(candles, period=14):
    trs = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i-1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    atrs = []
    for i in range(len(trs)):
        if i < period:
            atrs.append(trs[i])
        else:
            atrs.append(sum(trs[i-period:i]) / period)

    atrs.insert(0, atrs[0])
    return atrs


# ========= 백테스트 =========
def backtest(candles, fee=0.0006, slip=0.0005):
    closes = [c["close"] for c in candles]

    ema_fast = ema(closes, 9)
    ema_slow = ema(closes, 21)
    rsis = rsi(closes, 14)
    atrs = atr(candles, 14)

    position = None
    wins = 0
    losses = 0
    balance = 1.0  # 1 = 100%

    for i in range(30, len(candles)):
        price = closes[i]
        atr_val = atrs[i]

        trend_up = ema_fast[i] > ema_slow[i]
        trend_dn = ema_fast[i] < ema_slow[i]

        score = 0

        if trend_up:
            score += 40
        if 45 <= rsis[i] <= 65:
            score += 30
        if atr_val / price > 0.002:
            score += 20

        # ===== 진입 =====
        if position is None and score >= 60:
            entry = price * (1 + slip)
            sl = entry - atr_val * 1.5
            tp = entry + atr_val * 2
            position = ("LONG", entry, sl, tp)

        # ===== 청산 =====
        if position:
            side, entry, sl, tp = position

            if candles[i]["low"] <= sl:
                pnl = (sl - entry) / entry
                balance *= (1 + pnl - fee)
                losses += 1
                position = None

            elif candles[i]["high"] >= tp:
                pnl = (tp - entry) / entry
                balance *= (1 + pnl - fee)
                wins += 1
                position = None

    total = wins + losses
    winrate = (wins / total * 100) if total else 0

    print("\n===== BACKTEST RESULT =====")
    print("Trades:", total)
    print("Wins:", wins)
    print("Losses:", losses)
    print("Winrate:", round(winrate, 2), "%")
    print("Balance:", round(balance * 100, 2), "%")
    print("===========================\n")


# ========= 실행 =========
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    args = parser.parse_args()

    candles = load_csv(args.csv)
    backtest(candles)
