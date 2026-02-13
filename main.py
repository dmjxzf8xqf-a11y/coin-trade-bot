import time
import threading
from flask import Flask, jsonify
from trader import Trader
from config import LOOP_SECONDS

app = Flask(__name__)

state = {
    "running": False,
    "last_heartbeat": None,
    "last_error": None,
}

trader = Trader(state)

@app.get("/")
def home():
    return "Bot Running"

@app.get("/health")
def health():
    return jsonify({**state, **trader.public_state()})

def loop():
    state["running"] = True
    trader.notify("🤖 Bot Started")

    while True:
        try:
            state["last_heartbeat"] = time.strftime("%Y-%m-%d %H:%M:%S")
            trader.tick()
        except Exception as e:
            state["last_error"] = str(e)
            trader.notify(f"❌ {e}")
        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8000)
