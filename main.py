import os
import time
import threading
import requests
from flask import Flask, jsonify

from trader import Trader

app = Flask(__name__)

# -------------------------
# ENV
# -------------------------
BOT_TOKEN = (os.getenv("BOT_TOKEN", "") or "").strip()
CHAT_ID_ENV = (os.getenv("CHAT_ID", "") or "").strip()  # 비워도 됨 (첫 대화한 chat_id 자동 저장)
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

PORT = int(os.getenv("PORT", "8080"))
POLL_TIMEOUT = int(os.getenv("TG_POLL_TIMEOUT", "25"))
POLL_SLEEP = float(os.getenv("TG_POLL_SLEEP", "0.5"))
TRADING_TICK_SEC = float(os.getenv("TRADING_TICK_SEC", "5"))


state = {
    "running": False,
    "last_heartbeat": None,
    "last_event": None,
    "last_error": None,
    "last_telegram": None,
}

trader = Trader(state)

# 런타임 chat_id (고정 CHAT_ID 없으면 여기에 저장)
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
# Telegram send (reply_markup 지원 핵심)
# -------------------------
def tg_send(msg: str, reply_markup: dict | None = None):
    global _runtime_chat_id
    print(msg, flush=True)

    if not TELEGRAM_API:
        return

    chat_id = _runtime_chat_id
    if not chat_id:
        # 아직 chat_id를 모르면 전송 불가 (첫 메시지 들어오면 저장됨)
        print("⚠️ TG chat_id unknown (CHAT_ID env empty). Waiting first incoming message.", flush=True)
        return

    payload = {"chat_id": chat_id, "text": msg}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup  # ✅ dict 그대로 (json=payload로 전송)

    try:
        # ✅ IMPORTANT: json=payload (data= 쓰면 reply_markup가 깨지는 경우 많음)
        requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=15, proxies=PROXIES)
    except Exception as e:
        print("❌ TG send error:", repr(e), flush=True)


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

                # ✅ chat_id 자동 저장 (CHAT_ID env 비워도 작동)
                try:
                    chat_id = str((msg.get("chat") or {}).get("id") or "")
                    if chat_id and (not _runtime_chat_id):
                        _runtime_chat_id = chat_id
                        print(f"✅ TG chat_id captured: {_runtime_chat_id}", flush=True)
                except Exception:
                    pass

                if not text:
                    continue

                state["last_telegram"] = time.time()
                print(f"📩 CMD: {text}", flush=True)

                # ✅ 핵심: trader의 명령 처리로 넘김 (여기에 /ui, /help, 버튼 로직 다 있음)
                try:
                    if hasattr(trader, "handle_command"):
                        trader.handle_command(text)
                    else:
                        # fallback: 최소한의 핸들링
                        if text == "/status":
                            tg_send(str(trader.public_state()))
                except Exception as e:
                    print("❌ trader.handle_command crash:", repr(e), flush=True)
                    state["last_error"] = f"tg_cmd: {e}"

        except Exception as e:
            print("❌ polling error:", repr(e), flush=True)
            state["last_error"] = f"polling: {e}"
            time.sleep(3)

        time.sleep(POLL_SLEEP)


def trading_loop():
    while True:
        try:
            if state["running"]:
                trader.tick()
            state["last_heartbeat"] = time.time()
            time.sleep(TRADING_TICK_SEC)
        except Exception as e:
            print("❌ trading loop crash:", repr(e), flush=True)
            state["last_error"] = f"trading_loop: {e}"
            time.sleep(3)


if __name__ == "__main__":
    print("🚀 Bot starting...", flush=True)

    threading.Thread(target=telegram_loop, daemon=True).start()
    threading.Thread(target=trading_loop, daemon=True).start()

    # ✅ Railway는 while True로 app.run 반복 돌릴 필요 없음
    app.run(host="0.0.0.0", port=PORT)
