# trader.py (FINAL+++ UPGRADE) - FIXED FULL COPY-PASTE
# ✅ FIX 1) retCode=110043 leverage not modified  -> IGNORE (treat as success)
# ✅ FIX 2) retCode=10001 Missing symbol or settleCoin -> position/list always includes settleCoin (default USDT)
#
# --- Telegram 명령어 ---
# /start /stop
# /safe /aggro
# /status
# /buy  (고정 심볼로 롱 수동)
# /short (고정 심볼로 숏 수동)
# /sell /panic
#
# --- 추가 업그레이드 명령어 ---
# /symbols                 후보 심볼 목록
# /add BTCUSDT,ETHUSDT      후보 추가
# /remove BTCUSDT           후보 제거
# /autod on|off             자동탐색(거래대금 상위 자동추가) ON/OFF
# /div on|off               분산진입 ON/OFF
# /maxpos 1|2|3             최대 동시 포지션 수
# /setusdt 5                주문 USDT(현재 모드에 적용)
# /setlev 3                 레버(현재 모드에 적용)
# /setscore 65              진입 점수(현재 모드에 적용)
# /setsymbol BTCUSDT        수동 고정 심볼(스캔 대신 이 심볼만)
# /autosymbol on|off         심볼 자동선정(스캐너) ON/OFF
#
import os, time, json, hmac, hashlib, requests
from urllib.parse import urlencode
from datetime import datetime, timezone

try:
    from config import *  # optional
except Exception:
    pass

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ===== Proxy 설정 =====
PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""

PROXIES = {
    "http": PROXY,
    "https": PROXY,
} if PROXY else None

import math
import time
import math
import time
# ===== qty step 보정 (1000코인만) =====
def fix_qty(qty, symbol=None):
    try:
        sym = (symbol or "").upper()
        if sym.startswith("1000"):
            step = 1000
            return max(step, int(qty // step * step))
        return qty
    except Exception:
        return qty
def _to_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def _round_down_to_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    return math.floor(x / step) * step

def _decimals_from_step(step: float) -> int:
    # step=0.001 -> 3, step=1 -> 0
    s = f"{step:.16f}".rstrip("0").rstrip(".")
    if "." not in s:
        return 0
    return len(s.split(".")[1])

def _quantize(x: float, step: float):
    d = _decimals_from_step(step)
    return float(f"{x:.{d}f}") if d > 0 else float(int(x))

PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None
def _cfg(name, default):
    try:
        return globals()[name]
    except Exception:
        return default

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))

def _now_utc():
    return datetime.now(timezone.utc)

def _day_key_utc():
    return _now_utc().strftime("%Y-%m-%d")

def _utc_hour():
    return int(_now_utc().strftime("%H"))

# =========================
# CONFIG (env overrides)
# =========================
BYBIT_BASE_URL = (os.getenv("BYBIT_BASE_URL") or _cfg("BYBIT_BASE_URL", "https://api.bybit.com")).rstrip("/")
CATEGORY = os.getenv("CATEGORY", _cfg("CATEGORY", "linear"))
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", _cfg("ACCOUNT_TYPE", "UNIFIED"))

# ✅ 중요: UNIFIED에서 position/list는 settleCoin을 요구하는 케이스가 많음
SETTLE_COIN = os.getenv("SETTLE_COIN", _cfg("SETTLE_COIN", "USDT")).upper()

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", _cfg("BYBIT_API_KEY", ""))
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", _cfg("BYBIT_API_SECRET", ""))

BOT_TOKEN = os.getenv("BOT_TOKEN", _cfg("BOT_TOKEN", ""))
CHAT_ID = os.getenv("CHAT_ID", _cfg("CHAT_ID", ""))

DRY_RUN = str(os.getenv("DRY_RUN", str(_cfg("DRY_RUN", "true")))).lower() in ("1","true","yes","y","on")

MODE_DEFAULT = os.getenv("MODE", _cfg("MODE", "SAFE")).upper()  # SAFE/AGGRO
ALLOW_LONG_DEFAULT = str(os.getenv("ALLOW_LONG", "true")).lower() in ("1","true","yes","y","on")
ALLOW_SHORT_DEFAULT = str(os.getenv("ALLOW_SHORT", "true")).lower() in ("1","true","yes","y","on")

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

TRAIL_ON = str(os.getenv("TRAIL_ON","true")).lower() in ("1","true","yes","y","on")
TRAIL_ATR_MULT = float(os.getenv("TRAIL_ATR_MULT","1.0"))

ENTER_SCORE_SAFE = int(os.getenv("ENTER_SCORE_SAFE","65"))
ENTER_SCORE_AGGRO = int(os.getenv("ENTER_SCORE_AGGRO","55"))
EXIT_SCORE_DROP = int(os.getenv("EXIT_SCORE_DROP","35"))

ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC","60"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC","0"))
TIME_EXIT_MIN = int(os.getenv("TIME_EXIT_MIN","360"))

MAX_ENTRIES_PER_DAY = int(os.getenv("MAX_ENTRIES_PER_DAY","6"))
MAX_CONSEC_LOSSES = int(os.getenv("MAX_CONSEC_LOSSES","3"))

FEE_RATE = float(os.getenv("FEE_RATE", "0.0006"))
SLIPPAGE_BPS = float(os.getenv("SLIPPAGE_BPS", "5"))

PARTIAL_TP_ON = str(os.getenv("PARTIAL_TP_ON","true")).lower() in ("1","true","yes","y","on")
PARTIAL_TP_PCT = float(os.getenv("PARTIAL_TP_PCT", "0.5"))
TP1_FRACTION = float(os.getenv("TP1_FRACTION", "0.5"))
MOVE_STOP_TO_BE_ON_TP1 = str(os.getenv("MOVE_STOP_TO_BE_ON_TP1","true")).lower() in ("1","true","yes","y","on")

TRADE_HOURS_UTC = os.getenv("TRADE_HOURS_UTC", "00-23")

# =========================
# MULTI-COIN + DISCOVERY + DIVERSIFY + AI GROWTH
# =========================
SYMBOLS_ENV = os.getenv("SYMBOLS", _cfg("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT"))
SYMBOLS_ENV = [s.strip().upper() for s in SYMBOLS_ENV.split(",") if s.strip()]

SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "20"))
SCAN_LIMIT = int(os.getenv("SCAN_LIMIT", "10"))
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT", "0.12"))

