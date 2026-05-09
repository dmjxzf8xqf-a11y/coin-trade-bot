# walkforward_validator.py
# Institutional Upgrade V2 - lightweight multi-window / pseudo walk-forward validator
# 기존 run_backtest_opt.py가 날짜별 end 옵션을 지원하지 않아도 30/90/180/365 복수 구간으로 안정성 검증.

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from backtest_reporter import parse_backtest_log, summarize


def run_cmd(cmd: List[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert p.stdout is not None
        for line in p.stdout:
            print(line, end="")
            f.write(line)
        return p.wait()


def validate_symbol(summary_by_window: Dict[int, Dict[str, Any]], symbol: str, min_positive_windows: int, min_trades: int) -> Dict[str, Any]:
    rows = []
    for days, summary in summary_by_window.items():
        row = ((summary.get("symbols") or {}).get(symbol) or {})
        if row:
            rows.append({"days": days, **row})
    positive = sum(1 for r in rows if float(r.get("return_pct", 0) or 0) > 0)
    trades_ok = sum(1 for r in rows if int(r.get("trades", 0) or 0) >= min_trades)
    score = positive * 10 + trades_ok
    return {"symbol": symbol, "positive_windows": positive, "trades_ok_windows": trades_ok, "score": score, "pass": positive >= min_positive_windows, "windows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=os.getenv("TUNE_SYMBOLS", "ONDOUSDT,ZECUSDT,BTCUSDT,ETHUSDT,SOLUSDT"))
    ap.add_argument("--interval", default=os.getenv("ENTRY_INTERVAL", "15"))
    ap.add_argument("--windows", default="30,90,180,365")
    ap.add_argument("--short", default="on")
    ap.add_argument("--grid", default="on")
    ap.add_argument("--min-positive-windows", type=int, default=2)
    ap.add_argument("--min-trades", type=int, default=20)
    ap.add_argument("--out", default="data/backtests/walkforward_latest.json")
    args = ap.parse_args()

    windows = [int(x.strip()) for x in args.windows.split(",") if x.strip()]
    summary_by_window: Dict[int, Dict[str, Any]] = {}
    run_id = time.strftime("%Y%m%d_%H%M%S")
    for days in windows:
        log = Path(f"data/backtests/wf_{days}d_{run_id}.log")
        cmd = [sys.executable, "run_backtest_opt.py", "--symbols", args.symbols, "--interval", str(args.interval), "--days", str(days), "--grid", args.grid, "--short", args.short]
        print("RUN", " ".join(cmd))
        rc = run_cmd(cmd, log)
        rows = parse_backtest_log(log)
        summary_by_window[days] = summarize(rows)
        if rc != 0:
            print(f"warning: command failed rc={rc} days={days}")

    all_symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    verdicts = [validate_symbol(summary_by_window, s, args.min_positive_windows, args.min_trades) for s in all_symbols]
    out = {"created_ts": time.time(), "symbols": all_symbols, "windows": windows, "verdicts": verdicts, "summary_by_window": summary_by_window}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n=== WALKFORWARD VERDICT ===")
    for v in verdicts:
        print(f"{v['symbol']}: pass={v['pass']} positive={v['positive_windows']} score={v['score']}")
    print("written:", out_path)


if __name__ == "__main__":
    main()
