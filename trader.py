# trader.py (FINAL++: LONG+SHORT + winrate+reasons + PnL w/ fee+slippage + partial TP + time filter)
import os, time, json, hmac, hashlib, requests
from urllib.parse import urlencode
from datetime import datetime, timezone
from config import *

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

def _cfg(name, default):
    try:
        return globals()[name]
    except Exception:
        return default

# =========================
# CONFIG (env overrides)
# =========================
BYBIT_BASE_URL = (os.getenv("BYBIT_BASE_URL") or _cfg("BYBIT_BASE_URL", "https://api.bybit.com")).rstrip("/")
SYMBOL = os.getenv("SYMBOL", _cfg("SYMBOL", "BTCUSDT"))
CATEGORY = os.getenv("CATEGORY", _cfg("CATEGORY", "linear"))
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", _cfg("ACCOUNT_TYPE", "UNIFIED"))

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", _cfg("BYBIT_API_KEY", ""))
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", _cfg("BYBIT_API_SECRET", ""))

BOT_TOKEN = os.getenv("BOT_TOKEN", _cfg("BOT_TOKEN", ""))
CHAT_ID = os.getenv("CHAT_ID", _cfg("CHAT_ID", ""))

DRY_RUN = str(os.getenv("DRY_RUN", str(_cfg("DRY_RUN", "true")))).lower() in ("1","true","yes","y")
MODE = os.getenv("MODE", _cfg("MODE", "SAFE")).upper()  # SAFE / AGGRO

ALLOW_LONG = str(os.getenv("ALLOW_LONG", "true")).lower() in ("1","true","yes","y")
ALLOW_SHORT = str(os.getenv("ALLOW_SHORT", "true")).lower() in ("1","true","yes","y")

ENTRY_INTERVAL = str(os.getenv("ENTRY_INTERVAL", str(_cfg("ENTRY_INTERVAL", "15"))))
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", str(_cfg("KLINE_LIMIT", 240))))

EMA_FAST = int(os.getenv("EMA_FAST", str(_cfg("EMA_FAST", 20))))
EMA_SLOW = int(os.getenv("EMA_SLOW", str(_cfg("EMA_SLOW", 50))))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", str(_cfg("RSI_PERIOD", 14))))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", str(_cfg("ATR_PERIOD", 14))))

LEVERAGE_SAFE = int(os.getenv("LEVERAGE_SAFE", str(_cfg("LEVERAGE_SAFE", 3))))
LEVERAGE_AGGRO = int(os.getenv("LEVERAGE_AGGRO", str(_cfg("LEVERAGE_AGGRO", 8))))

ORDER_USDT_SAFE = float(os.getenv("ORDER_USDT_SAFE", str(_cfg("ORDER_USDT_SAFE", 5))))
ORDER_USDT_AGGRO = float(os.getenv("ORDER_USDT_AGGRO", str(_cfg("ORDER_USDT_AGGRO", 12))))

STOP_ATR_MULT_SAFE = float(os.getenv("STOP_ATR_MULT_SAFE", "1.8"))
STOP_ATR_MULT_AGGRO = float(os.getenv("STOP_ATR_MULT_AGGRO", "1.3"))
TP_R_MULT_SAFE = float(os.getenv("TP_R_MULT_SAFE", "1.5"))
TP_R_MULT_AGGRO = float(os.getenv("TP_R_MULT_AGGRO", "2.0"))

TRAIL_ON = str(os.getenv("TRAIL_ON","true")).lower() in ("1","true","yes","y")
TRAIL_ATR_MULT = float(os.getenv("TRAIL_ATR_MULT","1.0"))

ENTER_SCORE_SAFE = int(os.getenv("ENTER_SCORE_SAFE","65"))
ENTER_SCORE_AGGRO = int(os.getenv("ENTER_SCORE_AGGRO","55"))
EXIT_SCORE_DROP = int(os.getenv("EXIT_SCORE_DROP","35"))

ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC","60"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC","1200"))
TIME_EXIT_MIN = int(os.getenv("TIME_EXIT_MIN","360"))

MAX_ENTRIES_PER_DAY = int(os.getenv("MAX_ENTRIES_PER_DAY","6"))
MAX_CONSEC_LOSSES = int(os.getenv("MAX_CONSEC_LOSSES","3"))