AUTO_DISCOVERY_DEFAULT = str(os.getenv("AUTO_DISCOVERY", "true")).lower() in ("1","true","yes","y","on")
DISCOVERY_REFRESH_SEC = int(os.getenv("DISCOVERY_REFRESH_SEC", "180"))
DISCOVERY_TOPN = int(os.getenv("DISCOVERY_TOPN", "20"))

DIVERSIFY_DEFAULT = str(os.getenv("DIVERSIFY", "false")).lower() in ("1","true","yes","y","on")
MAX_POSITIONS_DEFAULT = int(os.getenv("MAX_POSITIONS", "1"))

AUTO_SYMBOL_DEFAULT = str(os.getenv("AUTO_SYMBOL", "true")).lower() in ("1","true","yes","y","on")
FIXED_SYMBOL_DEFAULT = os.getenv("SYMBOL", _cfg("SYMBOL", "BTCUSDT")).upper()

AI_GROWTH_DEFAULT = str(os.getenv("AI_GROWTH", "true")).lower() in ("1","true","yes","y","on")
GROWTH_MIN_TRADES = int(os.getenv("GROWTH_MIN_TRADES", "6"))
GROWTH_STEP_SCORE = int(os.getenv("GROWTH_STEP_SCORE", "2"))
GROWTH_STEP_USDT = float(os.getenv("GROWTH_STEP_USDT", "1.0"))
GROWTH_STEP_LEV = int(os.getenv("GROWTH_STEP_LEV", "1"))

GROWTH_SCORE_MIN = int(os.getenv("GROWTH_SCORE_MIN", "45"))
GROWTH_SCORE_MAX = int(os.getenv("GROWTH_SCORE_MAX", "85"))
GROWTH_USDT_MIN = float(os.getenv("GROWTH_USDT_MIN", "3"))
GROWTH_USDT_MAX = float(os.getenv("GROWTH_USDT_MAX", "30"))
GROWTH_LEV_MIN = int(os.getenv("GROWTH_LEV_MIN", "1"))
GROWTH_LEV_MAX = int(os.getenv("GROWTH_LEV_MAX", "12"))

RECV_WINDOW_BASE = int(os.getenv("RECV_WINDOW", "8000"))
MAX_RETRIES = int(os.getenv("BYBIT_MAX_RETRIES", "4"))

BINANCE = "https://api.binance.com/api/v3/ticker/price"

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

def fallback_price(symbol: str):
    try:
        return float(requests.get(BINANCE, params={"symbol": symbol}, headers=HEADERS, timeout=10, proxies=PROXIES).json()["price"])
    except Exception:
        return 0.0

def _safe_json(r: requests.Response):
    text = r.text or ""
    if not text.strip():
        return {"_non_json": True, "raw": "", "status": r.status_code}
    try:
        return r.json()
    except Exception:
        return {"_non_json": True, "raw": text[:800], "status": r.status_code}

def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

def _bybit_server_time_ms():
    try:
        r = requests.get(f"{BYBIT_BASE_URL}/v5/market/time", headers=HEADERS, timeout=10, proxies=PROXIES)
        j = r.json()
        if "timeSecond" in j:
            return int(float(j["timeSecond"]) * 1000)
    except Exception:
        pass
    return int(time.time() * 1000)

def _is_bybit_lev_not_modified(ret_code: str, ret_msg: str) -> bool:
    return str(ret_code) == "110043" or ("leverage not modified" in (ret_msg or "").lower())

class BybitHTTP:
    def __init__(self):
        self._time_offset_ms = 0
        self._last_sync = 0

    def _sync_time(self):
        now = time.time()
        if now - self._last_sync < 60:
            return
        srv = _bybit_server_time_ms()
        self._time_offset_ms = srv - int(time.time() * 1000)
        self._last_sync = now

    def request(self, method: str, path: str, params=None, auth=False):
        params = params or {}
        url = f"{BYBIT_BASE_URL}{path}"

        for attempt in range(MAX_RETRIES):
            self._sync_time()
            ts = int(time.time() * 1000) + self._time_offset_ms
            recv_window = RECV_WINDOW_BASE + attempt * 2000

            try:
                if not auth:
                    if method == "GET":
                        r = requests.get(url, params=params, headers=HEADERS, timeout=12, proxies=PROXIES)
                    else:
                        r = requests.post(url, json=params, headers=HEADERS, timeout=12, proxies=PROXIES)
                    j = _safe_json(r)
                else:
                    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
                        raise RuntimeError("Missing BYBIT_API_KEY / BYBIT_API_SECRET")

                    if method == "GET":
                        q = urlencode(sorted(params.items()))
                        payload = f"{ts}{BYBIT_API_KEY}{recv_window}{q}"
                        sign = _sign(BYBIT_API_SECRET, payload)
                        headers = {
                            **HEADERS,
                            "X-BAPI-API-KEY": BYBIT_API_KEY,
                            "X-BAPI-TIMESTAMP": str(ts),
                            "X-BAPI-RECV-WINDOW": str(recv_window),
                            "X-BAPI-SIGN": sign,
                            "X-BAPI-SIGN-TYPE": "2",
                        }
                        r = requests.get(url, params=params, headers=headers, timeout=12, proxies=PROXIES)
                        j = _safe_json(r)
                    else:
                        body = json.dumps(params, separators=(",", ":"))
                        payload = f"{ts}{BYBIT_API_KEY}{recv_window}{body}"
                        sign = _sign(BYBIT_API_SECRET, payload)
                        headers = {
                            **HEADERS,
                            "X-BAPI-API-KEY": BYBIT_API_KEY,
                            "X-BAPI-TIMESTAMP": str(ts),
                            "X-BAPI-RECV-WINDOW": str(recv_window),
                            "X-BAPI-SIGN": sign,
                            "X-BAPI-SIGN-TYPE": "2",
                            "Content-Type": "application/json",
                        }
                        r = requests.post(url, headers=headers, data=body, timeout=12, proxies=PROXIES)
                        j = _safe_json(r)

                if r.status_code == 403:
                    raise RuntimeError(f"Bybit 403 blocked base={BYBIT_BASE_URL} proxy={'ON' if PROXIES else 'OFF'}")
                if r.status_code == 407:
                    raise RuntimeError("Proxy auth failed (407)")
                if j.get("_non_json"):
                    raise RuntimeError(f"Bybit non-json status={j.get('status')} raw={j.get('raw')}")

                ret = str(j.get("retCode", "0"))
                if ret == "0":
                    return j

                # ✅ 10002: time sync issue -> resync + retry
                if ret == "10002":
                    self._last_sync = 0
                    time.sleep(0.6 + attempt * 0.4)
                    continue

                # ✅ FIX: leverage not modified -> treat success ONLY for set-leverage endpoint
                if path == "/v5/position/set-leverage" and _is_bybit_lev_not_modified(ret, j.get("retMsg", "")):
                    return j

                raise RuntimeError(f"Bybit error retCode={ret} retMsg={j.get('retMsg')}")
            except Exception:
                if attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(0.6 + attempt * 0.4)

        raise RuntimeError("request failed")

