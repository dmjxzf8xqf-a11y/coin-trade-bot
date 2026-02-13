import os
import time
import threading
from flask import Flask, jsonify, request
from trader import Trader
from config import BOT_TOKEN, CHAT_ID, LOOP_SECONDS

app = Flask(__name__)

state = {
    "running": False,
    "last_heartbeat": None,
    "last_event": None,
    "last_error": None,
}

trader = Trader(state)

@app.get("/")
def home():
    return "Bot Running"

@app.get("/health")
def health():
    return jsonify({**state, **trader.public_state()})

# ---- Telegram polling ----
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def tg_get_updates(offset=None):
    if not BOT_TOKEN:
        return []
    params = {"timeout": 10}
    if offset is not None:
        params["offset"] = offset
    try:
        r = request_get(f"{TG_API}/getUpdates", params=params, timeout=20)
        data = r.json()
        if not data.get("ok"):
            return []
        return data.get("result", [])
    except:
        return []

def request_get(url, params=None, timeout=10):
    import requests
    return requests.get(url, params=params, timeout=timeout)

def telegram_loop():
    if not BOT_TOKEN:
        return
    offset = None
    trader.tg_send("🤖 텔레그램 폴링 시작. /help 입력 가능")

    while True:
        updates = tg_get_updates(offset=offset)
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            text = msg.get("text", "")

            # ✅ 보안: CHAT_ID와 같은 채팅만 명령 허용
            if CHAT_ID and chat_id != str(CHAT_ID):
                continue

            if text:
                trader.handle_command(text)

        time.sleep(1)

# ---- Trading loop ----
def trading_loop():
    state["running"] = True
    trader.tg_send("🤖 봇 시작됨. /start 로 거래 ON")
    while True:
        try:
            state["last_heartbeat"] = time.strftime("%Y-%m-%d %H:%M:%S")
            trader.tick()
            state["last_error"] = None
        except Exception as e:
            state["last_error"] = str(e)
            trader.tg_send_bybit_err_throttled(f"❌ 루프 에러: {e}")
        time.sleep(int(LOOP_SECONDS))

if __name__ == "__main__":
    # 백그라운드 스레드 실행
    threading.Thread(target=telegram_loop, daemon=True).start()
    threading.Thread(target=trading_loop, daemon=True).start()

    # Render는 PORT로 뜸
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
