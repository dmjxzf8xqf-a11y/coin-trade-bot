#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-$(pwd)}"
SERVICE_NAME="${BOT_SERVICE_NAME:-coin-trade-bot}"
PY_BIN="$BOT_DIR/venv/bin/python"

if [ ! -f "$BOT_DIR/main.py" ]; then
  echo "ERROR: main.py not found. Run this inside ~/coin-trade-bot or set BOT_DIR." >&2
  exit 1
fi

if [ ! -x "$PY_BIN" ]; then
  PY_BIN="$(command -v python3)"
fi

if [ -z "${PY_BIN:-}" ]; then
  echo "ERROR: python3 not found" >&2
  exit 1
fi

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "[INSTALL] BOT_DIR=$BOT_DIR"
echo "[INSTALL] PY_BIN=$PY_BIN"
echo "[INSTALL] SERVICE_FILE=$SERVICE_FILE"

sudo tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=Coin Trade Bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$BOT_DIR
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-$BOT_DIR/.env
ExecStart=$PY_BIN $BOT_DIR/main.py
Restart=always
RestartSec=10
StartLimitIntervalSec=300
StartLimitBurst=10
StandardOutput=append:$BOT_DIR/bot.log
StandardError=append:$BOT_DIR/bot.err.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sleep 3
sudo systemctl --no-pager --full status "$SERVICE_NAME" || true

echo ""
echo "OK: systemd service installed."
echo "status:  sudo systemctl status $SERVICE_NAME --no-pager"
echo "logs:    journalctl -u $SERVICE_NAME -n 100 --no-pager"
echo "stop:    sudo systemctl stop $SERVICE_NAME"
echo "restart: sudo systemctl restart $SERVICE_NAME"
