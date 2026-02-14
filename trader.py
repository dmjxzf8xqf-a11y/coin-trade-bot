# trader.py (FINAL+++ UPGRADE) - 기존 FINAL++ 유지 + 멀티코인 자동선정 + 자동코인탐색(Discovery)
# + 분산진입(옵션/버튼/명령) + AI 자동성장(안전장치 포함) + Bybit 10002 시간동기화/재시도
#
# ✅ 그대로 "전체 교체" 해서 한 번에 복붙용 (파일 1개)
#
# --- Telegram 명령어 ---
# /start /stop
# /safe /aggro
# /status
# /buy  (현재 심볼로 롱 수동)
# /short (현재 심볼로 숏 수동)
# /sell /panic
#
# --- 추가 업그레이드 명령어 ---
# /symbols                 후보 심볼 목록
# /add BTCUSDT,ETHUSDT      후보 추가
# /remove BTCUSDT           후보 제거
# /autod on|off             자동탐색(거래대금 상위 자동추가) ON/OFF
# /div on|off               분산진입 ON/OFF (돈 적을 때 기본 OFF)
# /maxpos 1|2|3             최대 동시 포지션 수
# /setusdt 5                주문 USDT(현재 모드에 적용)
# /setlev 3                 레버(현재 모드에 적용)
# /setscore 65              진입 점수(현재 모드에 적용)
# /setsymbol BTCUSDT         수동 고정 심볼(스캔 대신 이 심볼만)
# /autosymbol on|off         심볼 자동선정(스캐너) ON/OFF
#
# --- 안전 기본값 ---
# 돈 적으면: MAX_POSITIONS=1, DIVERSIFY=false, AI_GROWTH=true 권장
#
import os, time, json, hmac, hashlib, requests
from urllib.parse import urlencode
from datetime import datetime, timezone

try:
    from config import *  # optional
except Exception:
    pass

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
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

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", _cfg("BYBIT_API_KEY", ""))
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", _cfg("BYBIT_API_SECRET", ""))

BOT_TOKEN = os.getenv("BOT_TOKEN", _cfg("BOT_TOKEN", ""))
CHAT_ID = os.getenv("CHAT_ID", _cfg("CHAT_ID", ""))

DRY_RUN = str(os.getenv("DRY_RUN", str(_cfg("DRY_RUN", "true")))).lower() in ("1","true","yes","y","on")

# 기존 FINAL++ 기본
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

# fee/slippage
FEE_RATE = float(os.getenv("FEE_RATE", "0.0006"))     # per-side
SLIPPAGE_BPS = float(os.getenv("SLIPPAGE_BPS", "5"))  # bps

# partial TP
PARTIAL_TP_ON = str(os.getenv("PARTIAL_TP_ON","true")).lower() in ("1","true","yes","y","on")
PARTIAL_TP_PCT = float(os.getenv("PARTIAL_TP_PCT", "0.5"))
TP1_FRACTION = float(os.getenv("TP1_FRACTION", "0.5"))
MOVE_STOP_TO_BE_ON_TP1 = str(os.getenv("MOVE_STOP_TO_BE_ON_TP1","true")).lower() in ("1","true","yes","y","on")

# time filter
TRADE_HOURS_UTC = os.getenv("TRADE_HOURS_UTC", "00-23")  # "01-23" or "22-03"

# =========================
# UPGRADE: MULTI-COIN + DISCOVERY + DIVERSIFY + AI GROWTH
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

AUTO_SYMBOL_DEFAULT = str(os.getenv("AUTO_SYMBOL", "true")).lower() in ("1","true","yes","y","on")  # 스캐너 사용 여부
FIXED_SYMBOL_DEFAULT = os.getenv("SYMBOL", _cfg("SYMBOL", "BTCUSDT")).upper()  # 기존 호환: SYMBOL env가 있으면 고정처럼 쓸 수 있음

