# main.py (FULL COPY-PASTE) - FULL QUANT Trader 호환본
import os
import time
import threading
import requests
from flask import Flask, jsonify

from trader import Trader  # ✅ 네가 붙여넣은 FULL QUANT trader.py

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
CHAT_ID = os.getenv("CHAT_ID", "")  # 설정하면 해당 채팅만 수신
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""


@app.get("/")
def home():
    return "Bot Running"


@app.get("/health")
def health():
    return jsonify({**state, **(trader.public_state() if hasattr(trader, "public_state") else {})})


def _tg_get_updates(offset=None, timeout=25):
    if not TELEGRAM_API:
        return {"ok": False, "result": []}
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=timeout + 10)
    return r.json()


def telegram_loop():
    if not TELEGRAM_API:
        # 텔레그램 없이도 루프는 돌 수 있게
        try:
            trader.notify("⚠️ BOT_TOKEN 없음 → 텔레그램 폴링 비활성")
        except:
            pass
        return

    offset = None
    trader.notify("🤖 텔레그램 폴링 시작. /help 입력 가능")

    while True:
        try:
            data = _tg_get_updates(offset=offset, timeout=25)
            if not data.get("ok"):
                state["last_telegram"] = f"getUpdates not ok: {str(data)[:120]}"
                time.sleep(2)
                continue

            for upd in data.get("result", []):
                offset = (upd.get("update_id") or 0) + 1

                msg = upd.get("message") or upd.get("edited_message") or {}
                text = (msg.get("text") or "").strip()
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id") or "")

                if not text:
                    continue

                # ✅ CHAT_ID가 설정돼 있으면 그 채팅만 받음
                if CHAT_ID and chat_id != str(CHAT_ID):
                    continue

                state["last_telegram"] = text

                # ✅ FULL QUANT Trader는 tg_send가 아니라 handle_command/notify 사용
                trader.handle_command(text)

        except Exception as e:
            state["last_telegram"] = f"telegram_loop err: {e}"
            try:
                trader.err_throttled(f"❌ 텔레그램 루프 에러: {e}")
            except:
                pass
            time.sleep(2)


def trading_loop():
    state["running"] = True
    try:
        trader.notify("🤖 봇 시작됨")
    except:
        pass

    loop_seconds = int(os.getenv("LOOP_SECONDS", "20"))

    while True:
        try:
            state["last_heartbeat"] = time.strftime("%Y-%m-%d %H:%M:%S")
            trader.tick()
            state["last_error"] = None
            state["last_event"] = trader.state.get("last_event")
        except Exception as e:
            state["last_error"] = str(e)
            try:
                trader.err_throttled(f"❌ 트레이딩 루프 에러: {e}")
            except:
                pass
        time.sleep(loop_seconds)


if __name__ == "__main__":
    # 텔레그램 폴링 스레드
    t1 = threading.Thread(target=telegram_loop, daemon=True)
    t1.start()

    # 트레이딩 루프 스레드
    t2 = threading.Thread(target=trading_loop, daemon=True)
    t2.start()

    # Railway/Render는 PORT 환경변수 사용
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
