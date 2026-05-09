# auto_param_tuner_v2.py
# Institutional Upgrade V2 - recommendation-first auto parameter tuner
# 기본은 자동 적용이 아니라 symbol_profiles_candidate.json에 추천만 저장.

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from backtest_reporter import parse_backtest_log, summarize


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except Exception:
        return float(default)


def _int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except Exception:
        return int(default)


def run_backtest(symbols: str, days: int, interval: str, grid: str, short: str, run_id: str) -> Tuple[Path, Dict[str, Any]]:
    log = Path(f"data/backtests/tune_v2_{days}d_{run_id}.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "run_backtest_opt.py", "--symbols", symbols, "--interval", interval, "--days", str(days), "--grid", grid, "--short", short]
    print("RUN", " ".join(cmd))
    with log.open("w", encoding="utf-8") as f:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert p.stdout is not None
        for line in p.stdout:
            print(line, end="")
            f.write(line)
        p.wait()
    return log, summarize(parse_backtest_log(log))


def grade_candidate(rows_by_days: Dict[int, Dict[str, Any]], symbol: str) -> Dict[str, Any]:
    min_bal = _float("TUNE_MIN_BAL", 110.0)
    min_win = _float("TUNE_MIN_WINRATE", 50.0)
    min_trades = _int("TUNE_MIN_TRADES", 40)
    min_positive_windows = _int("TUNE_MIN_POSITIVE_WINDOWS", 2)
    windows = []
    best: Dict[str, Any] = {}
    for days, summary in rows_by_days.items():
        row = ((summary.get("symbols") or {}).get(symbol) or {})
        if row:
            windows.append({"days": days, **row})
            if not best or float(row.get("bal_pct", 0) or 0) > float(best.get("bal_pct", 0) or 0):
                best = dict(row)
    positive = sum(1 for r in windows if float(r.get("bal_pct", 0) or 0) >= 100.0)
    strong = sum(1 for r in windows if float(r.get("bal_pct", 0) or 0) >= min_bal and float(r.get("winrate_pct", 0) or 0) >= min_win and int(r.get("trades", 0) or 0) >= min_trades)
    passed = positive >= min_positive_windows and strong >= 1
    grade = "CORE" if passed else "WATCH" if positive >= 1 else "BLOCK"
    # best row에 파라미터가 있으면 사용. 없으면 기존 추천 기본값.
    enter = int(float(best.get("enter_score", 70) or 70))
    sl = float(best.get("sl_atr", 1.8) or 1.8)
    tp = float(best.get("tp_r", 1.5) or 1.5)
    size_mult = 1.0 if grade == "CORE" else 0.25 if grade == "WATCH" else 0.0
    max_lev = 8 if grade == "CORE" else 3
    return {
        "symbol": symbol,
        "grade": grade,
        "enter_score": enter,
        "stop_atr": sl,
        "tp_r": tp,
        "max_lev": max_lev,
        "size_mult": size_mult,
        "short": True,
        "tune_pass": passed,
        "positive_windows": positive,
        "strong_windows": strong,
        "windows": windows,
        "updated_by": "auto_param_tuner_v2",
        "updated_ts": time.time(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=_env("TUNE_SYMBOLS", "ONDOUSDT,ZECUSDT,BTCUSDT,ETHUSDT,SOLUSDT"))
    ap.add_argument("--interval", default=_env("ENTRY_INTERVAL", "15"))
    ap.add_argument("--windows", default=_env("TUNE_WINDOWS", "30,90,180,365"))
    ap.add_argument("--grid", default=_env("TUNE_GRID", "on"))
    ap.add_argument("--short", default=_env("TUNE_SHORT", "on"))
    ap.add_argument("--out", default=_env("TUNE_CANDIDATE_FILE", "symbol_profiles_candidate.json"))
    ap.add_argument("--apply", action="store_true", help="danger: directly overwrite symbol_profiles.json")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    windows = [int(x.strip()) for x in args.windows.split(",") if x.strip()]
    run_id = time.strftime("%Y%m%d_%H%M%S")
    rows_by_days: Dict[int, Dict[str, Any]] = {}
    logs: Dict[int, str] = {}
    for d in windows:
        log, summary = run_backtest(",".join(symbols), d, args.interval, args.grid, args.short, run_id)
        rows_by_days[d] = summary
        logs[d] = str(log)

    candidates = {"_meta": {"version": "symbol_profiles_candidate_v2", "created_ts": time.time(), "logs": logs, "mode": "recommendation_only"}}
    for sym in symbols:
        c = grade_candidate(rows_by_days, sym)
        candidates[sym] = {k: v for k, v in c.items() if k != "symbol"}

    out_path = Path(args.out)
    out_path.write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n=== TUNER V2 CANDIDATES ===")
    for sym in symbols:
        c = candidates[sym]
        print(f"{sym}: {c['grade']} pass={c['tune_pass']} enter={c['enter_score']} sl={c['stop_atr']} tp={c['tp_r']} poswin={c['positive_windows']} strong={c['strong_windows']}")
    print("written:", out_path)

    if args.apply or _env("TUNE_AUTO_APPLY", "false").lower() in ("1", "true", "yes", "on"):
        dst = Path(_env("SYMBOL_PROFILE_FILE", "symbol_profiles.json"))
        if dst.exists():
            backup = dst.with_suffix(dst.suffix + f".bak_{run_id}")
            backup.write_text(dst.read_text(encoding="utf-8"), encoding="utf-8")
            print("backup:", backup)
        dst.write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("APPLIED:", dst)


if __name__ == "__main__":
    main()
