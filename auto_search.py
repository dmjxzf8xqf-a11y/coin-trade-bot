#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import itertools
import json
import re
import subprocess
import sys
from pathlib import Path

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]
MODES = ["long", "short"]
ENTER_SCORES = [55, 60, 65, 70, 75]
SL_ATRS = [1.0, 1.2, 1.5]
TP_ATRS = [0.8, 1.0, 1.2, 1.5]
TIME_EXITS = [0, 12, 24]

MIN_TRADES = 50
MIN_WIN = 45.0
MIN_BAL = 100.0

PAT = re.compile(
    r"🏆\s+(?P<symbol>[A-Z0-9]+):\s+trades=(?P<trades>\d+)\s+win=(?P<win>[\d.]+)%\s+bal=(?P<bal>[\d.]+)%"
)

def score_row(row):
    bal = row["bal"]
    win = row["win"]
    trades = row["trades"]
    trade_bonus = min(trades, 150) / 150.0 * 5.0
    return bal + (win * 0.25) + trade_bonus

def run_bt(sym, mode, e, sl, tp, texit):
    short_flag = "on" if mode == "short" else "off"
    cmd = [
        sys.executable, "run_backtest_opt.py",
        "--symbols", sym,
        "--interval", "15",
        "--days", "180",
        "--enter_score", str(e),
        "--sl_atr", str(sl),
        "--tp_atr", str(tp),
        "--short", short_flag,
    ]
    if texit > 0:
        cmd += ["--time_exit_bars", str(texit)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    m = PAT.search(out)
    if not m:
        return None, out[-500:]
    row = {
        "symbol": m.group("symbol"),
        "mode": mode,
        "enter_score": e,
        "sl_atr": sl,
        "tp_atr": tp,
        "time_exit_bars": texit,
        "trades": int(m.group("trades")),
        "win": float(m.group("win")),
        "bal": float(m.group("bal")),
    }
    row["score"] = round(score_row(row), 4)
    row["usable"] = bool(row["trades"] >= MIN_TRADES and row["win"] >= MIN_WIN and row["bal"] >= MIN_BAL)
    return row, ""

def main():
    results = []
    parse_fails = []

    for sym, mode, e, sl, tp, texit in itertools.product(
        SYMBOLS, MODES, ENTER_SCORES, SL_ATRS, TP_ATRS, TIME_EXITS
    ):
        row, fail = run_bt(sym, mode, e, sl, tp, texit)
        if row is None:
            parse_fails.append({
                "symbol": sym, "mode": mode, "enter_score": e,
                "sl_atr": sl, "tp_atr": tp, "time_exit_bars": texit,
                "fail": fail,
            })
            print(f"PARSE_FAIL {sym} {mode} e={e} sl={sl} tp={tp} t={texit}")
            continue
        results.append(row)
        print(
            f'OK {row["symbol"]} {row["mode"]} '
            f'e={row["enter_score"]} sl={row["sl_atr"]} tp={row["tp_atr"]} t={row["time_exit_bars"]} '
            f'-> bal={row["bal"]} win={row["win"]} trades={row["trades"]} usable={row["usable"]}'
        )

    results.sort(key=lambda x: (x["usable"], x["score"], x["bal"], x["win"], x["trades"]), reverse=True)
    top10 = results[:10]
    usable = [r for r in results if r["usable"]]

    print("\n===== TOP 10 =====")
    for r in top10:
        print(r)

    print("\n===== USABLE =====")
    for r in usable[:20]:
        print(r)

    best = usable[0] if usable else None
    if best is None:
        best = {
            "mode": "off",
            "symbol": "NONE",
            "enter_score": 70,
            "sl_atr": 1.5,
            "tp_atr": 1.0,
            "time_exit_bars": 0,
            "reason": "no_usable_config",
        }

    payload = {
        "selected": best,
        "top10": top10,
        "usable_count": len(usable),
    }

    Path("best_config.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSaved best_config.json")

if __name__ == "__main__":
    main()
