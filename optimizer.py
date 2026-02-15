# optimizer.py
# - Train/Test 시간 분할 + Walk-Forward
# - grid search
# - "수익만"이 아니라 DD/거래수/안정성까지 같이 점수화(과최적화 완화)
import os
from dataclasses import asdict
from backtest_engine import Params, simulate

def split_walkforward(candles, n_folds=4):
    """
    시간순으로 fold 분할:
    fold k: train=앞부분, test=그 다음 구간
    """
    L = len(candles)
    step = L // (n_folds + 1)
    folds = []
    for k in range(1, n_folds+1):
        train_end = step * k
        test_end = step * (k+1)
        train = candles[:train_end]
        test = candles[train_end:test_end]
        if len(train) > 200 and len(test) > 200:
            folds.append((train, test))
    return folds

def score_result(r):
    """
    과최적화 방지용 스코어:
    - pnl 높을수록 +
    - max_dd 높을수록 -
    - trades 너무 적으면 패널티
    """
    pnl = float(r["pnl"])
    dd = float(r["max_dd"])
    trades = int(r["trades"])
    if trades < 15:
        return -999999  # 너무 적게 벌면 의미 없음
    # dd가 pnl보다 커지면 폭망이니까 강하게 벌점
    return pnl - (dd * 1.3)

def grid_search(candles, allow_long=True, allow_short=True, notional_usdt=100.0):
    folds = split_walkforward(candles, n_folds=4)
    if not folds:
        raise RuntimeError("Not enough candles for walk-forward")

    # ✅ 그리드는 너무 넓히면 과최적화 + 느림
    enter_scores = [55, 60, 65, 70, 75]
    stop_atrs = [1.2, 1.5, 1.8, 2.1]
    tp_rs = [1.2, 1.5, 2.0]

    best = None
    all_rows = []

    for es in enter_scores:
        for sa in stop_atrs:
            for tr in tp_rs:
                p = Params(enter_score=es, stop_atr=sa, tp_r=tr, allow_long=allow_long, allow_short=allow_short)
                fold_scores = []
                fold_detail = []
                for (train, test) in folds:
                    # train 성능(참고용)
                    r_train = simulate(train, p, notional_usdt=notional_usdt, log_path=None)
                    # test 성능(진짜 평가)
                    r_test = simulate(test, p, notional_usdt=notional_usdt, log_path=None)
                    fold_scores.append(score_result(r_test))
                    fold_detail.append({"train": r_train, "test": r_test})

                # ✅ “평균”만 보지 말고 “최악”도 같이 봄 (안정성)
                avg_s = sum(fold_scores) / len(fold_scores)
                worst_s = min(fold_scores)

                stability = worst_s * 0.6 + avg_s * 0.4  # 최악을 더 중요시
                row = {"params": asdict(p), "avg_score": avg_s, "worst_score": worst_s, "stability": stability}
                all_rows.append(row)

                if (best is None) or (stability > best["stability"]):
                    best = row

    # 정렬해서 상위 10개 리턴
    all_rows.sort(key=lambda x: x["stability"], reverse=True)
    return best, all_rows[:10]
