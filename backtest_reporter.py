# backtest_reporter.py
# Institutional Upgrade V2 - parse backtest logs and write JSON/CSV reports

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

BAL_RE = re.compile(r"(?P<sym>[A-Z0-9]+USDT).*?bal\s*[=:]\s*(?P<bal>-?\d+(?:\.\d+)?)%?.*?win\s*[=:]\s*(?P<win>-?\d+(?:\.\d+)?)%?.*?trades\s*[=:]\s*(?P<trades>\d+)", re.I)
ALT_RE = re.compile(r"(?P<sym>[A-Z0-9]+USDT).*?(?P<bal>\d+(?:\.\d+)?)%.*?(?P<win>\d+(?:\.\d+)?)%.*?(?P<trades>\d+)", re.I)
PARAM_RE = re.compile(r"enter(?:_score)?\s*[=:]\s*(?P<enter>\d+(?:\.\d+)?).*?sl(?:_atr)?\s*[=:]\s*(?P<sl>\d+(?:\.\d+)?).*?tp(?:_atr|_r)?\s*[=:]\s*(?P<tp>\d+(?:\.\d+)?)", re.I)


def parse_backtest_log(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    last_params: Dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        pm = PARAM_RE.search(line)
        if pm:
            last_params = {"enter_score": float(pm.group("enter")), "sl_atr": float(pm.group("sl")), "tp_r": float(pm.group("tp"))}
        m = BAL_RE.search(line) or ALT_RE.search(line)
        if not m:
            continue
        try:
            bal = float(m.group("bal"))
            win = float(m.group("win"))
            trades = int(m.group("trades"))
            row = {"symbol": m.group("sym").upper(), "bal_pct": bal, "return_pct": bal - 100.0, "winrate_pct": win, "trades": trades, **last_params}
            rows.append(row)
        except Exception:
            continue
    # 같은 심볼이 여러 번 나오면 마지막/최고 성과를 구분할 수 없으므로 raw 그대로 두되, summary는 best로 만듦.
    return rows


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        sym = r.get("symbol")
        if not sym:
            continue
        if sym not in by_symbol or float(r.get("bal_pct", 0) or 0) > float(by_symbol[sym].get("bal_pct", 0) or 0):
            by_symbol[sym] = dict(r)
    vals = list(by_symbol.values())
    if not vals:
        return {"symbols": {}, "portfolio": {"n": 0}}
    returns = [float(v.get("return_pct", 0) or 0) for v in vals]
    wins = [float(v.get("winrate_pct", 0) or 0) for v in vals]
    return {
        "symbols": by_symbol,
        "portfolio": {
            "n": len(vals),
            "avg_return_pct": round(sum(returns) / len(returns), 4),
            "median_return_pct": round(statistics.median(returns), 4),
            "avg_winrate_pct": round(sum(wins) / len(wins), 4),
            "positive_symbols": sum(1 for x in returns if x > 0),
            "best_symbol": max(vals, key=lambda x: float(x.get("return_pct", 0) or 0)).get("symbol"),
            "worst_symbol": min(vals, key=lambda x: float(x.get("return_pct", 0) or 0)).get("symbol"),
        },
    }


def write_report(log_path: Path, out_dir: Path) -> Dict[str, Any]:
    rows = parse_backtest_log(log_path)
    summary = summarize(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"created_ts": time.time(), "source": str(log_path), "rows": rows, **summary}
    json_path = out_dir / "report_latest.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_path = out_dir / "report_latest.csv"
    fields = sorted({k for r in rows for k in r.keys()} or {"symbol", "bal_pct", "winrate_pct", "trades"})
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for r in rows:
            wr.writerow(r)
    return {"json": str(json_path), "csv": str(csv_path), "summary": summary}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?", default="")
    ap.add_argument("--out", default="data/backtests")
    args = ap.parse_args()
    if args.log:
        log_path = Path(args.log)
    else:
        files = sorted(Path("data/backtests").glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            raise SystemExit("no log file found in data/backtests")
        log_path = files[0]
    res = write_report(log_path, Path(args.out))
    print(json.dumps(res["summary"], ensure_ascii=False, indent=2))
    print(f"written: {res['json']} {res['csv']}")


if __name__ == "__main__":
    main()
