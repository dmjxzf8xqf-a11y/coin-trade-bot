# auto_param_tuner.py
# 목적:
# - run_backtest_opt.py를 백그라운드/수동으로 돌려 심볼별 후보 파라미터를 추천한다.
# - 기본값은 절대 자동 적용하지 않는다. candidate_symbol_profiles.json만 만든다.
# - 적용은 사용자가 확인 후 symbol_profiles.json으로 복사하거나 /applyprofile 같은 별도 승인 명령으로 하도록 설계.
#
# 사용 예:
#   python auto_param_tuner.py --symbols ONDOUSDT,ZECUSDT,BTCUSDT,ETHUSDT,SOLUSDT --days 180 --grid on --short on
#   python auto_param_tuner.py --symbols ONDOUSDT,ZECUSDT --days 365 --grid on --short on
#
# 출력:
#   data/param_tuner/auto_tune_YYYYmmdd_HHMMSS.log
#   data/param_tuner/candidate_symbol_profiles.json
#   data/param_tuner/tune_report.json

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_SYMBOLS = "ONDOUSDT,ZECUSDT,BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT"


def now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except Exception:
        return float(default)


def env_int(name: str, default: int) -> int:
    try:
        return int(float(str(os.getenv(name, str(default))).strip()))
    except Exception:
        return int(default)


def safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        s = str(x).strip().replace("%", "")
        return float(s)
    except Exception:
        return default


def safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(float(str(x).strip()))
    except Exception:
        return int(default)


def parse_backtest_output(text: str) -> Dict[str, Dict[str, Any]]:
    """Parse several common run_backtest_opt.py output styles.

    Expected fragments can be like:
      ONDOUSDT bal=122.76% win=61.29% trades=62 enter_score=70 sl_atr=2.0 tp_atr=1.5 short=True
      ONDOUSDT: win 61.29%, bal 122.76%, trades 62, best {'enter_score':70,'sl_atr':2.0,'tp_atr':1.5}
    """
    out: Dict[str, Dict[str, Any]] = {}
    sym_re = re.compile(r"\b([A-Z0-9]{2,20}USDT)\b")
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = sym_re.search(line)
        if not m:
            continue
        sym = m.group(1).upper()
        low = line.lower()
        if not any(k in low for k in ("bal", "win", "trade", "enter", "sl_atr", "tp_atr")):
            continue

        row = out.get(sym, {"symbol": sym})

        patterns = {
            "bal": [r"bal\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)\s*%?", r"balance\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)\s*%?"],
            "winrate": [r"win(?:rate)?\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)\s*%?"],
            "trades": [r"trades?\s*[=: ]\s*([0-9]+)", r"n\s*[=: ]\s*([0-9]+)"],
            "enter_score": [r"enter_score\s*[=: ]\s*([0-9]+)", r"enter\s*[=: ]\s*([0-9]+)"],
            "stop_atr": [r"sl_atr\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)", r"stop_atr\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)", r"sl\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)"],
            "tp_r": [r"tp_atr\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)", r"tp_r\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)", r"tp\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)"],
            "profit_factor": [r"pf\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)", r"profit_factor\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)"],
            "mdd": [r"mdd\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)\s*%?", r"drawdown\s*[=: ]\s*([0-9]+(?:\.[0-9]+)?)\s*%?"],
        }
        for key, pats in patterns.items():
            for pat in pats:
                mm = re.search(pat, line, flags=re.I)
                if mm:
                    val = mm.group(1)
                    if key in ("trades", "enter_score"):
                        row[key] = safe_int(val)
                    else:
                        row[key] = safe_float(val)
                    break

        # Python-dict-like best config fallback
        dict_match = re.search(r"\{.*\}", line)
        if dict_match:
            blob = dict_match.group(0)
            for key in ("enter_score", "sl_atr", "stop_atr", "tp_atr", "tp_r"):
                km = re.search(rf"['\"]?{key}['\"]?\s*:\s*([0-9]+(?:\.[0-9]+)?)", blob)
                if km:
                    if key == "enter_score":
                        row["enter_score"] = safe_int(km.group(1))
                    elif key in ("sl_atr", "stop_atr"):
                        row["stop_atr"] = safe_float(km.group(1))
                    elif key in ("tp_atr", "tp_r"):
                        row["tp_r"] = safe_float(km.group(1))
        out[sym] = row
    return out


def grade_row(row: Dict[str, Any], args: argparse.Namespace) -> str:
    bal = safe_float(row.get("bal"), 0.0) or 0.0
    win = safe_float(row.get("winrate"), 0.0) or 0.0
    trades = safe_int(row.get("trades"), 0)
    pf = safe_float(row.get("profit_factor"), None)
    mdd = safe_float(row.get("mdd"), None)

    if trades < args.min_trades:
        return "BLOCK"
    if bal >= args.min_bal and win >= args.min_winrate:
        if pf is not None and pf < args.min_profit_factor:
            return "WATCH"
        if mdd is not None and mdd > args.max_mdd:
            return "WATCH"
        return "CORE"
    if bal >= args.watch_min_bal and win >= args.watch_min_winrate:
        return "WATCH"
    return "BLOCK"