# =========================
# UPGRADE 1) fee/slippage PnL estimate
# =========================
FEE_RATE = float(os.getenv("FEE_RATE", "0.0006"))       # per-side fee estimate
SLIPPAGE_BPS = float(os.getenv("SLIPPAGE_BPS", "5"))    # bps (5 = 0.05%)

# =========================
# UPGRADE 2) partial take profit
# =========================
PARTIAL_TP_ON = str(os.getenv("PARTIAL_TP_ON","true")).lower() in ("1","true","yes","y")
PARTIAL_TP_PCT = float(os.getenv("PARTIAL_TP_PCT", "0.5"))     # 0.5 = 50% close
TP1_FRACTION = float(os.getenv("TP1_FRACTION", "0.5"))         # TP distance fraction for TP1
MOVE_STOP_TO_BE_ON_TP1 = str(os.getenv("MOVE_STOP_TO_BE_ON_TP1","true")).lower() in ("1","true","yes","y")

# =========================
# UPGRADE 3) time-of-day filter (entry only, UTC)
# =========================
TRADE_HOURS_UTC = os.getenv("TRADE_HOURS_UTC", "00-23")  # "01-23" or "22-03"

# =========================
# PRICE FALLBACK
# =========================
BINANCE = "https://api.binance.com/api/v3/ticker/price"
COINBASE = "https://api.coinbase.com/v2/prices/BTC-USD/spot"

def _now_utc():
    return datetime.now(timezone.utc)

def _day_key_utc():
    return _now_utc().strftime("%Y-%m-%d")

def _utc_hour():
    return int(_now_utc().strftime("%H"))

def _parse_trade_hours(spec: str):
    try:
        a, b = spec.split("-", 1)
        start = max(0, min(23, int(a.strip())))
        end = max(0, min(23, int(b.strip())))
        return start, end
    except Exception:
        return 0, 23

def entry_allowed_now_utc():
    start, end = _parse_trade_hours(TRADE_HOURS_UTC)
    h = _utc_hour()
    if start <= end:
        return start <= h <= end
    return (h >= start) or (h <= end)

def fallback_price():
    try:
        return float(requests.get(BINANCE, params={"symbol": SYMBOL}, headers=HEADERS, timeout=10, proxies=PROXIES).json()["price"])
    except Exception:
        pass
    try:
        if SYMBOL.upper() == "BTCUSDT":
            return float(requests.get(COINBASE, headers=HEADERS, timeout=10, proxies=PROXIES).json()["data"]["amount"])
    except Exception:
        pass
    return 0.0

# =========================
# BYBIT API helpers
# =========================
def _safe_json(r: requests.Response):
    text = r.text or ""
    if not text.strip():
        return {"_non_json": True, "raw": "", "status": r.status_code}
    try:
        return r.json()
    except Exception:
        return {"_non_json": True, "raw": text[:800], "status": r.status_code}

def _sign_get(params: dict):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = urlencode(sorted(params.items()))
    pre = ts + BYBIT_API_KEY + recv + query
    sign = hmac.new(BYBIT_API_SECRET.encode(), pre.encode(), hashlib.sha256).hexdigest()
    headers = {
        **HEADERS,
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
    }
    return headers, query

def _sign_post(body: dict):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body_str = json.dumps(body, separators=(",", ":"))
    pre = ts + BYBIT_API_KEY + recv + body_str
    sign = hmac.new(BYBIT_API_SECRET.encode(), pre.encode(), hashlib.sha256).hexdigest()
    headers = {
        **HEADERS,
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
    }
    return headers, body_str

def bybit_get(path: str, params: dict):
    if DRY_RUN:
        return {"retCode": 0, "retMsg": "DRY_RUN", "result": {}}
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        raise Exception("Missing BYBIT_API_KEY / BYBIT_API_SECRET")
    h, query = _sign_get(params)
    url = BYBIT_BASE_URL + path + ("?" + query if query else "")
    r = requests.get(url, headers=h, timeout=15, proxies=PROXIES)
    data = _safe_json(r)
    if r.status_code == 403:
        raise Exception(f"Bybit 403 blocked base={BYBIT_BASE_URL} proxy={'ON' if PROXIES else 'OFF'} raw={data.get('raw')}")
    if r.status_code == 407:
        raise Exception("Proxy auth failed (407)")
    if data.get("_non_json"):
        raise Exception(f"Bybit non-json status={data.get('status')} raw={data.get('raw')}")
    return data

