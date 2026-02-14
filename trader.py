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
# fee per side (taker rough): 0.0006 = 0.06%
FEE_RATE = float(os.getenv("FEE_RATE", "0.0006"))
# slippage in bps: 5 = 0.05%
SLIPPAGE_BPS = float(os.getenv("SLIPPAGE_BPS", "5"))

# =========================
# UPGRADE 2) partial take profit
# =========================
PARTIAL_TP_ON = str(os.getenv("PARTIAL_TP_ON","true")).lower() in ("1","true","yes","y")
PARTIAL_TP_PCT = float(os.getenv("PARTIAL_TP_PCT", "0.5"))     # 0.5 = 50% 청산
TP1_FRACTION = float(os.getenv("TP1_FRACTION", "0.5"))         # TP 거리의 50% 지점에서 1차 익절
MOVE_STOP_TO_BE_ON_TP1 = str(os.getenv("MOVE_STOP_TO_BE_ON_TP1","true")).lower() in ("1","true","yes","y")

# =========================
# UPGRADE 3) time-of-day filter (entry only, UTC)
# =========================
# e.g. "01-23" or "22-03" (wrap)
TRADE_HOURS_UTC = os.getenv("TRADE_HOURS_UTC", "00-23")

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
# BYBIT API
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
    body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty), "timeInForce": "IOC"}
    if reduce_only:
        body["reduceOnly"] = True
    return bybit_post("/v5/order/create", body)

def qty_from_order_usdt(order_usdt, lev, price):
    if order_usdt <= 0 or price <= 0:
        return 0.0
    return float(f"{(order_usdt*lev/max(price,1e-9)):.6f}")

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
    if r is not None and 45<r<65
