from __future__ import annotations

import os
import time
import threading
import requests

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):
        return False

try:
    from flask import Flask, jsonify
except Exception:
    class Flask:
        def __init__(self, name):
            self.name = name

        def get(self, _path):
            def deco(fn):
                return fn
            return deco

        def run(self, host="0.0.0.0", port=8080):
            print(f"[WARN] Flask not installed. Web server disabled ({host}:{port})", flush=True)

    def jsonify(obj):
        return obj


load_dotenv()

import trader as trader_module
import ai_score_runtime_patch  # noqa: F401
import trader_ai_upgrade_patch  # noqa: F401

try:
    import filter_upgrade_runtime_patch_v1  # noqa: F401
except Exception as e:
    print(f"[BOOT] optional filter patch skipped: {e}", flush=True)

from trader import Trader


def _env_bool(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default)).strip().lower() in ("1", "true", "yes", "y", "on")


def _attach_compat_hotfix() -> None:
    """Attach compatibility methods expected by older runtime patches."""
    try:
        has_module_fn = hasattr(trader_module, "compute_signal_and_exits")
        has_class_fn = hasattr(Trader, "compute_signal_and_exits")

        if has_module_fn and not has_class_fn:
            def _compat_compute_signal_and_exits(self, symbol, side, price, mp, avoid_low_rsi=False):
                return trader_module.compute_signal_and_exits(
                    symbol, side, price, mp, avoid_low_rsi=avoid_low_rsi
                )

            Trader.compute_signal_and_exits = _compat_compute_signal_and_exits
            print("[COMPAT_HOTFIX] Trader.compute_signal_and_exits attached", flush=True)

    except Exception as e:
        print(f"[COMPAT_HOTFIX] attach failed: {e}", flush=True)


_attach_compat_hotfix()

try:
    import stability_winrate_patch_v1  # noqa: F401
except Exception as e:
    print(f"[BOOT] optional stability/winrate patch skipped: {e}", flush=True)

try:
    import signal_engine_runtime_patch_v1  # noqa: F401
except Exception as e:
    print(f"[BOOT] optional signal engine patch skipped: {e}", flush=True)

try:
    import allin_guard_experimental_patch_v1  # noqa: F401
except Exception as e:
    print(f"[BOOT] optional all-in experimental patch skipped: {e}", flush=True)

try:
    import winrate_intelligence_patch_v1  # noqa: F401
except Exception as e:
    print(f"[BOOT] optional winrate intelligence patch skipped: {e}", flush=True)

try:
    import ops_intelligence_patch_v1  # noqa: F401
except Exception as e:
    print(f"[BOOT] optional ops intelligence patch skipped: {e}", flush=True)

try:
    import freqstyle_research_patch_v1  # noqa: F401
except Exception as e:
    print(f"[BOOT] optional freqstyle research patch skipped: {e}", flush=True)

app = Flask(__name__)

BOT_TOKEN = (os.getenv("BOT_TOKEN", "") or "").strip()
CHAT_ID_ENV = (os.getenv("CHAT_ID", "") or "").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

PORT = int(os.getenv("PORT", "8080"))
POLL_TIMEOUT = int(os.getenv("TG_POLL_TIMEOUT", "25"))
POLL_SLEEP = float(os.getenv("TG_POLL_SLEEP", "0.5"))
TRADING_TICK_SEC = float(os.getenv("TRADING_TICK_SEC", "5"))

# Security defaults:
# TG_STRICT_CHAT=true means only .env CHAT_ID may control the bot.
# If CHAT_ID is empty, Telegram commands are blocked instead of binding to a random first chat.
TG_STRICT_CHAT = _env_bool("TG_STRICT_CHAT", "true")

# HEALTH_VERBOSE=false means /health does not expose detailed trading state publicly.
HEALTH_VERBOSE = _env_bool("HEALTH_VERBOSE", "false")

state = {
    "running": True,
    "last_heartbeat": None,
    "last_event": None,
    "last_error": None,
    "last_telegram": None,
    "chat_id": CHAT_ID_ENV or None,
}

if CHAT_ID_ENV:
    trader_module.CHAT_ID = CHAT_ID_ENV

trader = Trader(state)
_runtime_chat_id = CHAT_ID_ENV
trader_lock = threading.RLock()


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    s = str(value)
    if len(s) <= 4:
        return "***"
    return f"{s[:2]}***{s[-2:]}"


