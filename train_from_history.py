# train_from_history.py
# 사용 예:
#   python train_from_history.py BTCUSDT 15 120
#   python train_from_history.py ETHUSDT 15 180
#
# 결과:
# - bt_data/ 아래 캔들 캐시
# - bt_logs/ 아래 상세로그(ENTER/EXIT jsonl)
# - tune_recommend.json 생성(트레이더에 붙일 값)
import os, json, sys
from bybit_data import download_history, load_cached
from backtest_engine import Params, simulate
from optimizer import grid_search

LOG_DIR = os.getenv("BT_LOG_DIR", "bt_logs")
os.makedirs(LOG_DIR, exist_ok=True)

def main():
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
    interval = (sys.argv[2] if len(sys.argv) > 2 else "15")
    days = int(sys.argv[3] if len(sys.argv) > 3 else "120")

    candles = download_history(symbol, interval, days=days)
    candles = load_cached(symbol, interval)

    # ✅ Walk-forward로 파라미터 선택(과최적화 완화)
    best, top10 = grid_search(candles, allow_long=True, allow_short=True, notional_usdt=100.0)

    # ✅ 선택된 best로 전체구간 상세로그 생성 (학습/분석용)
    p = Params(**best["params"])
    log_path = os.path.join(LOG_DIR, f"{symbol}_{interval}_{days}d_best.jsonl")
    full = simulate(candles, p, notional_usdt=100.0, log_path=log_path)

    out = {
        "symbol": symbol,
        "interval": interval,
        "days": days,
        "best_params": best["params"],
        "best_stability": best["stability"],
        "full_period_result": full,
        "top10": top10,
        "log_path": log_path,
    }
    with open("tune_recommend.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("✅ DONE -> tune_recommend.json")
    print("BEST:", best["params"])
    print("FULL:", full)
    print("LOG:", log_path)

if __name__ == "__main__":
    main()