http = BybitHTTP()

# =========================
# Indicators
# =========================
# ✅ BTC 필터: 추세장(강하게) / 횡보장(약하게)
def btc_filter(side: str) -> bool:
    """
    - BTC가 뚜렷한 추세면: 추세 방향만 허용 (강한 필터)
    - BTC가 횡보면: 필터 완화(거의 통과) -> 알트 독립장 허용
    """
    try:
        kl = get_klines("BTCUSDT", "60", 260)  # 1시간봉
        if not kl or len(kl) < 220:
            return True

        kl = list(reversed(kl))
        closes = [float(x[4]) for x in kl]
        price = closes[-1]

        ema50 = ema(closes[-150:], 50)
        ema200 = ema(closes[-200:], 200)

        # BTC 변동성으로 "횡보/추세" 구분
        highs = [float(x[2]) for x in kl[-120:]]
        lows  = [float(x[3]) for x in kl[-120:]]
        a = atr(highs, lows, closes[-120:], 14)
        if a is None or price <= 0:
            return True

        # ✅ 횡보 판정: ATR/price가 낮고, ema50-ema200 차이도 작으면 → 완화
        atr_ratio = a / price
        ema_gap = abs(ema50 - ema200) / price

        if atr_ratio < 0.003 and ema_gap < 0.002:
            return True  # 횡보장: 알트 독립 움직임 허용

        # ✅ 추세장: 방향 일치만 허용
        if side == "LONG":
            return (price > ema200) and (ema50 > ema200)
        else:
            return (price < ema200) and (ema50 < ema200)

    except Exception:
        return True
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

# =========================
# Market (per-symbol)
# =========================
def get_ticker(symbol: str):
    j = http.request("GET", "/v5/market/tickers", {"category": CATEGORY, "symbol": symbol}, auth=False)
    lst = (j.get("result") or {}).get("list") or []
    return lst[0] if lst else None

def get_price(symbol: str):
    if DRY_RUN:
        p = fallback_price(symbol)
        if p <= 0:
            raise Exception(f"fallback price failed for {symbol}")
        return float(p)
    t = get_ticker(symbol)
    if not t:
        raise Exception("tickers empty")
    return float(t.get("markPrice") or t.get("lastPrice") or 0)

def get_spread_pct(symbol: str):
    t = get_ticker(symbol)
    if not t:
        return None
    bid = float(t.get("bid1Price") or 0)
    ask = float(t.get("ask1Price") or 0)
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 100.0

def get_klines(symbol: str, interval: str, limit: int):
    if DRY_RUN:
        import random
        price = get_price(symbol)
        out=[]
        for _ in range(limit):
            h=price*(1+random.uniform(0,0.002))
            l=price*(1-random.uniform(0,0.002))
            c=price*(1+random.uniform(-0.001,0.001))
            out.append([0,0,f"{h}",f"{l}",f"{c}",0])
            price=c
        return out
    j = http.request("GET", "/v5/market/kline",
                     {"category": CATEGORY, "symbol": symbol, "interval": str(interval), "limit": int(limit)},
                     auth=False)
    return (j.get("result") or {}).get("list") or []

# =========================
# Positions (FIXED: settleCoin)
# =========================
def get_positions_all(symbol: str = None):
    """
    ✅ FIX: UNIFIED에서 10001 방지 위해 settleCoin 기본 포함
    - symbol이 있으면 symbol도 같이 넘겨서 더 안전하게
    """
    if DRY_RUN:
        return []
    params = {"category": CATEGORY, "settleCoin": SETTLE_COIN}
    if symbol:
        params["symbol"] = symbol
    j = http.request("GET", "/v5/position/list", params, auth=True)
    return (j.get("result") or {}).get("list") or []

def get_position_size(symbol: str) -> float:
    if DRY_RUN:
        return 0.0
    plist = get_positions_all(symbol=symbol)
    for p in plist:
        if (p.get("symbol") or "").upper() == symbol.upper():
            return float(p.get("size") or 0.0)
    return 0.0

def set_leverage(symbol: str, x: int):
    if DRY_RUN:
        return {"retCode": 0, "retMsg": "DRY_RUN"}
    body = {"category": CATEGORY, "symbol": symbol, "buyLeverage": str(x), "sellLeverage": str(x)}
    # ✅ 110043는 http.request에서 성공 처리됨
    return http.request("POST", "/v5/position/set-leverage", body, auth=True)

def order_market(symbol: str, side: str, qty: float, reduce_only=False):
    if DRY_RUN:
        return {"retCode": 0, "retMsg": "DRY_RUN"}

    qty = fix_qty(qty, symbol)  # ✅ body 위에 추가

    body = {
        "category": CATEGORY,
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "IOC",
    }
    if reduce_only:
        body["reduceOnly"] = True

    resp = http.request("POST", "/v5/order/create", body, auth=True)
    if (resp or {}).get("retCode") != 0:
        raise Exception(f"ORDER FAILED: {resp}")
    return resp

def qty_from_order_usdt(symbol: str, order_usdt, lev, price):
    if order_usdt <= 0 or price <= 0:
        return 0.0
    raw_qty = (order_usdt * lev) / price
    # 아주 단순 step (코인별 정확한 lotSize는 나중에 계정에서 심볼정보로 개선 가능)
    step = 0.001 if "BTC" in symbol else 0.01
    qty = (raw_qty // step) * step
    return round(qty, 6)

# =========================
# Reason + Signal
# =========================
def build_reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok):
    return (
        f"[{symbol} {side}] 근거\n"
        f"- price={price:.6f}\n"
        f"- EMA{EMA_FAST}={ef:.6f}, EMA{EMA_SLOW}={es:.6f}\n"
        f"- RSI{RSI_PERIOD}={r:.2f}\n"
        f"- ATR{ATR_PERIOD}={a:.6f}\n"
        f"- score={score} ({confidence_label(score)})\n"
        f"- trend_ok={trend_ok} | enter_ok={enter_ok}\n"
    )