AI_GROWTH_DEFAULT = str(os.getenv("AI_GROWTH", "true")).lower() in ("1","true","yes","y","on")
GROWTH_MIN_TRADES = int(os.getenv("GROWTH_MIN_TRADES", "6"))
GROWTH_STEP_SCORE = int(os.getenv("GROWTH_STEP_SCORE", "2"))
GROWTH_STEP_USDT = float(os.getenv("GROWTH_STEP_USDT", "1.0"))
GROWTH_STEP_LEV = int(os.getenv("GROWTH_STEP_LEV", "1"))

# 안전 범위
GROWTH_SCORE_MIN = int(os.getenv("GROWTH_SCORE_MIN", "45"))
GROWTH_SCORE_MAX = int(os.getenv("GROWTH_SCORE_MAX", "85"))
GROWTH_USDT_MIN = float(os.getenv("GROWTH_USDT_MIN", "3"))
GROWTH_USDT_MAX = float(os.getenv("GROWTH_USDT_MAX", "30"))
GROWTH_LEV_MIN = int(os.getenv("GROWTH_LEV_MIN", "1"))
GROWTH_LEV_MAX = int(os.getenv("GROWTH_LEV_MAX", "12"))

# Bybit 10002 방지
RECV_WINDOW_BASE = int(os.getenv("RECV_WINDOW", "8000"))
MAX_RETRIES = int(os.getenv("BYBIT_MAX_RETRIES", "4"))

# DRY_RUN 가격 소스
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

                if ret == "10002":
                    self._last_sync = 0
                    time.sleep(0.6 + attempt * 0.4)
                    continue

                raise RuntimeError(f"Bybit error retCode={ret} retMsg={j.get('retMsg')}")
            except Exception:
                if attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(0.6 + attempt * 0.4)

        raise RuntimeError("request failed")

http = BybitHTTP()

# =========================
# Indicators (기존 유지)
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

    j = http.request("GET", "/v5/market/kline", {"category": CATEGORY, "symbol": symbol, "interval": str(interval), "limit": int(limit)}, auth=False)
    return (j.get("result") or {}).get("list") or []

def get_positions_all():
    if DRY_RUN:
        return []
    j = http.request("GET", "/v5/position/list", {"category": CATEGORY}, auth=True)
    return (j.get("result") or {}).get("list") or []

def set_leverage(symbol: str, x: int):
    if DRY_RUN:
        return {"retCode": 0, "retMsg": "DRY_RUN"}
    body = {"category": CATEGORY, "symbol": symbol, "buyLeverage": str(x), "sellLeverage": str(x)}
    return http.request("POST", "/v5/position/set-leverage", body, auth=True)

def order_market(symbol: str, side: str, qty: float, reduce_only=False):
    if DRY_RUN:
        return {"retCode": 0, "retMsg": "DRY_RUN"}
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
        raise Exception(f"ORDER FAILED → retCode={resp.get('retCode')} retMsg={resp.get('retMsg')}")
    return resp

