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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""


@app.get("/")
def home():
    return "Bot Running"


@app.get("/health")
def health():
    return jsonify({**state, **(trader.public_state() if hasattr(trader, "public_state") else {})})


# ✅ 텔레그램 업데이트 가져오기
def _tg_get_updates(offset=None, timeout=25):
    if not TELEGRAM_API:
        return {"ok": False, "result": []}
    params = {"timeout": timeout}
    if offset:
        params["offset"] = offset
    r = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=timeout + 5)
    return r.json()


# ✅ 텔레그램 폴링 루프
def telegram_loop():
    print("✅ Telegram polling started")
    offset = None

    while True:
        try:
            data = _tg_get_updates(offset)

            if not data.get("ok"):
                time.sleep(2)
                continue

            for update in data["result"]:
                offset = update["update_id"] + 1

                msg = update.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if not text:
                    continue

                print(f"📩 {chat_id}: {text}")
                state["last_telegram"] = text

                # CHAT_ID 제한 사용 시
                if CHAT_ID and chat_id != CHAT_ID:
                    print("⛔ 다른 채팅 무시")
                    continue

                try:
                    trader.handle_command(text)
                except Exception as e:
                    print("❌ handle_command error:", e)

        except Exception as e:
            print("❌ telegram_loop error:", e)

        time.sleep(1)


# ✅ 트레이딩 루프
def loop():
    state["running"] = True
    trader.notify("🤖 봇 시작됨")

    while True:
        try:
            state["last_heartbeat"] = time.strftime("%Y-%m-%d %H:%M:%S")
            trader.tick()
            state["last_error"] = None
        except Exception as e:
            state["last_error"] = str(e)
            trader.notify(f"❌ 루프 에러: {e}")

        time.sleep(int(os.getenv("LOOP_SECONDS", "20")))


if __name__ == "__main__":
    # ✅ 텔레그램 루프
    threading.Thread(target=telegram_loop, daemon=True).start()

    # ✅ 트레이딩 루프
    threading.Thread(target=loop, daemon=True).start()

    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