def bybit_post(path: str, body: dict):
    if DRY_RUN:
        return {"retCode": 0, "retMsg": "DRY_RUN", "result": {}}
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        raise Exception("Missing BYBIT_API_KEY / BYBIT_API_SECRET")
    h, b = _sign_post(body)
    url = BYBIT_BASE_URL + path
    r = requests.post(url, headers=h, data=b, timeout=15, proxies=PROXIES)
    data = _safe_json(r)
    if r.status_code == 403:
        raise Exception(f"Bybit 403 blocked base={BYBIT_BASE_URL} proxy={'ON' if PROXIES else 'OFF'} raw={data.get('raw')}")
    if r.status_code == 407:
        raise Exception("Proxy auth failed (407)")
    if data.get("_non_json"):
        raise Exception(f"Bybit non-json status={data.get('status')} raw={data.get('raw')}")
    return data

def get_price():
    if DRY_RUN:
        p = fallback_price()
        if p <= 0:
            raise Exception("fallback price failed")
        return float(p)
    r = bybit_get("/v5/market/tickers", {"category": CATEGORY, "symbol": SYMBOL})
    lst = (((r.get("result") or {}).get("list")) or [])
    if not lst:
        raise Exception("tickers empty")
    t = lst[0]
    return float(t.get("markPrice") or t.get("lastPrice"))

def get_klines(interval: str, limit: int):
    if DRY_RUN:
        import random
        price = get_price()
        out=[]
        for _ in range(limit):
            h=price*(1+random.uniform(0,0.002))
            l=price*(1-random.uniform(0,0.002))
            c=price*(1+random.uniform(-0.001,0.001))
            out.append([0,0,f"{h}",f"{l}",f"{c}",0])
            price=c
        return out
    r = bybit_get("/v5/market/kline", {"category": CATEGORY, "symbol": SYMBOL, "interval": str(interval), "limit": int(limit)})
    return (r.get("result") or {}).get("list") or []

def get_position():
    if DRY_RUN:
        return {"has_pos": False}
    r = bybit_get("/v5/position/list", {"category": CATEGORY, "symbol": SYMBOL})
    items = (((r.get("result") or {}).get("list")) or [])
    if not items:
        return {"has_pos": False}
    picked = None
    for it in items:
        if (it.get("symbol") or "").upper() == SYMBOL.upper():
            picked = it
            break
    if picked is None:
        picked = items[0]
    size = float(picked.get("size") or 0)
    side = picked.get("side")  # Buy/Sell
    avg = float(picked.get("avgPrice") or picked.get("entryPrice") or 0)
    return {"has_pos": size > 0, "side": side, "size": size, "avgPrice": avg}

def set_leverage(x: int):
    body = {"category": CATEGORY, "symbol": SYMBOL, "buyLeverage": str(x), "sellLeverage": str(x)}
    return bybit_post("/v5/position/set-leverage", body)

def order_market(side: str, qty: float, reduce_only=False):
    body = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "IOC",
    }
    if reduce_only:
        body["reduceOnly"] = True

    resp = bybit_post("/v5/order/create", body)

    # ✅ 실패면 여기서 멈춤 + 이유를 텔레그램/로그로 올리게 됨
    if (resp or {}).get("retCode") != 0:
        raise Exception(f"ORDER FAILED → retCode={resp.get('retCode')} retMsg={resp.get('retMsg')}")

    return resp