def mode_params(mode: str, overrides=None):
    overrides = overrides or {}
    m = (mode or MODE_DEFAULT).upper()
    if m == "AGGRO":
        return {
            "lev": int(overrides.get("lev", LEVERAGE_AGGRO)),
            "order_usdt": float(overrides.get("order_usdt", ORDER_USDT_AGGRO)),
            "stop_atr": float(overrides.get("stop_atr", STOP_ATR_MULT_AGGRO)),
            "tp_r": float(overrides.get("tp_r", TP_R_MULT_AGGRO)),
            "enter_score": int(overrides.get("enter_score", ENTER_SCORE_AGGRO)),
        }
    return {
        "lev": int(overrides.get("lev", LEVERAGE_SAFE)),
        "order_usdt": float(overrides.get("order_usdt", ORDER_USDT_SAFE)),
        "stop_atr": float(overrides.get("stop_atr", STOP_ATR_MULT_SAFE)),
        "tp_r": float(overrides.get("tp_r", TP_R_MULT_SAFE)),
        "enter_score": int(overrides.get("enter_score", ENTER_SCORE_SAFE)),
    }

def compute_signal_and_exits(symbol: str, side: str, price: float, mp: dict):
    kl = get_klines(symbol, ENTRY_INTERVAL, KLINE_LIMIT)
    if len(kl) < max(120, EMA_SLOW * 3):
        ef = es = price
        r = 50.0
        a = price * 0.005
        score = 50
        trend_ok = True
        enter_ok = score >= mp["enter_score"]
        stop_dist = a * mp["stop_atr"]
        tp_dist = stop_dist * mp["tp_r"]
        sl = price - stop_dist if side=="LONG" else price + stop_dist
        tp = price + tp_dist if side=="LONG" else price - tp_dist
        reason = build_reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok) + "- note=kline 부족\n"
        return False, reason, score, sl, tp, a

    kl = list(reversed(kl))
    closes=[float(x[4]) for x in kl]
    highs=[float(x[2]) for x in kl]
    lows =[float(x[3]) for x in kl]

    ef=ema(closes[-EMA_FAST*3:], EMA_FAST)
    es=ema(closes[-EMA_SLOW*3:], EMA_SLOW)
    r=rsi(closes, RSI_PERIOD)
    a=atr(highs, lows, closes, ATR_PERIOD)

    if r is None:
        r = 50.0
    if a is None:
        a = price * 0.005

    # 🔒 변동성 필터 (너무 낮으면 횡보, 너무 높으면 난리장)
    if a / price < 0.002:
        return False, "LOW VOLATILITY", 0, None, None, a
    if a / price > 0.06:
        return False, "EXTREME VOLATILITY", 0, None, None, a

    score = ai_score(price, ef, es, r, a)
    enter_ok = score >= mp["enter_score"]

    if side == "LONG":
        trend_ok = (price > es) and (ef > es)
    else:
        trend_ok = (price < es) and (ef < es)

    ok = enter_ok and trend_ok
    reason = build_reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok)

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
# PnL estimate
# =========================
def _est_round_trip_cost_frac():
    slip = (SLIPPAGE_BPS / 10000.0)
    return (2 * FEE_RATE) + (2 * slip)

def estimate_pnl_usdt(side: str, entry_price: float, exit_price: float, notional_usdt: float):
    if entry_price <= 0 or notional_usdt <= 0:
        return 0.0
    raw_move = (exit_price - entry_price) / entry_price
    if side == "SHORT":
        raw_move = -raw_move
    gross = notional_usdt * raw_move
    cost = notional_usdt * _est_round_trip_cost_frac()
    return gross - cost

# =========================
# Telegram notify
# =========================
def tg_send(msg: str):
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

