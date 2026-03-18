import subprocess
import re

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]

ENTER = [50, 60, 70]
SL = [1.0, 1.2, 1.5]
TP = [1.0, 1.2, 1.5, 1.8]

results = []

for sym in SYMBOLS:
    for e in ENTER:
        for sl in SL:
            for tp in TP:
                try:
                    cmd = f"python run_backtest_opt.py --symbols {sym} --interval 15 --days 180 --enter_score {e} --sl_atr {sl} --tp_atr {tp}"
                    out = subprocess.check_output(cmd, shell=True, text=True)

                    win = float(re.search(r'win=([\d.]+)%', out).group(1))
                    bal = float(re.search(r'bal=([\d.]+)%', out).group(1))
                    trades = int(re.search(r'trades=(\d+)', out).group(1))

                    print(f"OK {sym} e={e} sl={sl} tp={tp} → bal={bal} win={win} trades={trades}")

                    results.append({
                        "symbol": sym,
                        "bal": bal,
                        "win": win,
                        "trades": trades,
                        "e": e,
                        "sl": sl,
                        "tp": tp
                    })

                except:
                    pass

print("\n==== TOP 10 ====")
results.sort(key=lambda x: (x["bal"]), reverse=True)

for r in results[:10]:
    print(r)

print("\n==== 실전 가능 ====")
usable = [r for r in results if r["bal"] > 100 and r["win"] > 45 and r["trades"] > 80]

for r in usable:
    print(r)