def _is_authorized_chat(chat_id: str) -> bool:
    """
    Rules:
    1) If CHAT_ID exists in .env, only that chat is allowed.
    2) If CHAT_ID is empty and TG_STRICT_CHAT=true, block all commands.
    3) If CHAT_ID is empty and TG_STRICT_CHAT=false, first authorized chat binds temporarily.
    """
    global _runtime_chat_id

    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return False

    if CHAT_ID_ENV:
        return chat_id == CHAT_ID_ENV

    if TG_STRICT_CHAT:
        return False

    if not _runtime_chat_id:
        return True

    return chat_id == _runtime_chat_id


@app.get("/")
def home():
    return "Bot Running"


@app.get("/health")
def health():
    base = {
        "ok": True,
        "running": bool(getattr(trader, "trading_enabled", True)),
        "last_heartbeat": state.get("last_heartbeat"),
        "last_error": state.get("last_error"),
        "telegram_configured": bool(TELEGRAM_API),
        "chat_locked": bool(CHAT_ID_ENV),
        "strict_chat": TG_STRICT_CHAT,
    }

    if not HEALTH_VERBOSE:
        return jsonify(base)

    try:
        with trader_lock:
            ps = trader.public_state() if hasattr(trader, "public_state") else {}
    except Exception as e:
        ps = {"public_state_error": str(e)}

    safe_state = dict(state)
    safe_state["chat_id"] = _mask(safe_state.get("chat_id"))

    return jsonify({**base, **safe_state, **ps})


def tg_send(msg: str, reply_markup: dict | None = None):
    global _runtime_chat_id

    print(msg, flush=True)

    if not TELEGRAM_API:
        return

    chat_id = _runtime_chat_id or CHAT_ID_ENV
    if not chat_id:
        print("[TG] chat_id unknown. Set CHAT_ID in .env.", flush=True)
        return

    payload = {"chat_id": chat_id, "text": msg}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json=payload,
            timeout=15,
            proxies=PROXIES,
        )
    except Exception as e:
        print(f"[TG] send error: {e!r}", flush=True)



def tg_reset_webhook_once() -> None:
    """Polling mode guard: remove old Telegram webhook before getUpdates loop."""
    if not TELEGRAM_API:
        return
    try:
        requests.get(
            f"{TELEGRAM_API}/deleteWebhook",
            params={"drop_pending_updates": "false"},
            timeout=10,
            proxies=PROXIES,
        )
        print("[TG] deleteWebhook requested for polling mode", flush=True)
    except Exception as e:
        print(f"[TG] deleteWebhook warning: {e!r}", flush=True)

def telegram_loop():
    global _runtime_chat_id

    offset = None

    print(
        f"[TG] loop start TELEGRAM_API={bool(TELEGRAM_API)} "
        f"CHAT_ID_ENV={bool(CHAT_ID_ENV)} TG_STRICT_CHAT={TG_STRICT_CHAT}",
        flush=True,
    )

    while True:
        try:
            if not TELEGRAM_API:
                print("[TG] TELEGRAM_API missing", flush=True)
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
                chat_id = str((msg.get("chat") or {}).get("id") or "").strip()

                if not text:
                    continue

                if not _is_authorized_chat(chat_id):
                    print(
                        f"[TG] unauthorized chat ignored chat_id={chat_id} text={text[:40]!r}. "
                        f"Set CHAT_ID in .env to allow commands.",
                        flush=True,
                    )
                    continue

                if chat_id:
                    if not _runtime_chat_id:
                        _runtime_chat_id = chat_id
                    state["chat_id"] = _runtime_chat_id or chat_id
                    trader_module.CHAT_ID = _runtime_chat_id or chat_id

                state["last_telegram"] = time.time()
                print(f"[TG] CMD: {text}", flush=True)

                try:
                    with trader_lock:
                        if hasattr(trader, "handle_command"):
                            trader.handle_command(text)
                        elif text == "/status":
                            tg_send(str(trader.public_state()))
                except Exception as e:
                    print(f"[TG] trader.handle_command crash: {e!r}", flush=True)
                    state["last_error"] = f"tg_cmd: {e}"

        except Exception as e:
            print(f"[TG] polling error: {e!r}", flush=True)
            state["last_error"] = f"polling: {e}"
            time.sleep(3)

        time.sleep(POLL_SLEEP)


def trading_loop():
    while True:
        try:
            with trader_lock:
                state["running"] = bool(getattr(trader, "trading_enabled", True))

                if state["running"]:
                    trader.tick()

                state["last_heartbeat"] = time.time()
            time.sleep(TRADING_TICK_SEC)

        except Exception as e:
            print(f"[TRADING] loop crash: {e!r}", flush=True)
            state["last_error"] = f"trading_loop: {e}"
            time.sleep(3)


if __name__ == "__main__":
    print("[BOOT] Bot starting...", flush=True)
    tg_reset_webhook_once()
    threading.Thread(target=telegram_loop, daemon=True).start()
    threading.Thread(target=trading_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