# =========================
# Trader
# =========================
class Trader:
    def __init__(self, state=None):
        self.state = state if isinstance(state, dict) else {}

        self.trading_enabled = True
        self.mode = MODE_DEFAULT
        self.allow_long = ALLOW_LONG_DEFAULT
        self.allow_short = ALLOW_SHORT_DEFAULT

        self.symbols = list(dict.fromkeys(SYMBOLS_ENV))
        self.auto_discovery = AUTO_DISCOVERY_DEFAULT
        self.auto_symbol = AUTO_SYMBOL_DEFAULT
        self.fixed_symbol = FIXED_SYMBOL_DEFAULT
        self._last_discovery_ts = 0

        self.diversify = DIVERSIFY_DEFAULT
        self.max_positions = int(_clamp(MAX_POSITIONS_DEFAULT, 1, 5))

        self.ai_growth = AI_GROWTH_DEFAULT
        self._trade_count_total = 0
        self._recent_results = []

        self.tune = {
            "SAFE": {"lev": LEVERAGE_SAFE, "order_usdt": ORDER_USDT_SAFE, "enter_score": ENTER_SCORE_SAFE},
            "AGGRO": {"lev": LEVERAGE_AGGRO, "order_usdt": ORDER_USDT_AGGRO, "enter_score": ENTER_SCORE_AGGRO},
        }

        self.positions = []  # dict list

        self.win = 0
        self.loss = 0
        self.day_profit = 0.0
        self.consec_losses = 0
        self._day_key = None
        self._day_entries = 0

        self._cooldown_until = 0
        self._last_alert_ts = 0
        self._last_err_ts = 0
        self._lev_set_cache = {}

        self._last_scan_ts = 0

    # ---------------- internal utils ----------------
    def notify(self, msg):
        tg_send(msg)

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

    def _mp(self):
        return mode_params(self.mode, self.tune.get(self.mode, {}))

    def _ensure_leverage(self, symbol: str):
        mp = self._mp()
        key = f"{symbol}:{self.mode}:{mp['lev']}"
        if self._lev_set_cache.get(key):
            return
        if not DRY_RUN:
            # ✅ 110043는 http.request에서 성공 처리됨
            set_leverage(symbol, int(mp["lev"]))
        self._lev_set_cache[key] = True

    # ---------------- discovery ----------------
    def _refresh_discovery(self):
        if not self.auto_discovery:
            return
        if time.time() - self._last_discovery_ts < DISCOVERY_REFRESH_SEC:
            return
        self._last_discovery_ts = time.time()
        try:
            j = http.request("GET", "/v5/market/tickers", {"category": CATEGORY}, auth=False)
            lst = (j.get("result") or {}).get("list") or []
            scored = []
            for t in lst:
                sym = (t.get("symbol") or "").upper()
                if not sym.endswith("USDT"):
                    continue
                turnover = float(t.get("turnover24h") or 0)
                if turnover <= 0:
                    continue
                scored.append((turnover, sym))
            scored.sort(reverse=True, key=lambda x: x[0])
            top_syms = [s for _, s in scored[:DISCOVERY_TOPN]]
            self.symbols = list(dict.fromkeys(self.symbols + top_syms))
            self.state["discovery"] = {"top_added": top_syms[:10], "universe_size": len(self.symbols)}
        except Exception as e:
            self.state["discovery_error"] = str(e)

    # ---------------- scanning ----------------
    def _score_symbol(self, symbol: str, price: float):
        mp = self._mp()
        sp = get_spread_pct(symbol)
        if sp is not None and sp > MAX_SPREAD_PCT:
            return {"ok": False, "reason": f"SPREAD({sp:.2f}%)"}

        if self.allow_long:
            okL, reasonL, scoreL, slL, tpL, aL = compute_signal_and_exits(symbol, "LONG", price, mp)
        else:
            okL, scoreL, reasonL, slL, tpL, aL = False, -999, "", None, None, None

        if self.allow_short:
            okS, reasonS, scoreS, slS, tpS, aS = compute_signal_and_exits(symbol, "SHORT", price, mp)
        else:
            okS, scoreS, reasonS, slS, tpS, aS = False, -999, "", None, None, None

        if scoreS > scoreL:
            return {"ok": okS, "side": "SHORT", "score": scoreS, "reason": reasonS, "sl": slS, "tp": tpS, "atr": aS}
        return {"ok": okL, "side": "LONG", "score": scoreL, "reason": reasonL, "sl": slL, "tp": tpL, "atr": aL}

    def pick_best(self):
        if time.time() - self._last_scan_ts < SCAN_INTERVAL_SEC:
            return None
        self._last_scan_ts = time.time()

        mp = self._mp()
        enter_score = int(mp["enter_score"])

        candidates = self.symbols[:SCAN_LIMIT] if len(self.symbols) > SCAN_LIMIT else self.symbols[:]

        best = None
        reasons = []

        for sym in candidates:
            try:
                price = get_price(sym)
                info = self._score_symbol(sym, price)
                if not info.get("ok"):
                    reasons.append(f"{sym}:NO({info.get('reason','')})")
                    continue
                sc = int(info.get("score", 0))
                if sc < enter_score:
                    reasons.append(f"{sym}:LOW({sc})")
                    continue
                if (best is None) or sc > best["score"]:
                    best = {"symbol": sym, **info, "price": price}
            except Exception:
                reasons.append(f"{sym}:ERR")
                continue

        self.state["last_scan"] = {"picked": best, "reasons": reasons[:12], "enter_score": enter_score, "universe": len(self.symbols)}
        return best

    # ---------------- position sync (실계정) ----------------
    def _sync_real_positions(self):
        if DRY_RUN:
            return
        try:
            plist = get_positions_all()
            real = []
            for p in plist:
                size = float(p.get("size") or 0)
                if size == 0:
                    continue
                sym = (p.get("symbol") or "").upper()
                side = "LONG" if (p.get("side") == "Buy") else "SHORT"
                entry = float(p.get("avgPrice") or p.get("entryPrice") or 0)
                real.append({"symbol": sym, "side": side, "size": size, "entry_price": entry})
            self.state["real_positions"] = real[:5]
            if (not self.positions) and real:
                self.notify_throttled(f"⚠️ 실계정 포지션 감지({len(real)}개). 봇 내부상태는 비어있음 → /panic 또는 수동정리 권장", 120)
        except Exception as e:
            self.state["sync_error"] = str(e)

    # ---------------- enter/exit helpers ----------------
    def _enter(self, symbol: str, side: str, price: float, reason: str, sl: float, tp: float):
        mp = self._mp()
        lev = float(mp["lev"])
        order_usdt = float(mp["order_usdt"])

        qty = qty_from_order_usdt(symbol, order_usdt, lev, price)
        if qty <= 0:
            raise Exception("qty<=0")

        self._ensure_leverage(symbol)

        if not DRY_RUN:
            order_market(symbol, "Buy" if side == "LONG" else "Sell", qty)

        tp1_price = None
        if PARTIAL_TP_ON:
            if side == "LONG":
                tp1_price = price + (tp - price) * TP1_FRACTION
            else:
                tp1_price = price - (price - tp) * TP1_FRACTION

        pos = {
            "symbol": symbol,
            "side": side,
            "entry_price": price,
            "entry_ts": time.time(),
            "stop_price": sl,
            "tp_price": tp,
            "trail_price": None,
            "tp1_price": tp1_price,
            "tp1_done": False,
            "last_order_usdt": order_usdt,
            "last_lev": lev,
        }
        self.positions.append(pos)

        self._cooldown_until = time.time() + COOLDOWN_SEC
        self._day_entries += 1
        self.state["entry_reason"] = reason

        self.notify(f"✅ ENTER {symbol} {side} qty={qty}\n{reason}\n⏳ stop={sl:.6f} tp={tp:.6f} tp1={tp1_price}")

    def _close_qty(self, symbol: str, side: str, close_qty: float):
        if DRY_RUN:
            return
        if close_qty <= 0:
            return
        order_market(symbol, "Sell" if side == "LONG" else "Buy", close_qty, reduce_only=True)

    def _exit_position(self, idx: int, why: str, force=False):
        if idx < 0 or idx >= len(self.positions):
            return
        pos = self.positions[idx]
        symbol = pos["symbol"]
        side = pos["side"]

        try:
            price = get_price(symbol)
        except Exception as e:
            if not force:
                self.err_throttled(f"❌ exit price 실패: {symbol} {e}")
                return
            price = pos.get("entry_price") or 0

        if not DRY_RUN:
            try:
                qty = get_position_size(symbol)
                if qty > 0:
                    self._close_qty(symbol, side, qty)
            except Exception as e:
                self.err_throttled(f"❌ 실청산 실패: {symbol} {e}")

        entry_price = float(pos.get("entry_price") or 0)
        notional = float(pos.get("last_order_usdt") or 0) * float(pos.get("last_lev") or 0)
        pnl_est = estimate_pnl_usdt(side, entry_price, price, notional)
        self.day_profit += pnl_est

        self._trade_count_total += 1
        self._recent_results.append(pnl_est)
        if len(self._recent_results) > 30:
            self._recent_results = self._recent_results[-30:]

        if pnl_est >= 0:
            self.win += 1
            self.consec_losses = 0
        else:
            self.loss += 1
            self.consec_losses += 1

        self.notify(f"✅ EXIT {symbol} {side} ({why}) price={price:.6f} pnl≈{pnl_est:.2f} day≈{self.day_profit:.2f} (W{self.win}/L{self.loss})")
        self.positions.pop(idx)
        self._maybe_ai_grow()

    # ---------------- AI Growth ----------------
    def _maybe_ai_grow(self):
        if not self.ai_growth:
            return
        if self._trade_count_total < GROWTH_MIN_TRADES:
            return

        recent = self._recent_results[-GROWTH_MIN_TRADES:]
        avg = sum(recent) / max(1, len(recent))
        wins = sum(1 for x in recent if x >= 0)
        winrate = wins / max(1, len(recent))

        m = self.mode
        t = self.tune.get(m, {}).copy()
        if not t:
            return

        if self.consec_losses >= 2 or avg < 0:
            t["enter_score"] = int(_clamp(int(t["enter_score"]) + GROWTH_STEP_SCORE, GROWTH_SCORE_MIN, GROWTH_SCORE_MAX))
            t["order_usdt"] = float(_clamp(float(t["order_usdt"]) - GROWTH_STEP_USDT, GROWTH_USDT_MIN, GROWTH_USDT_MAX))
            t["lev"] = int(_clamp(int(t["lev"]) - GROWTH_STEP_LEV, GROWTH_LEV_MIN, GROWTH_LEV_MAX))
            self.tune[m] = t
            self._lev_set_cache = {}  # 레버 변경 가능성 -> 캐시 리셋
            self.notify_throttled(f"🧠 AI성장(보수): score↑ usdt↓ lev↓ | score={t['enter_score']} usdt={t['order_usdt']} lev={t['lev']} (avg={avg:.2f}, winrate={winrate:.0%})", 90)
            return

        if avg > 0 and winrate >= 0.55:
            t["enter_score"] = int(_clamp(int(t["enter_score"]) - 1, GROWTH_SCORE_MIN, GROWTH_SCORE_MAX))
            t["order_usdt"] = float(_clamp(float(t["order_usdt"]) + GROWTH_STEP_USDT, GROWTH_USDT_MIN, GROWTH_USDT_MAX))
            t["lev"] = int(_clamp(int(t["lev"]) + 0, GROWTH_LEV_MIN, GROWTH_LEV_MAX))
            self.tune[m] = t
            self.notify_throttled(f"🧠 AI성장(완화): score↓ usdt↑ | score={t['enter_score']} usdt={t['order_usdt']} lev={t['lev']} (avg={avg:.2f}, winrate={winrate:.0%})", 90)

    # ---------------- Telegram commands ----------------
    def handle_command(self, text: str):
        cmd = (text or "").strip()
        if not cmd:
            return

        parts = cmd.split()
        c0 = parts[0].lower()
        arg = " ".join(parts[1:]).strip() if len(parts) > 1 else ""

        if c0 == "/start":
            self.trading_enabled = True
            self.notify("✅ 거래 ON")
            return
        if c0 == "/stop":
            self.trading_enabled = False
            self.notify("🛑 거래 OFF")
            return
        if c0 == "/safe":
            self.mode = "SAFE"
            self._lev_set_cache = {}  # 모드 바뀌면 캐시 리셋
            self.notify("🛡 SAFE 모드로 전환")
            return
        if c0 in ("/aggro", "/attack"):
            self.mode = "AGGRO"
            self._lev_set_cache = {}
            self.notify("⚔️ AGGRO 모드로 전환")
            return

        if c0 == "/autod":
            v = (arg or "").lower()
            self.auto_discovery = (v in ("on","1","true","yes","y"))
            self.notify(f"🌐 AUTO_DISCOVERY={self.auto_discovery}")
            return

        if c0 == "/autosymbol":
            v = (arg or "").lower()
            self.auto_symbol = (v in ("on","1","true","yes","y"))
            self.notify(f"🧭 AUTO_SYMBOL={self.auto_symbol}")
            return

        if c0 == "/div":
            v = (arg or "").lower()
            self.diversify = (v in ("on","1","true","yes","y"))
            self.notify(f"🧩 DIVERSIFY={self.diversify} (돈 적으면 OFF 권장)")
            return

        if c0 == "/maxpos":
            try:
                n = int(arg)
                self.max_positions = int(_clamp(n, 1, 5))
                self.notify(f"📌 MAX_POSITIONS={self.max_positions}")
            except Exception:
                self.notify("❌ 사용법: /maxpos 1|2|3")
            return

        if c0 == "/symbols":
            self.notify("📌 후보심볼:\n" + ",".join(self.symbols[:60]) + ("" if len(self.symbols)<=60 else "\n..."))
            return

        if c0 == "/add":
            if not arg:
                self.notify("❌ 사용법: /add BTCUSDT,ETHUSDT")
                return
            add = [s.strip().upper() for s in arg.split(",") if s.strip()]
            self.symbols = list(dict.fromkeys(self.symbols + add))
            self.notify(f"✅ 추가됨. universe={len(self.symbols)}")
            return

        if c0 == "/remove":
            if not arg:
                self.notify("❌ 사용법: /remove BTCUSDT")
                return
            rm = arg.strip().upper()
            self.symbols = [s for s in self.symbols if s != rm]
            self.notify(f"✅ 제거됨. universe={len(self.symbols)}")
            return

        if c0 == "/setsymbol":
            if not arg:
                self.notify("❌ 사용법: /setsymbol BTCUSDT")
                return
            self.fixed_symbol = arg.strip().upper()
            self.notify(f"📌 FIXED_SYMBOL={self.fixed_symbol} (AUTO_SYMBOL OFF일 때만 사용)")
            return

        if c0 == "/setusdt":
            try:
                v = float(arg)
                v = float(_clamp(v, GROWTH_USDT_MIN, GROWTH_USDT_MAX))
                self.tune[self.mode]["order_usdt"] = v
                self.notify(f"⚙️ {self.mode} order_usdt={v}")
            except Exception:
                self.notify("❌ 사용법: /setusdt 5")
            return

        if c0 == "/setlev":
            try:
                v = int(arg)
                v = int(_clamp(v, GROWTH_LEV_MIN, GROWTH_LEV_MAX))
                self.tune[self.mode]["lev"] = v
                self._lev_set_cache = {}
                self.notify(f"⚙️ {self.mode} lev={v}")
            except Exception:
                self.notify("❌ 사용법: /setlev 3")
            return

        if c0 == "/setscore":
            try:
                v = int(arg)
                v = int(_clamp(v, GROWTH_SCORE_MIN, GROWTH_SCORE_MAX))
                self.tune[self.mode]["enter_score"] = v
                self.notify(f"⚙️ {self.mode} enter_score={v}")
            except Exception:
                self.notify("❌ 사용법: /setscore 65")
            return

        if c0 == "/status":
            self.notify(self.status_text())
            return

        if c0 == "/buy":
            self.manual_enter("LONG")
            return
        if c0 == "/short":
            self.manual_enter("SHORT")
            return
        if c0 == "/sell":
            self.manual_exit("MANUAL SELL")
            return
        if c0 == "/panic":
            self.manual_exit("PANIC", force=True)
            self.trading_enabled = False
            self.notify("🚨 PANIC: 청산 시도 + 거래 OFF")
            return

        if c0 in ("/help", "help"):
            self.notify(self.help_text())
            return

        if c0.startswith("/"):
            self.notify("❓ 모르는 명령. /help")
            return

    def help_text(self):
        return (
            "📌 명령어\n"
            "/start /stop\n"
            "/safe /aggro\n"
            "/status\n"
            "/buy /short /sell /panic\n"
            "\n"
            "🧭 멀티코인\n"
            "/autosymbol on|off\n"
            "/symbols\n"
            "/add BTCUSDT,ETHUSDT\n"
            "/remove BTCUSDT\n"
            "/setsymbol BTCUSDT\n"
            "\n"
            "🌐 자동탐색\n"
            "/autod on|off\n"
            "\n"
            "🧩 분산\n"
            "/div on|off\n"
            "/maxpos 1|2|3\n"
            "\n"
            "⚙️ 튜닝\n"
            "/setusdt 5\n"
            "/setlev 3\n"
            "/setscore 65\n"
        )

    def status_text(self):
        total = self.win + self.loss
        winrate = (self.win / total * 100) if total else 0.0
        mp = self._mp()

        lines = []
        lines.append(f"🧠 DRY_RUN={DRY_RUN} | ON={self.trading_enabled} | MODE={self.mode} | AI_GROWTH={self.ai_growth}")
        lines.append(f"⚙️ lev={mp['lev']} | order_usdt={mp['order_usdt']} | enter_score>={mp['enter_score']}")
        lines.append(f"⏰ entry_hours_utc={TRADE_HOURS_UTC} | allowed_now={entry_allowed_now_utc()}")
        lines.append(f"💸 fee={FEE_RATE:.4%}/side | slip={SLIPPAGE_BPS:.1f}bps/side | partialTP={PARTIAL_TP_ON}({PARTIAL_TP_PCT:.0%})")
        lines.append(f"🌐 base={BYBIT_BASE_URL} | proxy={'ON' if PROXIES else 'OFF'} | settleCoin={SETTLE_COIN}")
        lines.append(f"🧭 AUTO_SYMBOL={self.auto_symbol} FIXED={self.fixed_symbol} | DISCOVERY={self.auto_discovery}")
        lines.append(f"🧩 DIVERSIFY={self.diversify} MAX_POS={self.max_positions} | universe={len(self.symbols)}")
        if self.state.get("last_scan"):
            picked = (self.state["last_scan"] or {}).get("picked")
            if picked:
                lines.append(f"🔎 last_pick={picked.get('symbol')} {picked.get('side')} score={picked.get('score')}")
        if self.positions:
            for p in self.positions[:5]:
                lines.append(f"📍 POS {p['symbol']} {p['side']} entry={p['entry_price']:.6f} stop={p['stop_price']:.6f} tp={p['tp_price']:.6f} tp1={p['tp1_price']}")
        else:
            lines.append("📍 POS=None")
        lines.append(f"📈 day_profit≈{self.day_profit:.2f} | winrate={winrate:.1f}% (W{self.win}/L{self.loss}) | consec_losses={self.consec_losses}")
        if self.state.get("entry_reason"):
            lines.append(f"🧠 근거:\n{self.state.get('entry_reason')}")
        if self.state.get("last_event"):
            lines.append(f"📝 last={self.state.get('last_event')}")
        return "\n".join(lines)

    # ---------------- manual controls ----------------
    def manual_enter(self, side: str):
        try:
            self._reset_day()
            if len(self.positions) >= self.max_positions:
                self.notify("⚠️ 최대 포지션 수 도달")
                return
            symbol = self.fixed_symbol
            price = get_price(symbol)
            mp = self._mp()
            ok, reason, score, sl, tp, a = compute_signal_and_exits(symbol, side, price, mp)
            self._enter(symbol, side, price, reason + "- manual=True\n", sl, tp)
        except Exception as e:
            self.err_throttled(f"❌ manual enter 실패: {e}")

    def manual_exit(self, why: str, force=False):
        try:
            if not self.positions and not force:
                self.notify("⚠️ 포지션 없음")
                return
            for idx in range(len(self.positions)-1, -1, -1):
                self._exit_position(idx, why, force=force)
            self._cooldown_until = time.time() + COOLDOWN_SEC
        except Exception as e:
            self.err_throttled(f"❌ manual exit 실패: {e}")

    # ---------------- manage open positions ----------------
    def _manage_one(self, idx: int):
        pos = self.positions[idx]
        symbol = pos["symbol"]
        side = pos["side"]

        price = get_price(symbol)

        mp = self._mp()
        ok, reason, score, sl_new, tp_new, a = compute_signal_and_exits(symbol, side, price, mp)

        if TRAIL_ON and a is not None and pos.get("stop_price") is not None:
            dist = a * TRAIL_ATR_MULT
            if side == "LONG":
                cand = price - dist
                if pos["trail_price"] is None or cand > pos["trail_price"]:
                    pos["trail_price"] = cand
            else:
                cand = price + dist
                if pos["trail_price"] is None or cand < pos["trail_price"]:
                    pos["trail_price"] = cand

        eff_stop = pos["stop_price"] if pos.get("stop_price") is not None else price
        if pos.get("trail_price") is not None:
            eff_stop = max(eff_stop, pos["trail_price"]) if side == "LONG" else min(eff_stop, pos["trail_price"])

        if pos.get("entry_ts") and (time.time() - pos["entry_ts"]) > (TIME_EXIT_MIN * 60):
            self._exit_position(idx, "TIME EXIT")
            return

        if score <= EXIT_SCORE_DROP:
            self._exit_position(idx, f"SCORE DROP {score}")
            return

        if PARTIAL_TP_ON and (not pos.get("tp1_done")) and pos.get("tp1_price") is not None and (not DRY_RUN):
            try:
                qty_total = get_position_size(symbol)
                if qty_total > 0:
                    hit_tp1 = (price >= pos["tp1_price"]) if side=="LONG" else (price <= pos["tp1_price"])
                    if hit_tp1:
                        close_qty = qty_total * float(PARTIAL_TP_PCT)
                        self._close_qty(symbol, side, close_qty)
                        pos["tp1_done"] = True
                        if MOVE_STOP_TO_BE_ON_TP1 and pos.get("entry_price") is not None:
                            if side == "LONG":
                                pos["stop_price"] = max(pos["stop_price"], pos["entry_price"])
                            else:
                                pos["stop_price"] = min(pos["stop_price"], pos["entry_price"])
                        self.notify(f"🧩 PARTIAL TP hit: {symbol} closed {PARTIAL_TP_PCT:.0%} @ {price:.6f} | stop-> {pos['stop_price']:.6f}")
            except Exception as e:
                self.err_throttled(f"❌ partial TP 실패: {e}")

        if side == "LONG":
            if eff_stop is not None and price <= eff_stop:
                self._exit_position(idx, "STOP/TRAIL")
                return
            if pos.get("tp_price") is not None and price >= pos["tp_price"]:
                self._exit_position(idx, "TAKE PROFIT")
                return
        else:
            if eff_stop is not None and price >= eff_stop:
                self._exit_position(idx, "STOP/TRAIL")
                return
            if pos.get("tp_price") is not None and price <= pos["tp_price"]:
                self._exit_position(idx, "TAKE PROFIT")
                return

        self.state["last_event"] = f"HOLD {symbol} {side} score={score} stop={eff_stop:.6f} tp={pos.get('tp_price'):.6f}"

    # ---------------- main tick ----------------
    def tick(self):
        self._reset_day()

        self.state["trading_enabled"] = self.trading_enabled
        self.state["mode"] = self.mode
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

        self._refresh_discovery()
        self._sync_real_positions()

        if self.positions:
            for idx in range(len(self.positions)-1, -1, -1):
                try:
                    self._manage_one(idx)
                except Exception as e:
                    self.err_throttled(f"❌ manage 실패: {e}")
            return

        if time.time() < self._cooldown_until:
            self.state["last_event"] = "대기: cooldown"
            return
        if self._day_entries >= MAX_ENTRIES_PER_DAY:
            self.state["last_event"] = "대기: 일일 진입 제한"
            return
        if not entry_allowed_now_utc():
            self.state["last_event"] = f"대기: 시간필터(UTC {TRADE_HOURS_UTC})"
            return

        if not self.auto_symbol:
            symbol = self.fixed_symbol
            try:
                price = get_price(symbol)
                mp = self._mp()
                info = self._score_symbol(symbol, price)
                self.state["entry_reason"] = info.get("reason")
                if not info.get("ok"):
                    self.state["last_event"] = f"대기: {symbol} not ok"
                    return
                if int(info.get("score", 0)) < int(mp["enter_score"]):
                    self.state["last_event"] = f"대기: score={info.get('score')}"
                    return
                self._enter(symbol, info["side"], price, info["reason"], info["sl"], info["tp"])
                self.state["last_event"] = f"ENTER {symbol} {info['side']}"
            except Exception as e:
                self.err_throttled(f"❌ entry 실패(fixed): {e}")
            return

        pick = self.pick_best()
        if not pick:
            self.state["last_event"] = "대기: 스캔 결과 없음"
            return

        try:
            self._enter(pick["symbol"], pick["side"], pick["price"], pick["reason"], pick["sl"], pick["tp"])
            self.state["last_event"] = f"ENTER {pick['symbol']} {pick['side']}"
        except Exception as e:
            self.err_throttled(f"❌ entry 실패(scan): {e}")

    def public_state(self):
        mp = self._mp()
        return {
            "dry_run": DRY_RUN,
            "mode": self.mode,
            "trading_enabled": self.trading_enabled,
            "positions": self.positions,
            "day_profit_approx": self.day_profit,
            "win": self.win,
            "loss": self.loss,
            "consec_losses": self.consec_losses,
            "day_entries": self._day_entries,
            "cooldown_until": self._cooldown_until,
            "entry_reason": self.state.get("entry_reason"),
            "last_event": self.state.get("last_event"),
            "bybit_base": BYBIT_BASE_URL,
            "proxy": "ON" if PROXIES else "OFF",
            "fee_rate": FEE_RATE,
            "slippage_bps": SLIPPAGE_BPS,
            "trade_hours_utc": TRADE_HOURS_UTC,
            "symbols_count": len(self.symbols),
            "auto_symbol": self.auto_symbol,
            "fixed_symbol": self.fixed_symbol,
            "auto_discovery": self.auto_discovery,
            "diversify": self.diversify,
            "max_positions": self.max_positions,
            "tune": {"mode": self.mode, "lev": mp["lev"], "order_usdt": mp["order_usdt"], "enter_score": mp["enter_score"]},
            "ai_growth": self.ai_growth,
            "settle_coin": SETTLE_COIN,
        }
