import os
import time
import threading

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify

load_dotenv()

import trader as trader_module
from trader import Trader

app = Flask(__name__)

# -------------------------
# ENV
# -------------------------
BOT_TOKEN = (os.getenv("BOT_TOKEN", "") or "").strip()
CHAT_ID_ENV = (os.getenv("CHAT_ID", "") or "").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

PORT = int(os.getenv("PORT", "8080"))
POLL_TIMEOUT = int(os.getenv("TG_POLL_TIMEOUT", "25"))
POLL_SLEEP = float(os.getenv("TG_POLL_SLEEP", "0.5"))
TRADING_TICK_SEC = float(os.getenv("TRADING_TICK_SEC", "5"))

state = {
    "running": True,
    "last_heartbeat": None,
    "last_event": None,
    "last_error": None,
    "last_telegram": None,
    "chat_id": CHAT_ID_ENV or None,
}

# Make trader module see the startup chat id immediately if present.
if CHAT_ID_ENV:
    trader_module.CHAT_ID = CHAT_ID_ENV

trader = Trader(state)
_runtime_chat_id = CHAT_ID_ENV


@app.get("/")
def home():
    return "Bot Running"


@app.get("/health")
def health():
    try:
        ps = trader.public_state() if hasattr(trader, "public_state") else {}
    except Exception as e:
        ps = {"public_state_error": str(e)}
    return jsonify({**state, **ps})


# -------------------------
# Telegram helper for main-side notifications
# -------------------------
def tg_send(msg: str, reply_markup: dict | None = None):
    global _runtime_chat_id
    print(msg, flush=True)

    if not TELEGRAM_API:
        return

    chat_id = _runtime_chat_id or CHAT_ID_ENV
    if not chat_id:
        print("â ï¸ TG chat_id unknown. Waiting for first incoming message.", flush=True)
        return

    payload = {"chat_id": chat_id, "text": msg}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=15, proxies=PROXIES)
    except Exception as e:
        print("â TG send error:", repr(e), flush=True)


# -------------------------
# Telegram polling
# -------------------------
def telegram_loop():
    global _runtime_chat_id
    offset = None

    while True:
        try:
            if not TELEGRAM_API:
                time.sleep(2)
                continue

            r = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params={"timeout": POLL_TIMEOUT, "offset": offset},
                timeout=POLL_TIMEOUT + 10,
                proxies=PROXIES,
            )
            data = r.json()

            for item in data.get("result", []):
                offset = item["update_id"] + 1
                msg = item.get("message", {}) or {}
                text = (msg.get("text", "") or "").strip()

                # Capture runtime chat id and mirror it into trader module,
                # because trader.py uses its own module-level CHAT_ID in tg_send().
                try:
                    chat_id = str((msg.get("chat") or {}).get("id") or "")
                    if chat_id:
                        _runtime_chat_id = chat_id
                        state["chat_id"] = chat_id
                        trader_module.CHAT_ID = chat_id
                except Exception:
                    pass

                if not text:
                    continue

                state["last_telegram"] = time.time()
                print(f"ð© CMD: {text}", flush=True)

                try:
                    if hasattr(trader, "handle_command"):
                        trader.handle_command(text)
                    else:
                        if text == "/status":
                            tg_send(str(trader.public_state()))
                except Exception as e:
                    print("â trader.handle_command crash:", repr(e), flush=True)
                    state["last_error"] = f"tg_cmd: {e}"

        except Exception as e:
            print("â polling error:", repr(e), flush=True)
            state["last_error"] = f"polling: {e}"
            time.sleep(3)

        time.sleep(POLL_SLEEP)


# -------------------------
# Trading loop
# -------------------------
def trading_loop():
    while True:
        try:
            state["running"] = bool(getattr(trader, "trading_enabled", True))
            if state["running"]:
                trader.tick()
            state["last_heartbeat"] = time.time()
            time.sleep(TRADING_TICK_SEC)
        except Exception as e:
            print("â trading loop crash:", repr(e), flush=True)
            state["last_error"] = f"trading_loop: {e}"
            time.sleep(3)


if __name__ == "__main__":
    print("ð Bot starting...", flush=True)

    threading.Thread(target=telegram_loop, daemon=True).start()
    threading.Thread(target=trading_loop, daemon=True).start()

    app.run(host="0.0.0.0", port=PORT)
