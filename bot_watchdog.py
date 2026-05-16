#!/usr/bin/env python3
"""
Operational watchdog for coin-trade-bot.

Safe by default:
- Does not trade.
- Does not modify strategy settings.
- Only checks service/process/log health.
- Can restart systemd service when enabled.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

SERVICE_NAME = os.getenv("BOT_SERVICE_NAME", "coin-trade-bot")
BOT_DIR = Path(os.getenv("BOT_DIR", Path(__file__).resolve().parent)).resolve()
LOG_FILE = Path(os.getenv("BOT_LOG_FILE", BOT_DIR / "bot.log"))
ENV_FILE = Path(os.getenv("BOT_ENV_FILE", BOT_DIR / ".env"))
MAX_LOG_AGE_SEC = int(float(os.getenv("WATCHDOG_MAX_LOG_AGE_SEC", "600")))
RESTART_ON_FAIL = os.getenv("WATCHDOG_RESTART_ON_FAIL", "false").strip().lower() in {"1", "true", "yes", "y", "on"}
ALERT_ON_FAIL = os.getenv("WATCHDOG_TG_ALERT", "true").strip().lower() in {"1", "true", "yes", "y", "on"}

BAD_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"ModuleNotFoundError"),
    re.compile(r"ImportError"),
    re.compile(r"TELEGRAM_API=False"),
    re.compile(r"CHAT_ID_ENV=False"),
    re.compile(r"TELEGRAM_API missing"),
    re.compile(r"Address already in use"),
    re.compile(r"Port 8080 is in use"),
    re.compile(r"ORDER STATUS UNKNOWN"),
]


def run(cmd: List[str], timeout: int = 20) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(BOT_DIR), text=True, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 999, repr(e)


def read_env(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def systemd_active() -> bool:
    code, out = run(["systemctl", "is-active", SERVICE_NAME])
    return code == 0 and out.splitlines()[0].strip() == "active"


def process_count() -> int:
    code, out = run(["bash", "-lc", "pgrep -af 'python.*main.py' | wc -l"])
    if code != 0:
        return 0
    try:
        return int(out.splitlines()[0].strip())
    except Exception:
        return 0


def log_tail(path: Path, lines: int = 120) -> str:
    if not path.exists():
        return ""
    code, out = run(["bash", "-lc", f"tail -{int(lines)} {str(path)!r}"])
    return out if code == 0 else ""


def log_age_sec(path: Path) -> int | None:
    if not path.exists():
        return None
    return int(time.time() - path.stat().st_mtime)


def telegram_alert(text: str) -> None:
    if not ALERT_ON_FAIL:
        return
    env = read_env(ENV_FILE)
    token = env.get("BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("CHAT_ID")
    if not token or not chat_id:
        return
    try:
        import requests  # type: ignore
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:3500]},
            timeout=10,
        )
    except Exception:
        pass


def restart_service(reason: str) -> str:
    if not RESTART_ON_FAIL:
        return "restart skipped: WATCHDOG_RESTART_ON_FAIL=false"
    code, out = run(["systemctl", "restart", SERVICE_NAME], timeout=30)
    if code == 0:
        msg = f"restart requested: {SERVICE_NAME} ({reason})"
    else:
        msg = f"restart failed: {out}"
    telegram_alert("🧯 bot watchdog\n" + msg)
    return msg


def check() -> Tuple[bool, List[str]]:
    problems: List[str] = []
    notes: List[str] = []

    active = systemd_active()
    pcount = process_count()
    age = log_age_sec(LOG_FILE)
    tail = log_tail(LOG_FILE)

    notes.append(f"service={SERVICE_NAME} active={active}")
    notes.append(f"process_count={pcount}")
    notes.append(f"log_file={LOG_FILE}")
    notes.append(f"log_age_sec={age}")

    if active is False:
        # Some users may still run nohup. In that case a process count of 1 is acceptable.
        if pcount < 1:
            problems.append("bot process is not running")
        else:
            notes.append("systemd inactive but process exists; likely nohup/manual mode")

    if pcount > 1:
        problems.append(f"duplicate bot processes detected: {pcount}")

    if age is None:
        problems.append("bot.log missing")
    elif age > MAX_LOG_AGE_SEC:
        problems.append(f"bot.log stale: {age}s > {MAX_LOG_AGE_SEC}s")

    hits = []
    for pat in BAD_PATTERNS:
        if tail and pat.search(tail):
            hits.append(pat.pattern)
    if hits:
        problems.append("bad log patterns: " + ", ".join(hits[:5]))

    return (len(problems) == 0), notes + problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one check and exit")
    ap.add_argument("--restart", action="store_true", help="restart on failure for this run")
    args = ap.parse_args()

    global RESTART_ON_FAIL
    if args.restart:
        RESTART_ON_FAIL = True

    ok, lines = check()
    print("[WATCHDOG] OK" if ok else "[WATCHDOG] FAIL")
    for line in lines:
        print("- " + line)

    if not ok:
        reason = "; ".join(lines[-3:])
        print("- " + restart_service(reason))
        telegram_alert("🧯 bot watchdog FAIL\n" + "\n".join(lines[-8:]))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