def qty_from_order_usdt(symbol: str, order_usdt, lev, price):
    if order_usdt <= 0 or price <= 0:
        return 0.0
    raw_qty = (order_usdt * lev) / price
    if "BTC" in symbol:
        step = 0.001
    else:
        step = 0.01
    qty = (raw_qty // step) * step
    return round(qty, 6)

# =========================
# Reason + Signal (기존 유지, symbol 인자만 추가)
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

    if r is None: r = 50.0
    if a is None: a = price * 0.005

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
# PnL estimate (기존 유지)
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

        # runtime flags
        self.trading_enabled = True
        self.mode = MODE_DEFAULT
        self.allow_long = ALLOW_LONG_DEFAULT
        self.allow_short = ALLOW_SHORT_DEFAULT

        # multi-coin universe
        self.symbols = list(dict.fromkeys(SYMBOLS_ENV))  # unique keep order
        self.auto_discovery = AUTO_DISCOVERY_DEFAULT
        self.auto_symbol = AUTO_SYMBOL_DEFAULT
        self.fixed_symbol = FIXED_SYMBOL_DEFAULT  # used if auto_symbol OFF
        self._last_discovery_ts = 0

        # diversify / max positions
        self.diversify = DIVERSIFY_DEFAULT
        self.max_positions = int(_clamp(MAX_POSITIONS_DEFAULT, 1, 5))

        # AI growth
        self.ai_growth = AI_GROWTH_DEFAULT
        self._trade_count_total = 0  # realized exits
        self._recent_results = []    # list of pnl_est

        # per-mode tunables (can be overridden by telegram)
        self.tune = {
            "SAFE": {"lev": LEVERAGE_SAFE, "order_usdt": ORDER_USDT_SAFE, "enter_score": ENTER_SCORE_SAFE},
            "AGGRO": {"lev": LEVERAGE_AGGRO, "order_usdt": ORDER_USDT_AGGRO, "enter_score": ENTER_SCORE_AGGRO},
        }

        # positions: list of dict
        # each pos: {"symbol","side","entry_price","entry_ts","stop_price","tp_price","trail_price","tp1_price","tp1_done","last_order_usdt","last_lev"}
        self.positions = []

        # stats day
        self.win = 0
        self.loss = 0
        self.day_profit = 0.0
        self.consec_losses = 0
        self._day_key = None
        self._day_entries = 0

        self._cooldown_until = 0
        self._last_alert_ts = 0
        self._last_err_ts = 0
        self._lev_set_cache = {}  # symbol->(mode)->bool

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
        base = mode_params(self.mode, self.tune.get(self.mode, {}))
        return base

    def _ensure_leverage(self, symbol: str):
        mp = self._mp()
        key = f"{symbol}:{self.mode}:{mp['lev']}"
        if self._lev_set_cache.get(key):
            return
        if not DRY_RUN:
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
            # 24h 거래대금 상위(USDT) 추정: turnover24h 사용(선형/현물 기준 차이 있지만 필터로 충분)
            j = http.request("GET", "/v5/market/tickers", {"category": CATEGORY}, auth=False)
            lst = (j.get("result") or {}).get("list") or []
            scored = []
            for t in lst:
                sym = (t.get("symbol") or "").upper()
                if not sym.endswith("USDT"):
                    continue
                # 밈/극단 스캠 등은 여기서도 완벽히 걸러지진 않음 -> spread/score에서 추가 필터
                turnover = float(t.get("turnover24h") or 0)
                if turnover <= 0:
                    continue
                scored.append((turnover, sym))
            scored.sort(reverse=True, key=lambda x: x[0])
            top_syms = [s for _, s in scored[:DISCOVERY_TOPN]]
            # 기존 수동 목록 유지 + top 추가 (중복 제거)
            merged = list(dict.fromkeys(self.symbols + top_syms))
            self.symbols = merged
            self.state["discovery"] = {"top_added": top_syms[:10], "universe_size": len(self.symbols)}
        except Exception as e:
            self.state["discovery_error"] = str(e)

    # ---------------- scanning ----------------
    def _score_symbol(self, symbol: str, price: float):
        mp = self._mp()
        # spread filter
        sp = get_spread_pct(symbol)
        if sp is not None and sp > MAX_SPREAD_PCT:
            return {"ok": False, "reason": f"SPREAD({sp:.2f}%)"}

        if self.allow_long:
            okL, reasonL, scoreL, slL, tpL, aL = compute_signal_and_exits(symbol, "LONG", price, mp)
        else:
            okL, scoreL = False, -999
            reasonL = ""
            slL = tpL = aL = None

        if self.allow_short:
            okS, reasonS, scoreS, slS, tpS, aS = compute_signal_and_exits(symbol, "SHORT", price, mp)
        else:
            okS, scoreS = False, -999
            reasonS = ""
            slS = tpS = aS = None

        # pick direction by higher score (기존 로직 확장)
        if scoreS > scoreL:
            return {"ok": okS, "side": "SHORT", "score": scoreS, "reason": reasonS, "sl": slS, "tp": tpS, "atr": aS}
        return {"ok": okL, "side": "LONG", "score": scoreL, "reason": reasonL, "sl": slL, "tp": tpL, "atr": aL}

    def pick_best(self):
        if time.time() - self._last_scan_ts < SCAN_INTERVAL_SEC:
            return None
        self._last_scan_ts = time.time()

        mp = self._mp()
        enter_score = int(mp["enter_score"])

        candidates = self.symbols[:]
        if len(candidates) > SCAN_LIMIT:
            candidates = candidates[:SCAN_LIMIT]

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
            # 우리는 내부 관리 포지션과 정확히 1:1 동기화까지는 하지 않음(복잡/오류 위험)
            # 대신, 내부 positions가 없는데 real이 있으면 경고만
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
        tp1_done = False
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
            "tp1_done": tp1_done,
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
        # idx in self.positions
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

        # 실청산은 size를 모르는 문제가 있어서: v5 포지션에서 size 찾아서 reduceOnly 청산
        if not DRY_RUN:
            try:
                plist = get_positions_all()
                qty = 0.0
                for p in plist:
                    if (p.get("symbol") or "").upper() == symbol:
                        qty = float(p.get("size") or 0)
                        break
                if qty > 0:
                    self._close_qty(symbol, side, qty)
            except Exception as e:
                self.err_throttled(f"❌ 실청산 실패: {symbol} {e}")

        # pnl estimate
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

        # remove
        self.positions.pop(idx)

        # growth tune (after exit)
        self._maybe_ai_grow()

    # ---------------- AI Growth (자동성장) ----------------
    def _maybe_ai_grow(self):
        if not self.ai_growth:
            return
        if self._trade_count_total < GROWTH_MIN_TRADES:
            return

        # 최근 성과 기반
        recent = self._recent_results[-GROWTH_MIN_TRADES:]
        avg = sum(recent) / max(1, len(recent))
        wins = sum(1 for x in recent if x >= 0)
        winrate = wins / max(1, len(recent))

        m = self.mode
        t = self.tune.get(m, {}).copy()
        if not t:
            return

        # 안전 규칙:
        # - 연속손실 많으면 더 보수적으로(enter_score 올리고, usdt/lev 내림)
        # - 평균 손익이 +이고 승률도 괜찮으면 조금 공격적으로(enter_score 낮추고, usdt/lev 약간 올림)
        if self.consec_losses >= 2 or avg < 0:
            t["enter_score"] = int(_clamp(int(t["enter_score"]) + GROWTH_STEP_SCORE, GROWTH_SCORE_MIN, GROWTH_SCORE_MAX))
            t["order_usdt"] = float(_clamp(float(t["order_usdt"]) - GROWTH_STEP_USDT, GROWTH_USDT_MIN, GROWTH_USDT_MAX))
            t["lev"] = int(_clamp(int(t["lev"]) - GROWTH_STEP_LEV, GROWTH_LEV_MIN, GROWTH_LEV_MAX))
            self.tune[m] = t
            self.notify_throttled(f"🧠 AI성장(보수): score↑ usdt↓ lev↓ | score={t['enter_score']} usdt={t['order_usdt']} lev={t['lev']} (avg={avg:.2f}, winrate={winrate:.0%})", 90)
            return

        if avg > 0 and winrate >= 0.55:
            t["enter_score"] = int(_clamp(int(t["enter_score"]) - 1, GROWTH_SCORE_MIN, GROWTH_SCORE_MAX))
            t["order_usdt"] = float(_clamp(float(t["order_usdt"]) + GROWTH_STEP_USDT, GROWTH_USDT_MIN, GROWTH_USDT_MAX))
            t["lev"] = int(_clamp(int(t["lev"]) + 0, GROWTH_LEV_MIN, GROWTH_LEV_MAX))  # 레버는 기본 고정(리스크 큼)
            self.tune[m] = t
            self.notify_throttled(f"🧠 AI성장(완화): score↓ usdt↑ | score={t['enter_score']} usdt={t['order_usdt']} lev={t['lev']} (avg={avg:.2f}, winrate={winrate:.0%})", 90)

    # ---------------- Telegram commands ----------------
    def handle_command(self, text: str):
        cmd = (text or "").strip()
        if not cmd:
            return

        # quick parse
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
            self.notify("🛡 SAFE 모드로 전환")
            return
        if c0 in ("/aggro", "/attack"):
            self.mode = "AGGRO"
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
                self._lev_set_cache = {}  # reset cache
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
        lines.append(f"🌐 base={BYBIT_BASE_URL} | proxy={'ON' if PROXIES else 'OFF'}")
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
            # 수동은 ok 강제 진입(너가 버튼 누른거니까)
            self._enter(symbol, side, price, reason + "- manual=True\n", sl, tp)
        except Exception as e:
            self.err_throttled(f"❌ manual enter 실패: {e}")

    def manual_exit(self, why: str, force=False):
        try:
            if not self.positions and not force:
                self.notify("⚠️ 포지션 없음")
                return
            # 전부 청산
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

        # re-score for exit logic (기존: score drop / time exit)
        mp = self._mp()
        ok, reason, score, sl_new, tp_new, a = compute_signal_and_exits(symbol, side, price, mp)

        # trailing
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
            if side == "LONG":
                eff_stop = max(eff_stop, pos["trail_price"])
            else:
                eff_stop = min(eff_stop, pos["trail_price"])

        # time exit
        if pos.get("entry_ts") and (time.time() - pos["entry_ts"]) > (TIME_EXIT_MIN * 60):
            self._exit_position(idx, "TIME EXIT")
            return

        # score drop exit
        if score <= EXIT_SCORE_DROP:
            self._exit_position(idx, f"SCORE DROP {score}")
            return

        # partial TP (실계정에서만 정확)
        if PARTIAL_TP_ON and (not pos.get("tp1_done")) and pos.get("tp1_price") is not None and (not DRY_RUN):
            try:
                # real size from positions
                plist = get_positions_all()
                qty_total = 0.0
                for p in plist:
                    if (p.get("symbol") or "").upper() == symbol:
                        qty_total = float(p.get("size") or 0.0)
                        break
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

        # SL/TP
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

        # update state
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

        # discovery refresh
        self._refresh_discovery()

        # sync real positions (optional)
        self._sync_real_positions()

        # manage existing positions
        if self.positions:
            # 여러 포지션이면 순차 관리
            for idx in range(len(self.positions)-1, -1, -1):
                try:
                    self._manage_one(idx)
                except Exception as e:
                    self.err_throttled(f"❌ manage 실패: {e}")
            return

        # no positions -> entry
        if time.time() < self._cooldown_until:
            self.state["last_event"] = "대기: cooldown"
            return
        if self._day_entries >= MAX_ENTRIES_PER_DAY:
            self.state["last_event"] = "대기: 일일 진입 제한"
            return
        if not entry_allowed_now_utc():
            self.state["last_event"] = f"대기: 시간필터(UTC {TRADE_HOURS_UTC})"
            return

        # decide symbols / entry plan
        if not self.auto_symbol:
            # fixed symbol only (기존 호환)
            symbol = self.fixed_symbol
            try:
                price = get_price(symbol)
                mp = self._mp()
                # pick best direction using same rule
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

        # auto symbol scan
        pick = self.pick_best()
        if not pick:
            self.state["last_event"] = "대기: 스캔 결과 없음"
            return

        # diversify logic:
        # - 기본은 1포지션이라 여기서는 사실상 pick 1개만 진입
        # - diversify=true AND max_positions>1 인 경우: 진입 후 다음 tick에 또 pick 가능하도록 둠(무리진입 방지)
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
        }
