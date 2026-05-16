#!/usr/bin/env bash
set -euo pipefail
SERVICE_NAME="${BOT_SERVICE_NAME:-coin-trade-bot}"
BOT_DIR="${BOT_DIR:-$(pwd)}"

echo "=== systemd ==="
sudo systemctl status "$SERVICE_NAME" --no-pager || true

echo "=== process ==="
pgrep -af "python.*main.py" || echo "bot process not found"

echo "=== port 8080 ==="
ss -ltnp | grep ':8080' || true

echo "=== recent log ==="
tail -120 "$BOT_DIR/bot.log" 2>/dev/null || true
