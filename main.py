import os
import time
import threading
import requests
from flask import Flask, jsonify

from trader import Trader

app = Flask(__name__)

state = {
    "running": False,
    "last_heartbeat": None,
    "last_event": None,
    "last_error": None,
    "last_telegram": None,
}

trader = Trader(state)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""


@app.get("/")
def home():
    return "Bot Running"


@app.get("/health")
def health():
    return jsonify({**state, **(trader.public_state() if hasattr(trader, "public_state") else {})})


def tg_send(msg):
    print(msg, flush=True)
    if not TELEGRAM_API:
        return
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception as e:
        print("❌ TG send error:", e, flush=True)


def handle_command(text):
    try:
        print(f"📩 CMD: {text}", flush=True)

        if text == "/start":
            state["running"] = True
            tg_send("✅ START")

        elif text == "/stop":
            state["running"] = False
            tg_send("⛔ STOP")

        elif text == "/status":
            tg_send(str(trader.public_state()))

        elif text == "/buy":
            tg_send("🟢 BUY")
            trader.manual_buy()

        elif text == "/sell":
            tg_send("🔴 SELL")
            trader.manual_sell()

    except Exception as e:
        print("❌ handler crash:", e, flush=True)


def telegram_loop():
    offset = None

    while True:
        try:
            r = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params={"timeout": 10, "offset": offset},
                timeout=15,
            )
            data = r.json()

            for item in data.get("result", []):
                offset = item["update_id"] + 1
                msg = item.get("message", {})
                text = msg.get("text", "")

                if text:
                    handle_command(text)

        except Exception as e:
            print("❌ polling error:", e, flush=True)
            time.sleep(3)


def trading_loop():
    while True:
        try:
            if state["running"]:
                trader.tick()
            state["last_heartbeat"] = time.time()
            time.sleep(5)
        except Exception as e:
            print("❌ trading loop crash:", e, flush=True)
            time.sleep(3)


if __name__ == "__main__":
    print("🚀 Bot starting...", flush=True)

    threading.Thread(target=telegram_loop, daemon=True).start()
    threading.Thread(target=trading_loop, daemon=True).start()

    while True:
        try:
            app.run(host="0.0.0.0", port=8080)
        except Exception as e:
            print("❌ Flask crash, restarting:", e, flush=True)
            time.sleep(2)
