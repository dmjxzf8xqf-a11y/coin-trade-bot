#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-$(pwd)}"
PY_BIN="$BOT_DIR/venv/bin/python"
[ -x "$PY_BIN" ] || PY_BIN="$(command -v python3)"

if [ ! -f "$BOT_DIR/bot_watchdog.py" ]; then
  echo "ERROR: bot_watchdog.py not found. Run inside repo root." >&2
  exit 1
fi

CRON_LINE="*/5 * * * * cd $BOT_DIR && WATCHDOG_RESTART_ON_FAIL=true $PY_BIN bot_watchdog.py --once >> data/watchdog.log 2>&1"
mkdir -p "$BOT_DIR/data"

( crontab -l 2>/dev/null | grep -v "bot_watchdog.py --once" || true; echo "$CRON_LINE" ) | crontab -

echo "OK: watchdog cron installed"
echo "$CRON_LINE"