def qty_from_order_usdt(order_usdt, lev, price):
    if order_usdt <= 0 or price <= 0:
        return 0.0

    raw_qty = (order_usdt * lev) / price

    # 🔥 코인별 최소 수량 단위
    if "BTC" in SYMBOL:
        step = 0.001
    else:
        step = 0.01   # ETH & 대부분 알트

    qty = (raw_qty // step) * step
    return round(qty, 6)

# =========================
# INDICATORS + SIGNAL
# =========================
def ema(data, p):
    k = 2/(p+1)
    e = data[0]
    for v in data[1:]:
        e = v*k + e*(1-k)
    return e

def rsi(data, p=14):
    if len(data) < p + 1:
        return None
    gain=loss=0.0
    for i in range(-p,0):
        diff=data[i]-data[i-1]
        if diff>0: gain+=diff
        else: loss-=diff
    rs=gain/(loss+1e-9)
    return 100-(100/(1+rs))

def atr(high,low,close,p=14):
    if len(close) < p + 1:
        return None
    trs=[]
    for i in range(-p,0):
        trs.append(max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1])))
    return sum(trs)/p

def ai_score(price, ef, es, r, a):
    score=0
    if price>es: score+=25
    if price>ef: score+=20
    if r is not None and 45<r<65: score+=20
    if ef>es: score+=20
    if (a/price)<0.02: score+=15
    return int(score)

def confidence_label(score):
    if score >= 85: return "🔥 매우높음"
    if score >= 70: return "✅ 높음"
    if score >= 55: return "⚠️ 보통"
    return "❌ 낮음"

def mode_params():
    m = os.getenv("MODE", MODE).upper()
    if m == "AGGRO":
        return {
            "lev": LEVERAGE_AGGRO,
            "order_usdt": ORDER_USDT_AGGRO,
            "stop_atr": STOP_ATR_MULT_AGGRO,
            "tp_r": TP_R_MULT_AGGRO,
            "enter_score": ENTER_SCORE_AGGRO,
        }
    return {
        "lev": LEVERAGE_SAFE,
        "order_usdt": ORDER_USDT_SAFE,
        "stop_atr": STOP_ATR_MULT_SAFE,
        "tp_r": TP_R_MULT_SAFE,
        "enter_score": ENTER_SCORE_SAFE,
    }

def build_reason(side, price, ef, es, r, a, score, trend_ok, enter_ok):
    return (
        f"[{side}] 근거\n"
        f"- price={price:.2f}\n"
        f"- EMA{EMA_FAST}={ef:.2f}, EMA{EMA_SLOW}={es:.2f}\n"
        f"- RSI{RSI_PERIOD}={r:.1f}\n"
        f"- ATR{ATR_PERIOD}={a:.2f}\n"
        f"- score={score} ({confidence_label(score)})\n"
        f"- trend_ok={trend_ok} | enter_ok={enter_ok}\n"
    )

def compute_signal_and_exits(side: str, price: float):
    kl = get_klines(ENTRY_INTERVAL, KLINE_LIMIT)
    if len(kl) < max(120, EMA_SLOW * 3):
        ef = es = price
        r = 50.0
        a = price * 0.005
        mp = mode_params()
        score = 50
        trend_ok = True
        enter_ok = score >= mp["enter_score"]
        stop_dist = a * mp["stop_atr"]
        tp_dist = stop_dist * mp["tp_r"]
        sl = price - stop_dist if side=="LONG" else price + stop_dist
        tp = price + tp_dist if side=="LONG" else price - tp_dist
        reason = build_reason(side, price, ef, es, r, a, score, trend_ok, enter_ok) + "- note=kline 부족\n"
        return False, reason, score, sl, tp, a

    kl = list(reversed(kl))  # newest last
    closes=[float(x[4]) for x in kl]
    highs=[float(x[2]) for x in kl]
    lows =[float(x[3]) for x in kl]

    ef=ema(closes[-EMA_FAST*3:], EMA_FAST)
    es=ema(closes[-EMA_SLOW*3:], EMA_SLOW)
    r=rsi(closes, RSI_PERIOD)
    a=atr(highs, lows, closes, ATR_PERIOD)

    if r is None: r = 50.0
    if a is None: a = price * 0.005

    score = ai_score(price, ef, es, r, a)
    mp = mode_params()
    enter_ok = score >= mp["enter_score"]

    if side == "LONG":
        trend_ok = (price > es) and (ef > es)
    else:
        trend_ok = (price < es) and (ef < es)

    ok = enter_ok and trend_ok
    reason = build_reason(side, price, ef, es, r, a, score, trend_ok, enter_ok)

    stop_dist = a * mp["stop_atr"]
    tp_dist = stop_dist * mp["tp_r"]
    if side == "LONG":
        sl = price - stop_dist
        tp = price + tp_dist
    else:
        sl = price + stop_dist
        tp = price - tp_dist

    return ok, reason, score, sl, tp, a