def profile_from_row(row: Dict[str, Any], grade: str) -> Dict[str, Any]:
    sym = str(row.get("symbol") or "").upper()
    enter = safe_int(row.get("enter_score"), 70 if grade == "CORE" else 78)
    stop = safe_float(row.get("stop_atr"), 1.5 if grade == "CORE" else 1.8)
    tp = safe_float(row.get("tp_r"), 2.0 if grade == "CORE" else 1.5)

    if grade == "CORE":
        max_lev = 8
        size_mult = 1.0
    elif grade == "WATCH":
        max_lev = 3
        size_mult = 0.25
        enter = max(enter, 78)
    else:
        max_lev = 0
        size_mult = 0.0

    return {
        "grade": grade,
        "enter_score": int(enter),
        "stop_atr": float(stop),
        "tp_r": float(tp),
        "max_lev": int(max_lev),
        "size_mult": float(size_mult),
        "short": True,
        "source": "auto_param_tuner",
        "last_tuned_utc": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "bal": row.get("bal"),
            "winrate": row.get("winrate"),
            "trades": row.get("trades"),
            "profit_factor": row.get("profit_factor"),
            "mdd": row.get("mdd"),
        },
        "note": f"{sym} tuned as {grade}. Review before applying to live.",
    }


def build_command(args: argparse.Namespace) -> List[str]:
    cmd = [
        sys.executable,
        "run_backtest_opt.py",
        "--symbols",
        args.symbols,
        "--interval",
        str(args.interval),
        "--days",
        str(args.days),
    ]
    if args.grid:
        cmd += ["--grid", args.grid]
    if args.short:
        cmd += ["--short", args.short]
    return cmd


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default=os.getenv("TUNE_SYMBOLS", DEFAULT_SYMBOLS))
    p.add_argument("--interval", default=os.getenv("ENTRY_INTERVAL", "15"))
    p.add_argument("--days", type=int, default=env_int("TUNE_LOOKBACK_DAYS", 180))
    p.add_argument("--grid", default="on", choices=["on", "off", ""])
    p.add_argument("--short", default="on", choices=["on", "off", ""])
    p.add_argument("--outdir", default="data/param_tuner")
    p.add_argument("--min-bal", type=float, default=env_float("TUNE_MIN_BAL", 110.0))
    p.add_argument("--min-winrate", type=float, default=env_float("TUNE_MIN_WINRATE", 50.0))
    p.add_argument("--min-trades", type=int, default=env_int("TUNE_MIN_TRADES", 40))
    p.add_argument("--min-profit-factor", type=float, default=env_float("TUNE_MIN_PROFIT_FACTOR", 1.2))
    p.add_argument("--max-mdd", type=float, default=env_float("TUNE_MAX_MDD", 20.0))
    p.add_argument("--watch-min-bal", type=float, default=95.0)
    p.add_argument("--watch-min-winrate", type=float, default=45.0)
    p.add_argument("--apply", action="store_true", help="위험: symbol_profiles.json에 바로 적용. 기본은 추천만 저장.")
    args = p.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    run_id = now_id()
    log_path = outdir / f"auto_tune_{run_id}.log"
    report_path = outdir / "tune_report.json"
    candidate_path = outdir / "candidate_symbol_profiles.json"

    cmd = build_command(args)
    print("[TUNER] running:", " ".join(shlex.quote(c) for c in cmd), flush=True)
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = proc.stdout or ""
    log_path.write_text(text, encoding="utf-8")

    rows = parse_backtest_output(text)
    profiles: Dict[str, Any] = {
        "_meta": {
            "version": "candidate_from_auto_param_tuner_v1",
            "run_id": run_id,
            "days": args.days,
            "interval": args.interval,
            "grid": args.grid,
            "short": args.short,
            "command": cmd,
            "returncode": proc.returncode,
            "apply_mode": "AUTO_APPLIED" if args.apply else "RECOMMEND_ONLY",
        }
    }
    summary = []
    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        row = rows.get(sym, {"symbol": sym})
        row.setdefault("symbol", sym)
        grade = grade_row(row, args)
        profiles[sym] = profile_from_row(row, grade)
        summary.append({"symbol": sym, "grade": grade, **row})

    candidate_path.write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "ok": proc.returncode == 0,
        "run_id": run_id,
        "log_path": str(log_path),
        "candidate_path": str(candidate_path),
        "summary": summary,
        "thresholds": {
            "min_bal": args.min_bal,
            "min_winrate": args.min_winrate,
            "min_trades": args.min_trades,
            "min_profit_factor": args.min_profit_factor,
            "max_mdd": args.max_mdd,
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n[TUNER] summary")
    for row in summary:
        print(
            f"- {row.get('symbol')} {row.get('grade')} "
            f"bal={row.get('bal','?')} win={row.get('winrate','?')} trades={row.get('trades','?')} "
            f"enter={row.get('enter_score','?')} sl={row.get('stop_atr','?')} tp={row.get('tp_r','?')}"
        )
    print(f"\n[TUNER] log={log_path}")
    print(f"[TUNER] candidate={candidate_path}")
    print(f"[TUNER] report={report_path}")

    if args.apply:
        target = Path("symbol_profiles.json")
        backup = Path(f"symbol_profiles.backup_{run_id}.json")
        if target.exists():
            backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        target.write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[TUNER] APPLIED to {target}; backup={backup if backup.exists() else '-'}")
    else:
        print("[TUNER] recommend only. 검토 후 candidate_symbol_profiles.json 내용을 symbol_profiles.json에 반영해.")

    return 0 if proc.returncode == 0 else proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
