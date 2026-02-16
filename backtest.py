import csv
import math
import argparse

# =========================
# OPTIONAL AI LEARN IMPORT
# =========================
AI_LEARN_OK = False
try:
    from ai_learn import record_trade_result, update_result, load_state
    AI_LEARN_OK = True
except Exception:
    def record_trade_result(_pnl: float):  # fallback
        return None
    def update_result(_win: bool):
        return 60
    def load_state():
        return {"wins": 0, "losses": 0, "enter_score": 60}

# ========= CSV 로드 =========
def load_csv(path):
    candles = []
    with open(path, newline="") as f:
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

    atrs.insert(0, atrs[0] if atrs else 0.0)
    return atrs

# ========= 백테스트 =========
def backtest(candles, fee=0.0006, slip=0.0005, learn=False, learn_weight=0.5, use_learn_enter_score=True):
    closes = [c["close"] for c in candles]

    ema_fast = ema(closes, 9)
    ema_slow = ema(closes, 21)
    rsis = rsi(closes, 14)
    atrs = atr(candles, 14)

    position = None
    wins = 0
    losses = 0
    balance = 1.0  # 1 = 100%

    # ✅ learn_state.json의 enter_score를 진입 기준으로 쓰기
    enter_score_threshold = 60
    if use_learn_enter_score and AI_LEARN_OK:
        try:
            st = load_state()
            enter_score_threshold = int(st.get("enter_score", 60))
        except Exception:
            enter_score_threshold = 60

    def _learn_trade(pnl_frac: float, win: bool):
        # pnl_frac: 예) +0.01 = +1%, -0.02 = -2%
        if not learn or (not AI_LEARN_OK):
            return
        try:
            # ✅ 승률/트레이드수 기록 (ai_stats.json)
            record_trade_result(float(pnl_frac) * float(learn_weight))
        except Exception:
            pass
        try:
            # ✅ enter_score 자동 튜닝 (learn_state.json)
            new_score = update_result(bool(win))
            return int(new_score)
        except Exception:
            return None

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
        if position is None and score >= enter_score_threshold:
            entry = price * (1 + slip)
            sl = entry - atr_val * 1.5
            tp = entry + atr_val * 2
            position = ("LONG", entry, sl, tp)

        # ===== 청산 =====
        if position:
            side, entry, sl, tp = position

            # 손절
            if candles[i]["low"] <= sl:
                pnl = (sl - entry) / entry  # 음수
                # 비용(수수료) 반영: 너 원래대로 fee만 뺐는데, slip은 entry에 이미 반영됨
                balance *= (1 + pnl - fee)

                losses += 1
                position = None

                # ✅ 학습 연결
                new_score = _learn_trade(pnl - fee, win=False)
                if use_learn_enter_score and new_score is not None:
                    enter_score_threshold = new_score

            # 익절
            elif candles[i]["high"] >= tp:
                pnl = (tp - entry) / entry  # 양수
                balance *= (1 + pnl - fee)

                wins += 1
                position = None

                # ✅ 학습 연결
                new_score = _learn_trade(pnl - fee, win=True)
                if use_learn_enter_score and new_score is not None:
                    enter_score_threshold = new_score

    total = wins + losses
    winrate = (wins / total * 100) if total else 0

    print("\n===== BACKTEST RESULT =====")
    print("Trades:", total)
    print("Wins:", wins)
    print("Losses:", losses)
    print("Winrate:", round(winrate, 2), "%")
    print("Balance:", round(balance * 100, 2), "%")
    print("===========================\n")

    if learn:
        if AI_LEARN_OK:
            print(f"[LEARN] ✅ 백테스트 학습 반영됨 (weight={learn_weight})")
            print(f"[LEARN] 현재 enter_score_threshold={enter_score_threshold}")
        else:
            print("[LEARN] ❌ ai_learn.py import 실패라 학습 못함 (파일 경로/이름 확인)")

# ========= 실행 =========
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--fee", type=float, default=0.0006)
    parser.add_argument("--slip", type=float, default=0.0005)

    # ✅ 학습 옵션
    parser.add_argument("--learn", action="store_true", help="백테스트 결과를 ai_learn.py에 기록")
    parser.add_argument("--learn_weight", type=float, default=0.5, help="백테스트 학습 반영 가중치(0~1)")
    parser.add_argument("--use_learn_enter_score", action="store_true", help="learn_state.json enter_score를 진입 기준으로 사용")

    args = parser.parse_args()

    candles = load_csv(args.csv)
    backtest(
        candles,
        fee=args.fee,
        slip=args.slip,
        learn=args.learn,
        learn_weight=args.learn_weight,
        use_learn_enter_score=args.use_learn_enter_score
    )