# =========================
# PnL estimate (fee + slippage)
# =========================
def _est_round_trip_cost_frac():
    # fee both sides + slippage both sides
    slip = (SLIPPAGE_BPS / 10000.0)
    return (2 * FEE_RATE) + (2 * slip)

def estimate_pnl_usdt(side: str, entry_price: float, exit_price: float, notional_usdt: float):
    # price move fraction
    if entry_price <= 0 or notional_usdt <= 0:
        return 0.0
    raw_move = (exit_price - entry_price) / entry_price
    if side == "SHORT":
        raw_move = -raw_move
    gross = notional_usdt * raw_move
    cost = notional_usdt * _est_round_trip_cost_frac()
    return gross - cost

# =========================
# TRADER
# =========================
class Trader:
    def __init__(self, state=None):
        self.state = state if isinstance(state, dict) else {}
        self.trading_enabled = True

        self.position=None     # "LONG"/"SHORT"
        self.entry_price=None
        self.entry_ts=None

        self.stop_price=None
        self.tp_price=None
        self.trail_price=None

        # partial TP state
        self.tp1_price=None
        self.tp1_done=False
        self.qty_est=None  # for DRY_RUN bookkeeping only

        # stats
        self.win=0
        self.loss=0
        self.day_profit=0.0
        self.consec_losses=0
        self._day_key=None
        self._day_entries=0

        self._cooldown_until=0
        self._last_alert_ts=0
        self._last_err_ts=0
        self._lev_set_for_mode=None

        # last used params for pnl estimate
        self._last_order_usdt=None
        self._last_lev=None

    # ---- notify ----
    def notify(self, msg):
        print(msg)
        if BOT_TOKEN and CHAT_ID:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data={"chat_id": CHAT_ID, "text": msg},
                    timeout=10
                )
            except Exception:
                pass

    def notify_throttled(self, msg, min_sec=None):
        cooldown = min_sec if min_sec is not None else ALERT_COOLDOWN_SEC
        if time.time() - self._last_alert_ts >= cooldown:
            self._last_alert_ts = time.time()
            self.notify(msg)

    def err_throttled(self, msg):
        if time.time() - self._last_err_ts >= max(ALERT_COOLDOWN_SEC, 120):
            self._last_err_ts = time.time()
            self.notify(msg)

    def _reset_day(self):
        dk = _day_key_utc()
        if self._day_key != dk:
            self._day_key = dk
            self._day_entries = 0
            self.day_profit = 0.0
            self.win = 0
            self.loss = 0
            self.consec_losses = 0

    def _sync_real_position(self):
        if DRY_RUN:
            return
        p = get_position()
        if p.get("has_pos"):
            side = p.get("side")
            self.position = "LONG" if side == "Buy" else "SHORT"
            self.entry_price = float(p.get("avgPrice") or self.entry_price or 0.0)
            if not self.entry_ts:
                self.entry_ts = time.time()
        else:
            self.position = None
            self.entry_price = None
            self.entry_ts = None
            self.stop_price = None
            self.tp_price = None
            self.trail_price = None
            self.tp1_price = None
            self.tp1_done = False
            self.qty_est = None

    # ===== Telegram commands =====
    def handle_command(self, text: str):
        cmd = (text or "").strip()

        if cmd == "/start":
            self.trading_enabled = True
            self.notify("✅ 거래 ON")
            return

        if cmd == "/stop":
            self.trading_enabled = False
            self.notify("🛑 거래 OFF")
            return

        if cmd == "/safe":
            os.environ["MODE"] = "SAFE"
            self._lev_set_for_mode = None
            self.notify("🛡 SAFE 모드로 전환")
            return

        if cmd in ("/aggro", "/attack"):
            os.environ["MODE"] = "AGGRO"
            self._lev_set_for_mode = None
            self.notify("⚔️ AGGRO 모드로 전환")
            return

        if cmd == "/status":
            self.notify(self.status_text())
            return

        if cmd == "/buy":
            self.manual_enter("LONG")
            return

        if cmd == "/short":
            self.manual_enter("SHORT")
            return

        if cmd == "/sell":
            self.manual_exit("MANUAL SELL")
            return

        if cmd == "/panic":
            self.manual_exit("PANIC", force=True)
            self.trading_enabled = False
            self.notify("🚨 PANIC: 청산 시도 + 거래 OFF")
            return

        if cmd in ("/help", "help"):
            self.notify(self.help_text())
            return

        if cmd.startswith("/"):
            self.notify("❓ 모르는 명령. /help")
            return

    def help_text(self):
        return (
            "📌 명령어\n"
            "/start /stop\n"
            "/safe /aggro(= /attack)\n"
            "/status\n"
            "/buy (롱 수동)\n"
            "/short (숏 수동)\n"
            "/sell (청산)\n"
            "/panic (강제청산+OFF)\n"
        )

    def status_text(self):
        total = self.win + self.loss
        winrate = (self.win / total * 100) if total else 0.0
        mp = mode_params()
        lines = []
        lines.append(f"🧠 DRY_RUN={DRY_RUN} | ON={self.trading_enabled} | MODE={os.getenv('MODE','SAFE')}")
        lines.append(f"⚙️ lev={mp['lev']} | order_usdt={mp['order_usdt']} | enter_score>={mp['enter_score']}")
        lines.append(f"⏰ entry_hours_utc={TRADE_HOURS_UTC} | allowed_now={entry_allowed_now_utc()}")
        lines.append(f"💸 fee={FEE_RATE:.4%}/side | slip={SLIPPAGE_BPS:.1f}bps/side | partialTP={PARTIAL_TP_ON}({PARTIAL_TP_PCT:.0%})")
        lines.append(f"🌐 base={BYBIT_BASE_URL} | proxy={'ON' if PROXIES else 'OFF'}")
        if self.state.get("last_price") is not None:
            lines.append(f"💵 price={self.state.get('last_price'):.2f}")
        lines.append(f"📍 POS={self.position or 'None'} entry={self.entry_price}")
        if self.stop_price and self.tp_price:
            lines.append(f"🎯 stop={self.stop_price:.2f} | tp={self.tp_price:.2f} | tp1={self.tp1_price} | trail={self.trail_price}")
        lines.append(f"📈 day_profit≈{self.day_profit:.2f} | winrate={winrate:.1f}% (W{self.win}/L{self.loss}) | consec_losses={self.consec_losses}")
        if self.state.get("entry_reason"):
            lines.append(f"🧠 근거:\n{self.state.get('entry_reason')}")
        if self.state.get("last_event"):
            lines.append(f"📝 last={self.state.get('last_event')}")
        return "\n".join(lines)

    # ===== Orders =====
    def _ensure_leverage(self):
        mp = mode_params()
        m = os.getenv("MODE", MODE).upper()
        if self._lev_set_for_mode != m and not DRY_RUN:
            set_leverage(int(mp["lev"]))
            self._lev_set_for_mode = m

    def _enter(self, side: str, price: float, reason: str, sl: float, tp: float, atr_val: float):
        mp = mode_params()
        lev = mp["lev"]
        order_usdt = mp["order_usdt"]
        qty = qty_from_order_usdt(order_usdt, lev, price)
        if qty <= 0:
            raise Exception("qty<=0")

        # remember for pnl estimate
        self._last_order_usdt = float(order_usdt)
        self._last_lev = float(lev)

        if not DRY_RUN:
            order_market("Buy" if side == "LONG" else "Sell", qty)

        self.position = side
        self.entry_price = price
        self.entry_ts = time.time()
        self.stop_price = sl
        self.tp_price = tp
        self.trail_price = None

        # partial TP target
        self.tp1_done = False
        if PARTIAL_TP_ON:
            if side == "LONG":
                self.tp1_price = price + (tp - price) * TP1_FRACTION
            else:
                self.tp1_price = price - (price - tp) * TP1_FRACTION
        else:
            self.tp1_price = None

        self._cooldown_until = time.time() + COOLDOWN_SEC
        self._day_entries += 1
        self.state["entry_reason"] = reason

        self.notify(f"✅ ENTER {side} qty={qty}\n{reason}\n⏳ stop={sl:.2f} tp={tp:.2f} tp1={self.tp1_price}")

    def manual_enter(self, side: str):
        try:
            self._reset_day()
            self._sync_real_position()
            if self.position:
                self.notify("⚠️ 이미 포지션 있음")
                return

            price = get_price()
            self._ensure_leverage()

            ok, reason, score, sl, tp, a = compute_signal_and_exits(side, price)
            self._enter(side, price, reason + "- manual=True\n", sl, tp, a)

        except Exception as e:
            self.err_throttled(f"❌ manual enter 실패: {e}")

    def _close_qty(self, close_qty: float, side: str):
        # side here is current position side ("LONG"/"SHORT")
        if DRY_RUN:
            return
        if close_qty <= 0:
            return
        order_market("Sell" if side == "LONG" else "Buy", close_qty, reduce_only=True)

    def manual_exit(self, why: str, force=False):
        try:
            self._sync_real_position()
            if not self.position and not force:
                self.notify("⚠️ 포지션 없음")
                return

            price = get_price()

            # close all
            if not DRY_RUN and self.position:
                p = get_position()
                qty = float(p.get("size") or 0.0)
                if qty > 0:
                    self._close_qty(qty, self.position)

            # PnL estimate using notional = order_usdt * lev (approx)
            if self.entry_price and self.position:
                notional = float(self._last_order_usdt or 0) * float(self._last_lev or 0)
                pnl_est = estimate_pnl_usdt(self.position, self.entry_price, price, notional)
                self.day_profit += pnl_est
                if pnl_est >= 0:
                    self.win += 1
                    self.consec_losses = 0
                else:
                    self.loss += 1
                    self.consec_losses += 1

            self.notify(f"✅ EXIT({why}) price={price:.2f} day_profit≈{self.day_profit:.2f} (W{self.win}/L{self.loss})")

            self.position = None
            self.entry_price = None
            self.entry_ts = None
            self.stop_price = None
            self.tp_price = None
            self.trail_price = None
            self.tp1_price = None
            self.tp1_done = False

            self._cooldown_until = time.time() + COOLDOWN_SEC

        except Exception as e:
            self.err_throttled(f"❌ manual exit 실패: {e}")

    # =========================
    # Main tick
    # =========================
    def tick(self):
        self._reset_day()

        self.state["trading_enabled"] = self.trading_enabled
        self.state["mode"] = os.getenv("MODE", "SAFE")
        self.state["bybit_base"] = BYBIT_BASE_URL
        self.state["proxy"] = "ON" if PROXIES else "OFF"

        if not self.trading_enabled:
            self.state["last_event"] = "거래 OFF"
            return

        if self.consec_losses >= MAX_CONSEC_LOSSES:
            self.notify_throttled("🛑 연속 손실 제한 도달. 거래 중지")
            self.trading_enabled = False
            self.state["last_event"] = "STOP: consec losses"
            return

        # price
        try:
            price = get_price()
            self.state["last_price"] = price
        except Exception as e:
            self.err_throttled(f"❌ price 실패: {e}")
            return

        # sync real position
        try:
            self._sync_real_position()
        except Exception as e:
            self.err_throttled(f"❌ position sync 실패: {e}")

        # ================= ENTRY =================
        if not self.position:
            if time.time() < self._cooldown_until:
                self.state["last_event"] = "대기: cooldown"
                return
            if self._day_entries >= MAX_ENTRIES_PER_DAY:
                self.state["last_event"] = "대기: 일일 진입 제한"
                return

            # time filter (ENTRY only)
            if not entry_allowed_now_utc():
                self.state["last_event"] = f"대기: 시간필터(UTC {TRADE_HOURS_UTC})"
                return

            try:
                self._ensure_leverage()
            except Exception as e:
                self.err_throttled(f"❌ leverage 설정 실패: {e}")
                return

            best = None
            if ALLOW_LONG:
                ok, reason, score, sl, tp, a = compute_signal_and_exits("LONG", price)
                best = ("LONG", ok, reason, score, sl, tp, a)
            if ALLOW_SHORT:
                ok2, reason2, score2, sl2, tp2, a2 = compute_signal_and_exits("SHORT", price)
                if (best is None) or (score2 > best[3]):
                    best = ("SHORT", ok2, reason2, score2, sl2, tp2, a2)

            if best is None:
                self.state["last_event"] = "대기: 방향 없음"
                return

            side, ok, reason, score, sl, tp, a = best
            self.state["entry_reason"] = reason

            if not ok:
                self.state["last_event"] = f"대기: score={score}"
                return

            try:
                self._enter(side, price, reason, sl, tp, a)
                self.state["last_event"] = f"ENTER {side}"
            except Exception as e:
                self.err_throttled(f"❌ entry 실패: {e}")
            return

        # ================= EXIT / MANAGE =================
        side = self.position
        ok, reason, score, sl_new, tp_new, a = compute_signal_and_exits(side, price)

        # trailing
        if TRAIL_ON and a is not None:
            dist = a * TRAIL_ATR_MULT
            if side == "LONG":
                cand = price - dist
                if self.trail_price is None or cand > self.trail_price:
                    self.trail_price = cand
            else:
                cand = price + dist
                if self.trail_price is None or cand < self.trail_price:
                    self.trail_price = cand

        eff_stop = self.stop_price
        if self.trail_price is not None:
            if side == "LONG":
                eff_stop = max(eff_stop, self.trail_price)
            else:
                eff_stop = min(eff_stop, self.trail_price)

        # time exit
        if self.entry_ts and (time.time() - self.entry_ts) > (TIME_EXIT_MIN * 60):
            self.manual_exit("TIME EXIT")
            return

        # score drop exit
        if score <= EXIT_SCORE_DROP:
            self.manual_exit(f"SCORE DROP {score}")
            return

        # PARTIAL TP
        if PARTIAL_TP_ON and (not self.tp1_done) and self.tp1_price is not None and (not DRY_RUN):
            try:
                p = get_position()
                qty_total = float(p.get("size") or 0.0)
                if qty_total > 0:
                    hit_tp1 = (price >= self.tp1_price) if side=="LONG" else (price <= self.tp1_price)
                    if hit_tp1:
                        close_qty = qty_total * float(PARTIAL_TP_PCT)
                        self._close_qty(close_qty, side)
                        self.tp1_done = True
                        if MOVE_STOP_TO_BE_ON_TP1 and self.entry_price is not None:
                            if side == "LONG":
                                self.stop_price = max(self.stop_price, self.entry_price)
                            else:
                                self.stop_price = min(self.stop_price, self.entry_price)
                        self.notify(f"🧩 PARTIAL TP hit: closed {PARTIAL_TP_PCT:.0%} @ {price:.2f} | stop-> {self.stop_price:.2f}")
            except Exception as e:
                self.err_throttled(f"❌ partial TP 실패: {e}")

        # SL/TP
        if side == "LONG":
            if price <= eff_stop:
                self.manual_exit("STOP/TRAIL")
                return
            if price >= self.tp_price:
                self.manual_exit("TAKE PROFIT")
                return
        else:
            if price >= eff_stop:
                self.manual_exit("STOP/TRAIL")
                return
            if price <= self.tp_price:
                self.manual_exit("TAKE PROFIT")
                return

        self.state["last_event"] = f"HOLD {side} score={score} stop={eff_stop:.2f} tp={self.tp_price:.2f}"

    def public_state(self):
        return {
            "dry_run": DRY_RUN,
            "mode": os.getenv("MODE","SAFE"),
            "trading_enabled": self.trading_enabled,
            "position": self.position,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "tp_price": self.tp_price,
            "tp1_price": self.tp1_price,
            "tp1_done": self.tp1_done,
            "trail_price": self.trail_price,
            "day_profit_approx": self.day_profit,
            "win": self.win,
            "loss": self.loss,
            "consec_losses": self.consec_losses,
            "day_entries": self._day_entries,
            "cooldown_until": self._cooldown_until,
            "entry_reason": self.state.get("entry_reason"),
            "last_event": self.state.get("last_event"),
            "last_price": self.state.get("last_price"),
            "bybit_base": BYBIT_BASE_URL,
            "proxy": "ON" if PROXIES else "OFF",
            "fee_rate": FEE_RATE,
            "slippage_bps": SLIPPAGE_BPS,
            "trade_hours_utc": TRADE_HOURS_UTC,
        }
