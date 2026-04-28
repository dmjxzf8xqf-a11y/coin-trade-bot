# trader.py (FINAL+++++ CLEAN BUILD) - FULL COPY-PASTE (ONE BLOCK)
# ✅ FIX 1) retCode=110043 leverage not modified -> IGNORE (treat as success)
# ✅ FIX 2) retCode=10001 Missing symbol or settleCoin -> position/list always includes settleCoin (default USDT)
# ✅ FIX 3) compute_signal_and_exits() 'aa' typo crash -> fixed
# ✅ FIX 4) /setsymbol -> AUTO_SYMBOL OFF automatically
# ✅ FIX 5) _ensure_leverage() double-safe try/except
# ✅ FIX 6) 네가 붙인 코드에 있던 "self.state" / 들여쓰기 / tick 블록 깨짐 / risk_engine 인라인 import 오류 전부 정리
# ✅ Optional) risk_engine.py 있으면 자동 사용 가능 (없으면 기존 USDT 기반 qty 계산 사용)

import os
import time
import json
import hmac
import hashlib
import math
import requests
from urllib.parse import urlencode
from datetime import datetime, timezone
from storage_utils import data_dir, data_path, safe_read_json, atomic_write_json
from kill_switch import KillSwitch

# --- optional advanced modules ---
try:
    from volatility_position import adjust_position_size
except Exception:
    def adjust_position_size(base_usdt, atr_pct):
        return base_usdt

try:
    from strategy_guard import StrategyGuard
except Exception:
    StrategyGuard = None

# --- FINAL QUANT CORE (optional) ---
try:
    from quant_core.execution_engine import ExecutionEngine
    from quant_core.slippage_tracker import SlippageTracker
    from quant_core.strategy_performance import StrategyPerformance
    from quant_core.portfolio_optimizer import PortfolioOptimizer
    from quant_core.portfolio_engine import PortfolioEngine
    from quant_core.institutional_risk_model import InstitutionalRiskModel
    from quant_core.liquidity_filter import is_liquid_ok
    from quant_core.walkforward import WalkForwardScheduler
except Exception:
    ExecutionEngine = None
    SlippageTracker = None
    StrategyPerformance = None
    PortfolioOptimizer = None
    PortfolioEngine = None
    InstitutionalRiskModel = None
    is_liquid_ok = None
    WalkForwardScheduler = None
# ===== LOT SIZE CACHE =====
_lot_cache = {}
# --- optional AI learn module ---
try:
    from ai_learn import check_winrate_milestone, record_trade_result, get_ai_stats
except Exception:
    def check_winrate_milestone():
        return None
    def record_trade_result(_pnl):
        return None
    def get_ai_stats():
        return {"winrate": 0, "wins": 0, "losses": 0}

# --- optional config import ---
try:
    from config import *  # noqa
except Exception:
    pass


# --- optional strategy router (if exists) ---
try:
    from strategy_router import select_strategy  # type: ignore
except Exception:
    def select_strategy(regime: str) -> str:
        r = (regime or '').lower()
        # conservative defaults
        if r in ('volatile', 'crash', 'panic'):
            return 'low_risk'
        if r in ('range', 'sideways'):
            return 'mean_reversion'
        return 'trend'


# =========================
# OPTIONAL FEATURE FLAGS (safe defaults if not in config.py)
# =========================
def _bool_env(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default)).lower() in ("1","true","yes","y","on")

# quant_core feature toggles
USE_EXECUTION_ENGINE = globals().get("USE_EXECUTION_ENGINE", _bool_env("USE_EXECUTION_ENGINE", "true"))
USE_STRATEGY_PERF   = globals().get("USE_STRATEGY_PERF", _bool_env("USE_STRATEGY_PERF", "true"))
USE_PORTFOLIO_OPT   = globals().get("USE_PORTFOLIO_OPT", _bool_env("USE_PORTFOLIO_OPT", "true"))
USE_WALKFORWARD     = globals().get("USE_WALKFORWARD", _bool_env("USE_WALKFORWARD", "false"))
USE_MTF_FILTER      = globals().get("USE_MTF_FILTER", _bool_env("USE_MTF_FILTER", "true"))
USE_VOL_POSITION    = globals().get("USE_VOL_POSITION", _bool_env("USE_VOL_POSITION", "true"))
ENABLE_LIVE_OPTIMIZER= globals().get("ENABLE_LIVE_OPTIMIZER", _bool_env("ENABLE_LIVE_OPTIMIZER", "false"))
MTF_TREND_INTERVAL   = str(os.getenv("MTF_TREND_INTERVAL", globals().get("MTF_TREND_INTERVAL", "60")))

USE_LIQUIDITY_FILTER= globals().get("USE_LIQUIDITY_FILTER", _bool_env("USE_LIQUIDITY_FILTER", "true"))

# Institutional layer (new)
USE_INST_PORTFOLIO  = globals().get("USE_INST_PORTFOLIO", _bool_env("USE_INST_PORTFOLIO", "true"))
USE_INST_RISK_MODEL = globals().get("USE_INST_RISK_MODEL", _bool_env("USE_INST_RISK_MODEL", "true"))

# Inst portfolio params
INST_MAX_SYMBOLS = int(os.getenv("INST_MAX_SYMBOLS", str(globals().get("INST_MAX_SYMBOLS", 3))))
INST_MAX_WEIGHT  = float(os.getenv("INST_MAX_WEIGHT", str(globals().get("INST_MAX_WEIGHT", 0.35))))

# =========================
# ENDGAME (Top Priority) feature flags
# =========================
USE_MTF_FILTER      = globals().get("USE_MTF_FILTER", _bool_env("USE_MTF_FILTER", "false"))
MTF_TREND_INTERVAL  = int(os.getenv("MTF_TREND_INTERVAL", str(globals().get("MTF_TREND_INTERVAL", 60))))  # minutes

USE_VOL_POSITION    = globals().get("USE_VOL_POSITION", _bool_env("USE_VOL_POSITION", "false"))
VOL_TARGET          = float(os.getenv("VOL_TARGET", "0.012"))        # target ATR/price (1.2%)
VOL_MIN_SCALE       = float(os.getenv("VOL_MIN_SCALE", "0.5"))
VOL_MAX_SCALE       = float(os.getenv("VOL_MAX_SCALE", "1.8"))

USE_REALIZED_PNL    = globals().get("USE_REALIZED_PNL", _bool_env("USE_REALIZED_PNL", "true"))
REALIZED_PNL_LOOKBACK_MIN = int(os.getenv("REALIZED_PNL_LOOKBACK_MIN", "720"))  # how far back to query closed pnl

# Hedge/one-way position mode support (Bybit linear)
POSITION_MODE = (os.getenv("POSITION_MODE", "ONEWAY") or "ONEWAY").upper()  # ONEWAY|HEDGE
# Circuit breaker (API instability protection)
CB_ON = _bool_env("CB_ON", "true")
CB_ERR_MAX = int(os.getenv("CB_ERR_MAX", "6"))          # errors within window -> disable trading temporarily
CB_WINDOW_SEC = int(os.getenv("CB_WINDOW_SEC", "180"))  # rolling window seconds
CB_COOLDOWN_SEC = int(os.getenv("CB_COOLDOWN_SEC", "600"))

# Telegram buttons UI
TG_BUTTONS_ON = _bool_env("TG_BUTTONS_ON", "false")

# Structured JSONL logging
LOG_EVENTS = _bool_env("LOG_EVENTS", "true")
LOG_FILE = data_path("events.jsonl")
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ===== Proxy 설정 =====
PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

# ===== optional risk engine (if exists) =====
# If you create risk_engine.py with calc_position_size(balance, risk_pct, entry_price, stop_price, leverage),
# set USE_RISK_ENGINE=true and provide RISK_PCT (e.g. 1.0) and optional BALANCE_USDT (or leave blank to skip).
USE_RISK_ENGINE = str(os.getenv("USE_RISK_ENGINE", "false")).lower() in ("1", "true", "yes", "y", "on")
RISK_PCT = float(os.getenv("RISK_PCT", "1.0"))
BALANCE_USDT_ENV = os.getenv("BALANCE_USDT", "")
try:
    from risk_engine import calc_position_size  # type: ignore
except Exception:
    calc_position_size = None

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

# ✅ UNIFIED에서 position/list는 settleCoin을 요구하는 케이스 많음
SETTLE_COIN = os.getenv("SETTLE_COIN", _cfg("SETTLE_COIN", "USDT")).upper()

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", _cfg("BYBIT_API_KEY", ""))
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", _cfg("BYBIT_API_SECRET", ""))

BOT_TOKEN = os.getenv("BOT_TOKEN", _cfg("BOT_TOKEN", ""))
CHAT_ID = os.getenv("CHAT_ID", _cfg("CHAT_ID", ""))

DRY_RUN = str(os.getenv("DRY_RUN", str(_cfg("DRY_RUN", "true")))).lower() in ("1", "true", "yes", "y", "on")

MODE_DEFAULT = os.getenv("MODE", _cfg("MODE", "SAFE")).upper()  # SAFE/AGGRO
ALLOW_LONG_DEFAULT = str(os.getenv("ALLOW_LONG", "true")).lower() in ("1", "true", "yes", "y", "on")
ALLOW_SHORT_DEFAULT = str(os.getenv("ALLOW_SHORT", "true")).lower() in ("1", "true", "yes", "y", "on")

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

TRAIL_ON = str(os.getenv("TRAIL_ON", "true")).lower() in ("1", "true", "yes", "y", "on")
TRAIL_ATR_MULT = float(os.getenv("TRAIL_ATR_MULT", "1.0"))

ENTER_SCORE_SAFE = int(os.getenv("ENTER_SCORE_SAFE", "65"))
ENTER_SCORE_AGGRO = int(os.getenv("ENTER_SCORE_AGGRO", "55"))
EXIT_SCORE_DROP = int(os.getenv("EXIT_SCORE_DROP", "35"))

ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC", "60"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "0"))
TIME_EXIT_MIN = int(os.getenv("TIME_EXIT_MIN", "360"))

MAX_ENTRIES_PER_DAY = int(os.getenv("MAX_ENTRIES_PER_DAY", "6"))
MAX_CONSEC_LOSSES = int(os.getenv("MAX_CONSEC_LOSSES", "3"))

FEE_RATE = float(os.getenv("FEE_RATE", "0.0006"))
SLIPPAGE_BPS = float(os.getenv("SLIPPAGE_BPS", "5"))

PARTIAL_TP_ON = str(os.getenv("PARTIAL_TP_ON", "true")).lower() in ("1", "true", "yes", "y", "on")
PARTIAL_TP_PCT = float(os.getenv("PARTIAL_TP_PCT", "0.5"))
TP1_FRACTION = float(os.getenv("TP1_FRACTION", "0.5"))
MOVE_STOP_TO_BE_ON_TP1 = str(os.getenv("MOVE_STOP_TO_BE_ON_TP1", "true")).lower() in ("1", "true", "yes", "y", "on")

TRADE_HOURS_UTC = os.getenv("TRADE_HOURS_UTC", "00-23")

# =========================
# MULTI-COIN + DISCOVERY + DIVERSIFY + AI GROWTH
# =========================
SYMBOLS_ENV = os.getenv("SYMBOLS", _cfg("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT"))
SYMBOLS_ENV = [s.strip().upper() for s in SYMBOLS_ENV.split(",") if s.strip()]

SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "20"))
SCAN_LIMIT = int(os.getenv("SCAN_LIMIT", "10"))
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT", "0.12"))

AUTO_DISCOVERY_DEFAULT = str(os.getenv("AUTO_DISCOVERY", "true")).lower() in ("1", "true", "yes", "y", "on")
DISCOVERY_REFRESH_SEC = int(os.getenv("DISCOVERY_REFRESH_SEC", "180"))
DISCOVERY_TOPN = int(os.getenv("DISCOVERY_TOPN", "20"))

DIVERSIFY_DEFAULT = str(os.getenv("DIVERSIFY", "false")).lower() in ("1", "true", "yes", "y", "on")
MAX_POSITIONS_DEFAULT = int(os.getenv("MAX_POSITIONS", "1"))

AUTO_SYMBOL_DEFAULT = str(os.getenv("AUTO_SYMBOL", "true")).lower() in ("1", "true", "yes", "y", "on")
FIXED_SYMBOL_DEFAULT = os.getenv("SYMBOL", _cfg("SYMBOL", "BTCUSDT")).upper()

AI_GROWTH_DEFAULT = str(os.getenv("AI_GROWTH", "true")).lower() in ("1", "true", "yes", "y", "on")
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
        r = requests.get(BINANCE, params={"symbol": symbol}, headers=HEADERS, timeout=10, proxies=PROXIES)
        return float(r.json()["price"])
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

                # ✅ FIX 1: leverage not modified -> treat success for set-leverage endpoint
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
def ema(data, p):
    k = 2 / (p + 1)
    e = data[0]
    for v in data[1:]:
        e = v * k + e * (1 - k)
    return e

def rsi(data, p=14):
    if len(data) < p + 1:
        return None
    gain = loss = 0.0
    for i in range(-p, 0):
        diff = data[i] - data[i - 1]
        if diff > 0:
            gain += diff
        else:
            loss -= diff
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

def atr(high, low, close, p=14):
    if len(close) < p + 1:
        return None
    trs = []
    for i in range(-p, 0):
        trs.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return sum(trs) / p

def ai_score(price, ef, es, r, a):
    score = 0
    if price > es:
        score += 25
    if price > ef:
        score += 20
    if r is not None and 45 < r < 65:
        score += 20
    if ef > es:
        score += 20
    if (a / price) < 0.02:
        score += 15
    return int(score)

def confidence_label(score):
    if score >= 85:
        return "🔥 매우높음"
    if score >= 70:
        return "✅ 높음"
    if score >= 55:
        return "⚠️ 보통"
    return "❌ 낮음"


# =========================
# Multi-timeframe trend filter (HTF)
# =========================
def _mtf_trend(symbol: str):
    """Return 'UP'|'DOWN'|'RANGE'|None using HTF EMA trend."""
    try:
        interval = int(MTF_TREND_INTERVAL)
        if interval <= 0:
            return None
        kl = get_klines(symbol, str(interval), max(120, EMA_SLOW * 3))
        if not kl or len(kl) < max(60, EMA_SLOW * 2):
            return None
        kl = list(reversed(kl))
        closes = [float(x[4]) for x in kl]
        ef = ema(closes[-EMA_FAST * 3 :], EMA_FAST)
        es = ema(closes[-EMA_SLOW * 3 :], EMA_SLOW)
        price = closes[-1]
        # simple classification
        if price >= es and ef >= es:
            return "UP"
        if price <= es and ef <= es:
            return "DOWN"
        return "RANGE"
    except Exception:
        return None

def _vol_scale_from_atr(price: float, atr_val: float):
    """Return scaling multiplier for order_usdt based on ATR/price."""
    try:
        if price <= 0 or atr_val is None or atr_val <= 0:
            return 1.0
        vol = float(atr_val) / float(price)
        if vol <= 0:
            return 1.0
        target = float(VOL_TARGET)
        scale = target / vol
        return float(_clamp(scale, float(VOL_MIN_SCALE), float(VOL_MAX_SCALE)))
    except Exception:
        return 1.0

def _position_idx_for(side: str):
    if POSITION_MODE != "HEDGE":
        return None
    s = (side or "").lower()
    # Bybit linear hedge: 1=Buy side, 2=Sell side
    return 1 if s in ("buy", "long") else 2
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
        out = []
        for _ in range(limit):
            h = price * (1 + random.uniform(0, 0.002))
            l = price * (1 - random.uniform(0, 0.002))
            c = price * (1 + random.uniform(-0.001, 0.001))
            out.append([0, 0, f"{h}", f"{l}", f"{c}", 0])
            price = c
        return out
    j = http.request(
        "GET",
        "/v5/market/kline",
        {"category": CATEGORY, "symbol": symbol, "interval": str(interval), "limit": int(limit)},
        auth=False,
    )
    return (j.get("result") or {}).get("list") or []

# =========================
# Positions (FIXED: settleCoin)
# =========================
def get_positions_all(symbol: str = None):
    if DRY_RUN:
        return []
    params = {"category": CATEGORY, "settleCoin": SETTLE_COIN}  # ✅ FIX 2
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
    return http.request("POST", "/v5/position/set-leverage", body, auth=True)

def order_market(symbol: str, side: str, qty: float, reduce_only=False):
    if DRY_RUN:
        return {"retCode": 0, "retMsg": "DRY_RUN"}
    qty = fix_qty(qty, symbol)
    body = {
        "category": CATEGORY,
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "IOC",
    }
    _pidx = _position_idx_for(side)
    if _pidx is not None:
        body["positionIdx"] = _pidx
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
    # 단순 step (정확 lotSize는 나중에 심볼정보로 개선)
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

def compute_signal_and_exits(symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False):
    kl = get_klines(symbol, ENTRY_INTERVAL, KLINE_LIMIT)
    if len(kl) < max(120, EMA_SLOW * 3):
        ef = es = price
        r = 50.0
        a = price * 0.005
        score = 50
        trend_ok = True

        stop_dist = a * mp["stop_atr"]
        tp_dist = stop_dist * mp["tp_r"]
        sl = price - stop_dist if side == "LONG" else price + stop_dist
        tp = price + tp_dist if side == "LONG" else price - tp_dist

        enter_ok = score >= mp["enter_score"]
        reason = build_reason(symbol, side, price, ef, es, r, a, score, trend_ok, enter_ok) + "- note=kline 부족\n"
        return False, reason, score, sl, tp, a  # ✅ FIX 3: aa -> a

    kl = list(reversed(kl))
    closes = [float(x[4]) for x in kl]
    highs = [float(x[2]) for x in kl]
    lows = [float(x[3]) for x in kl]

    ef = ema(closes[-EMA_FAST * 3 :], EMA_FAST)
    es = ema(closes[-EMA_SLOW * 3 :], EMA_SLOW)
    r = rsi(closes, RSI_PERIOD)
    a = atr(highs, lows, closes, ATR_PERIOD)

    if r is None:
        r = 50.0
    if a is None:
        a = price * 0.005

    # ✅ (네가 넣고 싶어했던) RSI 손실구간 회피 옵션: avoid_low_rsi=True 일 때만 적용
    if avoid_low_rsi and r < 40:
        return False, "AI AVOID LOSS RSI ZONE", 0, None, None, a

    # 변동성 필터
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
# Regime + Strategy Router
# =========================
def detect_market_regime(symbol: str) -> str:
    """returns: bull | bear | range | volatile"""
    try:
        kl = get_klines(symbol, ENTRY_INTERVAL, max(KLINE_LIMIT, EMA_SLOW * 3 + 60))
        if not kl or len(kl) < (EMA_SLOW * 3 + 30):
            return "range"


        kl = list(reversed(kl))
        closes = [float(x[4]) for x in kl]
        highs = [float(x[2]) for x in kl]
        lows  = [float(x[3]) for x in kl]

        price = closes[-1]
        ef = ema(closes[-EMA_FAST * 3 :], EMA_FAST)
        es = ema(closes[-EMA_SLOW * 3 :], EMA_SLOW)
        a = atr(highs, lows, closes, ATR_PERIOD)
        if a is None:
            a = price * 0.005

        vol = a / max(price, 1e-9)
        if vol >= 0.03:
            return "volatile"

        # Prefer FINAL10's slope function if present
        slope_bps = None
        try:
            slope_bps = _final10_regime_slope_bps(symbol)
        except Exception:
            slope_bps = None

        if slope_bps is None:
            lookback = 20
            es_now = es
            es_old = ema(closes[-(EMA_SLOW * 3 + lookback) : -lookback], EMA_SLOW)
            if es_old > 0:
                slope_bps = abs((es_now - es_old) / es_old) * 10000.0

        if slope_bps is not None and slope_bps < 6.0:
            return "range"

        if price >= es and ef >= es:
            return "bull"
        if price <= es and ef <= es:
            return "bear"
        return "range"
    except Exception:
        return "range"


def _mtf_trend(symbol: str) -> str:
    """Higher-timeframe trend filter.
    returns: up | down | range
    Interval is controlled by MTF_TREND_INTERVAL (default: 60).
    """
    try:
        kl = get_klines(symbol, str(MTF_TREND_INTERVAL), max(300, EMA_SLOW * 3 + 50))
        if not kl or len(kl) < max(220, EMA_SLOW * 2):
            return "range"
        kl = list(reversed(kl))
        closes = [float(x[4]) for x in kl]
        ef = ema(closes[-EMA_FAST * 3 :], EMA_FAST)
        es = ema(closes[-EMA_SLOW * 3 :], EMA_SLOW)
        price = closes[-1]
        # conservative: require price+fast align with slow
        if price >= es and ef >= es:
            return "up"
        if price <= es and ef <= es:
            return "down"
        return "range"
    except Exception:
        return "range"


def apply_strategy_to_mp(symbol: str, mp: dict):
    """mp를 시장 국면에 맞게 조정. (진입 단계에서만 사용)"""
    regime = detect_market_regime(symbol)
    strategy = select_strategy(regime)

    if strategy in ("low_risk", "no_trade"):
        return False, f"STRATEGY_BLOCK: {regime} -> {strategy}", strategy

    if strategy == "mean_reversion":
        mp["enter_score"] = int(mp.get("enter_score", 65)) + 5

    return True, f"STRATEGY_OK: {regime} -> {strategy}", strategy

# =========================
# PnL estimate
# =========================

def _get_realized_pnl_usdt(symbol: str, entry_ts: float):
    """Fetch realized (closed) PnL from Bybit closed-pnl endpoint.
    Returns pnl (float) or None. Uses a conservative lookback to reduce API load.
    """
    if DRY_RUN or (not USE_REALIZED_PNL):
        return None
    try:
        end_ms = int(time.time() * 1000)
        lookback_ms = int(max(60, REALIZED_PNL_LOOKBACK_MIN) * 60 * 1000)
        start_ms = max(0, end_ms - lookback_ms)
        # Only query if entry was within lookback, else clamp
        if entry_ts and entry_ts > 0:
            start_ms = max(start_ms, int(entry_ts * 1000) - 5 * 60 * 1000)

        params = {
            "category": CATEGORY,
            "symbol": symbol,
            "limit": "50",
            "startTime": str(start_ms),
            "endTime": str(end_ms),
        }
        j = http.request("GET", "/v5/position/closed-pnl", params, auth=True)
        lst = (j.get("result") or {}).get("list") or []
        if not lst:
            return None

        # Choose the most recent record whose updatedTime/createdTime/closeTime is after entry_ts
        best = None
        entry_ms = int(entry_ts * 1000) if entry_ts else 0
        for r in lst:
            # time fields vary; try a few
            t_ms = 0
            for k in ("updatedTime", "createdTime", "closeTime", "execTime"):
                v = r.get(k)
                if v is None:
                    continue
                try:
                    t_ms = int(float(v))
                    if t_ms > 10_000_000_000:  # already ms
                        pass
                    else:
                        t_ms = int(float(v) * 1000)
                    break
                except Exception:
                    continue
            if entry_ms and t_ms and t_ms < entry_ms:
                continue
            best = r
            break

        if not best:
            best = lst[0]
        pnl = best.get("closedPnl")
        if pnl is None:
            pnl = best.get("pnl")
        if pnl is None:
            return None
        return float(pnl)
    except Exception:
        return None
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

def _log_event(event: str, **fields):
    if not LOG_EVENTS:
        return
    try:
        rec = {"ts": int(time.time()), "event": str(event)}
        for k, v in fields.items():
            # jsonable coercion
            try:
                json.dumps(v)
                rec[k] = v
            except Exception:
                rec[k] = str(v)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _tg_keyboard():
    return {
        "keyboard": [
            ["▶️ 시작 /start", "⏹️ 중지 /stop"],
            ["🛡️ 안전 /safe", "⚔️ 공격 /aggro"],
            ["📊 상태 /status", "🔍 이유 /why", "❓ 도움말 /help"],
            ["🟢 매수 /buy", "🔴 숏 /short", "💥 긴급청산 /panic"],
            ["🎛️ 버튼켜기 /ui on", "🎛️ 버튼끄기 /ui off"],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
    }
def tg_send(msg: str, reply_markup=None):
    print(msg, flush=True)

    if not BOT_TOKEN or not CHAT_ID:
        print("tg_send skip: missing BOT_TOKEN or CHAT_ID", flush=True)
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
    }

    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        r = requests.post(url, json=payload, timeout=10)
        print("tg_send status:", r.status_code, flush=True)
        print("tg_send resp:", r.text, flush=True)
    except Exception as e:
        print("tg_send error:", repr(e), flush=True)

def _is_order_status_unknown(err: Exception) -> bool:
    try:
        s = str(err)
    except Exception:
        return False
    return "ORDER STATUS UNKNOWN" in s and "safer stop" in s

def _ai_record_pnl(pnl_est: float):
    """Record trade result into ai_learn (Supabase/JSON).
    By default we DO NOT count near-breakeven trades as win/loss to avoid skew from fees/rounding.
    Tune via env:
      - AI_COUNT_BREAKEVEN=1  (count pnl==0 as loss per original logic)
      - AI_PNL_EPS=0.01       (ignore |pnl| < eps, default 0.01)
      - AI_LEARN_DEBUG=1      (print errors)
    """
    try:
        pnl = float(pnl_est or 0.0)
        eps = float(os.getenv("AI_PNL_EPS", "0.01"))
        count_be = str(os.getenv("AI_COUNT_BREAKEVEN", "0")).lower() in ("1","true","yes","y","on")
        if (abs(pnl) < eps) and (not count_be):
            return  # neutral -> don't update wins/losses
        record_trade_result(pnl)
    except Exception as e:
        if str(os.getenv("AI_LEARN_DEBUG","0")).lower() in ("1","true","yes","y","on"):
            print("❌ ai_learn record_trade_result error:", repr(e), flush=True)

# =========================
# Trader
# =========================
class Trader:
    def __init__(self, state=None):
        self.state = state if isinstance(state, dict) else {}
        # --- option flags ---
        self.state.setdefault("avoid_low_rsi", False)

        # --- quant core instances ---
        self._slip = None
        self._exec = None
        self._perf = None
        self._port = None
        self._wf = None
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

        # --- local strategy guard (fallback if quant_core perf not available) ---
        self._sg = StrategyGuard() if StrategyGuard is not None else None

        self.positions = []  # dict list
        # --- stats / day reset ---
        self.win = 0
        self.loss = 0
        self.day_profit = 0.0
        self.consec_losses = 0
        self._day_key = None
        self._day_entries = 0
        # ===== FINAL 안정성 세트 =====
        self._ks = KillSwitch()
        self._idempo = {}  # {key: ts}
        self._idempo_ttl = int(os.getenv("IDEMPO_TTL", "120"))  # 2분

        # 일일 PnL(간단 집계) - 너의 실현손익 기록 방식에 맞춰 trader 내부에서 업데이트하면 됨
        self.daily_pnl = float(safe_read_json(data_path("daily_pnl.json"), {"pnl": 0.0}).get("pnl", 0.0))

        # 재시작해도 연속손실/기타 유지하고 싶으면 여기서 로드
        persisted = safe_read_json(data_path("runtime_state.json"), {})
        try:
            self.consec_losses = int(persisted.get("consec_losses", getattr(self, "consec_losses", 0)) or 0)
        except Exception:
            pass
        # --- cooldown / throttles ---
        self._cooldown_until = 0
        self._last_alert_ts = 0
        self._last_err_ts = 0
        # circuit breaker
        self._cb_err_count = 0
        self._cb_window_start = 0.0

        # --- leverage cache ---
        self._lev_set_cache = {}

        # --- scan ---
        self._last_scan_ts = 0

        try:
            if USE_EXECUTION_ENGINE and ExecutionEngine is not None:
                self._slip = SlippageTracker() if SlippageTracker is not None else None
                self._exec = ExecutionEngine(get_price_fn=get_price, place_market_fn=order_market, tracker=self._slip)
        except Exception:
            self._exec = None
        
        try:
            if USE_STRATEGY_PERF and StrategyPerformance is not None:
                self._perf = StrategyPerformance(
                    window=PERF_WINDOW,
                    min_trades=PERF_MIN_TRADES,
                    disable_below_winrate=PERF_DISABLE_BELOW_WINRATE,
                    disable_for_min=PERF_DISABLE_FOR_MIN,
                )
        except Exception:
            self._perf = None
        
        try:
            if USE_PORTFOLIO_OPT and PortfolioOptimizer is not None:
                self._port = PortfolioOptimizer(
                    base_mult=PORT_BASE_MULT,
                    max_mult=PORT_MAX_MULT,
                    min_mult=PORT_MIN_MULT,
                    smooth=PORT_SMOOTH,
                )
        except Exception:
            self._port = None

        # --- Institutional portfolio/risk (lightweight) ---
        try:
            if USE_INST_RISK_MODEL and (InstitutionalRiskModel is not None):
                self._inst_risk = InstitutionalRiskModel(max_symbol_weight=float(INST_MAX_WEIGHT))
            else:
                self._inst_risk = None
        except Exception:
            self._inst_risk = None

        try:
            if USE_INST_PORTFOLIO and (PortfolioEngine is not None):
                self._inst_port = PortfolioEngine(risk=self._inst_risk, max_symbols=int(INST_MAX_SYMBOLS))
                self._inst_weights = {}
                self._inst_last_recalc = 0.0
            else:
                self._inst_port = None
                self._inst_weights = {}
                self._inst_last_recalc = 0.0
        except Exception:
            self._inst_port = None
            self._inst_weights = {}
            self._inst_last_recalc = 0.0


        
        try:
            if USE_WALKFORWARD and WalkForwardScheduler is not None:
                self._wf = WalkForwardScheduler(interval_hours=float(OPTIMIZE_INTERVAL_HOURS))
        except Exception:
            self._wf = None
    def _get_lot_size(self, symbol):
        """Bybit 최소 주문수량/스텝 조회. 실패 시 안전한 기본값 반환."""
        if symbol in _lot_cache:
            return _lot_cache[symbol]

        try:
            r = http.request(
                "GET",
                "/v5/market/instruments-info",
                {"category": CATEGORY, "symbol": symbol},
                auth=False,
            )
            info = (r.get("result") or {}).get("list") or []
            info = (info[0] or {}).get("lotSizeFilter") if info else None
            if not info:
                raise RuntimeError("instruments-info empty")
            step = float(info.get("qtyStep") or 0.01)
            min_qty = float(info.get("minOrderQty") or 0.01)
            if step <= 0:
                step = 0.01
            if min_qty <= 0:
                min_qty = step
            _lot_cache[symbol] = (step, min_qty)
            return step, min_qty
        except Exception:
            return 0.01, 0.01


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


    def _cb_on_error(self, where: str, exc: Exception):
        if not CB_ON:
            return
        try:
            now = time.time()
            ws = float(getattr(self, "_cb_window_start", 0.0) or 0.0)
            if (ws <= 0) or (now - ws > CB_WINDOW_SEC):
                self._cb_window_start = now
                self._cb_err_count = 0
            self._cb_err_count = int(getattr(self, "_cb_err_count", 0) or 0) + 1
            _log_event("api_error", where=where, err=str(exc), count=self._cb_err_count)
            if self._cb_err_count >= CB_ERR_MAX:
                self.trading_enabled = False
                self._cooldown_until = now + CB_COOLDOWN_SEC
                self.notify_throttled(f"🛑 CircuitBreaker: API 오류 {self._cb_err_count}회/{CB_WINDOW_SEC}s → 거래 OFF {int(CB_COOLDOWN_SEC/60)}분", 120)
                self.state["last_event"] = "STOP: circuit breaker"
        except Exception:
            pass

    def _cb_on_success(self):
        if not CB_ON:
            return
        # decay error count slowly
        try:
            c = int(getattr(self, "_cb_err_count", 0) or 0)
            if c > 0:
                self._cb_err_count = max(0, c - 1)
        except Exception:
            pass

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
        target_lev = int(mp["lev"])
        key = f"{symbol}:{self.mode}:{target_lev}"
        if self._lev_set_cache.get(key):
            return
        if DRY_RUN:
            self._lev_set_cache[key] = True
            return
        try:
            set_leverage(symbol, target_lev)
        except Exception as e:
            msg = str(e)
            if not _is_bybit_lev_not_modified("110043" if "110043" in msg else "", msg):
                raise
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

    def _recalc_inst_weights(self):
        """Recalculate portfolio weights periodically (cheap, safe).
        Uses recent close prices from Bybit klines.
        """
        if not self._inst_port:
            return
        # every N seconds
        interval = int(os.getenv("INST_RECALC_SEC", "120"))
        if time.time() - float(getattr(self, "_inst_last_recalc", 0.0) or 0.0) < interval:
            return
        self._inst_last_recalc = time.time()
        closes_map = {}
        # universe: current symbols list (cap for safety)
        universe = list(dict.fromkeys([s.upper() for s in (self.symbols or []) if isinstance(s, str)]))
        cap = int(os.getenv("INST_UNIVERSE_CAP", "15"))
        universe = universe[:cap]
        for sym in universe:
            try:
                kl = get_klines(sym, ENTRY_INTERVAL, int(os.getenv("INST_LOOKBACK", "180")))
                if not kl:
                    continue
                kl = list(reversed(kl))
                closes = [float(x[4]) for x in kl if float(x[4] or 0) > 0]
                if len(closes) >= 20:
                    closes_map[sym] = closes
            except Exception:
                continue
        if not closes_map:
            return
        try:
            self._inst_weights = self._inst_port.allocate(closes_map, winrate_map=None)
            self.state["inst_weights"] = dict(list(self._inst_weights.items())[:8])
        except Exception as e:
            self.state["inst_weights_error"] = str(e)

    def _score_symbol(self, symbol: str, price: float):
        mp = self._mp()
        
        # ✅ 전략 라우팅(시장 국면 기반)
        mp = dict(mp)  # 원본 보호
        ok_strat, strat_msg, strat_key = apply_strategy_to_mp(symbol, mp)
        if not ok_strat:
            return {"ok": False, "reason": strat_msg, "strategy": strat_key}

        sp = get_spread_pct(symbol)
        if sp is not None and sp > MAX_SPREAD_PCT:
            return {"ok": False, "reason": f"SPREAD({sp:.2f}%)"}

        # --- liquidity filter (ticker based) ---
        if USE_LIQUIDITY_FILTER and (is_liquid_ok is not None):
            try:
                t = get_ticker(symbol) or {}
                bid = float(t.get("bid1Price") or 0)
                ask = float(t.get("ask1Price") or 0)
                turnover = float(t.get("turnover24h") or 0)
                okliq, msgliq = is_liquid_ok(bid, ask, turnover, MIN_TURNOVER24H_USDT, MAX_SPREAD_BPS)
                if not okliq:
                    return {"ok": False, "reason": msgliq, "strategy": strat_key}
            except Exception:
                pass

        # --- institutional risk snapshot (VaR/ES/vol) ---
        try:
            if USE_INST_RISK_MODEL and self._inst_risk is not None:
                kl = get_klines(symbol, ENTRY_INTERVAL, int(os.getenv("INST_RISK_LOOKBACK", "180")))
                if kl:
                    kl = list(reversed(kl))
                    closes = [float(x[4]) for x in kl if float(x[4] or 0) > 0]
                    snap = self._inst_risk.summarize_symbol(closes)
                    self.state["risk"] = {"symbol": symbol, **snap}
        except Exception:
            pass

        avoid_low_rsi = bool(self.state.get("avoid_low_rsi", False))

        mtf = None
        if USE_MTF_FILTER:
            try:
                mtf = _mtf_trend(symbol)
                self.state["mtf"] = {"symbol": symbol, "interval": int(MTF_TREND_INTERVAL), "trend": mtf}
            except Exception:
                mtf = None

        # MTF gate (HTF trend filter)
        mtf_norm = str(mtf or "").lower()
        mtf_block_long = (mtf_norm == "down")
        mtf_block_short = (mtf_norm == "up")

        if self.allow_long and (not mtf_block_long):
            okL, reasonL, scoreL, slL, tpL, aL = compute_signal_and_exits(
                symbol, "LONG", price, mp, avoid_low_rsi=avoid_low_rsi
            )
        elif self.allow_long and mtf_block_long:
            okL, reasonL, scoreL, slL, tpL, aL = False, f"MTF_BLOCK(LONG) trend={mtf}", -999, None, None, None
        else:
            okL, reasonL, scoreL, slL, tpL, aL = False, "LONG DISABLED", -999, None, None, None

        if self.allow_short and (not mtf_block_short):
            okS, reasonS, scoreS, slS, tpS, aS = compute_signal_and_exits(
                symbol, "SHORT", price, mp, avoid_low_rsi=avoid_low_rsi
            )
        elif self.allow_short and mtf_block_short:
            okS, reasonS, scoreS, slS, tpS, aS = False, f"MTF_BLOCK(SHORT) trend={mtf}", -999, None, None, None
        else:
            okS, reasonS, scoreS, slS, tpS, aS = False, "SHORT DISABLED", -999, None, None, None

        if scoreS > scoreL:
            return {"ok": okS, "side": "SHORT", "score": scoreS, "reason": reasonS, "sl": slS, "tp": tpS, "atr": aS, "strategy": strat_key}
        return {"ok": okL, "side": "LONG", "score": scoreL, "reason": reasonL, "sl": slL, "tp": tpL, "atr": aL, "strategy": strat_key}

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
    def _enter(self, symbol: str, side: str, price: float, reason: str, sl: float, tp: float, strategy: str = "", score: float = 0.0, atr: float = 0.0):
        mp = self._mp()
        lev = float(mp["lev"])
        order_usdt = float(mp["order_usdt"])

        # --- volatility position sizing (ATR/price) ---
        if USE_VOL_POSITION:
            try:
                scale = _vol_scale_from_atr(float(price), float(atr) if atr is not None else None)
                order_usdt = float(order_usdt) * float(scale)
                self.state["vol"] = {"symbol": symbol, "scale": float(scale), "target": float(VOL_TARGET)}
            except Exception:
                pass

        # --- portfolio optimizer: order_usdt multiplier ---
        try:
            if USE_PORTFOLIO_OPT and self._port is not None:
                mult = float(self._port.recommend_multiplier(symbol, score=float(score or 0.0), winrate_pct=None))
                order_usdt = float(order_usdt) * max(0.1, mult)
        except Exception:
            pass


        # --- institutional portfolio engine: weight-based multiplier ---
        try:
            if USE_INST_PORTFOLIO and self._inst_port is not None and isinstance(getattr(self, "_inst_weights", None), dict):
                mult2 = float(self._inst_port.multiplier_for_symbol(self._inst_weights, symbol))
                order_usdt = float(order_usdt) * max(0.1, mult2)
                self.state["inst_mult"] = {"symbol": symbol, "mult": mult2}
        except Exception:
            pass

        # qty 계산: 기본은 USDT*lev / price
        qty = (order_usdt * lev) / float(price)

        step, min_qty = self._get_lot_size(symbol)

        # step 단위 맞춤
        qty = math.floor(qty / step) * step

        # 최소 주문 수량 보정
        if qty < min_qty:
            qty = min_qty

        # optional: risk_engine 사용 (있을 때만)
        if USE_RISK_ENGINE and (calc_position_size is not None):
            try:
                # balance는 env로 주면 사용, 안 주면 USDT 잔고 조회
                if BALANCE_USDT_ENV.strip():
                    balance = float(BALANCE_USDT_ENV.strip())
                else:
                    balance = float(self._get_usdt_balance())

                qty2 = float(calc_position_size(symbol, float(price), balance, lev))
                if qty2 > 0:
                    # risk_engine도 lot 규칙 맞추기
                    qty2 = math.floor(qty2 / step) * step
                    if qty2 < min_qty:
                        qty2 = min_qty
                    qty = qty2
            except Exception as e:
                self.err_throttled(f"⚠️ risk_engine 실패: {e}")

        if qty <= 0:
            raise Exception("qty<=0")

        self._ensure_leverage(symbol)

        if not DRY_RUN:
            side_ex = "Buy" if side == "LONG" else "Sell"
            try:
                if USE_EXECUTION_ENGINE and self._exec is not None:
                    self._exec.market(symbol, side_ex, qty, reduce_only=False)
                else:
                    order_market(symbol, side_ex, qty)
            except Exception as e:
                # Bybit sometimes returns "ORDER STATUS UNKNOWN" even if the order actually executed.
                # Confirm by checking real position size; if it exists, treat as success.
                if _is_order_status_unknown(e):
                    try:
                        if get_position_size(symbol) > 0:
                            self.notify_throttled(f"⚠️ 주문 상태 확인 지연(UNKNOWN) - 포지션 존재로 성공 처리: {symbol}")
                        else:
                            raise
                    except Exception:
                        raise
                else:
                    raise


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

"strategy": strategy,
"entry_score": float(score or 0.0),
        }
        self.positions.append(pos)

        self._cooldown_until = time.time() + COOLDOWN_SEC
        self._day_entries += 1
        self.state["entry_reason"] = reason

        self.notify(f"✅ ENTER {symbol} {side} qty={qty}\n{reason}\n⏳ stop={sl:.6f} tp={tp:.6f} tp1={tp1_price}")
        _log_event("enter", symbol=symbol, side=side, qty=qty, price=price, stop=sl, tp=tp, strategy=strategy, score=float(score or 0.0), order_usdt=order_usdt, lev=lev, vol_scale=self.state.get('vol', {}).get('scale'))

    def _close_qty(self, symbol: str, side: str, close_qty: float):
        if DRY_RUN:
            return
        if close_qty <= 0:
            return

        side_ex = "Sell" if side == "LONG" else "Buy"

        try:
            if USE_EXECUTION_ENGINE and self._exec is not None:
                self._exec.market(symbol, side_ex, close_qty, reduce_only=True)
            else:
                order_market(symbol, side_ex, close_qty, reduce_only=True)
        except Exception as e:
            # Bybit sometimes returns "ORDER STATUS UNKNOWN" even if the order actually executed.
            # Confirm by checking real position size; if already closed, treat as success.
            if _is_order_status_unknown(e):
                try:
                    if get_position_size(symbol) <= 0:
                        return
                except Exception:
                    pass
            raise

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
        pnl_real = _get_realized_pnl_usdt(symbol, float(pos.get("entry_ts") or 0))
        pnl_est = pnl_real if (pnl_real is not None) else estimate_pnl_usdt(side, entry_price, price, notional)
        if pnl_real is not None:
            self.state["last_pnl_source"] = "REALIZED"
        else:
            self.state["last_pnl_source"] = "ESTIMATED"
        self.day_profit += pnl_est
        _ai_record_pnl(pnl_est)

        # --- strategy performance learning ---
        try:
            if USE_STRATEGY_PERF and self._perf is not None:
                self._perf.record_trade(str(pos.get("strategy") or ""), float(pnl_est))
        except Exception:
            pass

        # --- local strategy guard learning (fallback) ---
        try:
            if (not USE_STRATEGY_PERF or self._perf is None) and self._sg is not None:
                self._sg.record(str(pos.get("strategy") or ""), float(pnl_est))
        except Exception:
            pass
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
        _log_event("exit", symbol=symbol, side=side, why=why, exit_price=price, entry_price=entry_price, pnl=float(pnl_est), pnl_source=self.state.get('last_pnl_source'))
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
        winrate_local = wins / max(1, len(recent))

        m = self.mode
        t = self.tune.get(m, {}).copy()
        if not t:
            return

        if self.consec_losses >= 2 or avg < 0:
            t["enter_score"] = int(_clamp(int(t["enter_score"]) + GROWTH_STEP_SCORE, GROWTH_SCORE_MIN, GROWTH_SCORE_MAX))
            t["order_usdt"] = float(_clamp(float(t["order_usdt"]) - GROWTH_STEP_USDT, GROWTH_USDT_MIN, GROWTH_USDT_MAX))
            t["lev"] = int(_clamp(int(t["lev"]) - GROWTH_STEP_LEV, GROWTH_LEV_MIN, GROWTH_LEV_MAX))
            self.tune[m] = t
            self._lev_set_cache = {}  # lev 변경 가능성 -> 캐시 리셋
            self.notify_throttled(
                f"🧠 AI성장(보수): score↑ usdt↓ lev↓ | score={t['enter_score']} usdt={t['order_usdt']} lev={t['lev']} (avg={avg:.2f}, winrate={winrate_local:.0%})",
                90,
            )
            return

        if avg > 0 and winrate_local >= 0.55:
            t["enter_score"] = int(_clamp(int(t["enter_score"]) - 1, GROWTH_SCORE_MIN, GROWTH_SCORE_MAX))
            t["order_usdt"] = float(_clamp(float(t["order_usdt"]) + GROWTH_STEP_USDT, GROWTH_USDT_MIN, GROWTH_USDT_MAX))
            t["lev"] = int(_clamp(int(t["lev"]) + 0, GROWTH_LEV_MIN, GROWTH_LEV_MAX))
            self.tune[m] = t
            self.notify_throttled(
                f"🧠 AI성장(완화): score↓ usdt↑ | score={t['enter_score']} usdt={t['order_usdt']} lev={t['lev']} (avg={avg:.2f}, winrate={winrate_local:.0%})",
                90,
            )

    # ---------------- Telegram commands ----------------
    def handle_command(self, text: str):
        cmd = (text or "").strip()
        if not cmd:
            return

        parts = cmd.split()
        c0 = ""
        cmd_idx = -1

        for i, p in enumerate(parts):
            if p.startswith("/"):
                c0 = p.lower()
                cmd_idx = i
                break

        if not c0:
            return

        arg = " ".join(parts[cmd_idx + 1:]) if cmd_idx >= 0 else ""

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
            self._lev_set_cache = {}
            self.notify("🛡 SAFE 모드로 전환")
            return
        if c0 in ("/aggro", "/attack"):
            self.mode = "AGGRO"
            self._lev_set_cache = {}
            self.notify("⚔️ AGGRO 모드로 전환")
            return

        if c0 == "/autod":
            v = (arg or "").lower()
            self.auto_discovery = (v in ("on", "1", "true", "yes", "y"))
            self.notify(f"🌐 AUTO_DISCOVERY={self.auto_discovery}")
            return

        if c0 == "/autosymbol":
            v = (arg or "").lower()
            self.auto_symbol = (v in ("on", "1", "true", "yes", "y"))
            self.notify(f"🧭 AUTO_SYMBOL={self.auto_symbol}")
            return

        if c0 == "/div":
            v = (arg or "").lower()
            self.diversify = (v in ("on", "1", "true", "yes", "y"))
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
            self.notify("📌 후보심볼:\n" + ",".join(self.symbols[:60]) + ("" if len(self.symbols) <= 60 else "\n..."))
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
            self.auto_symbol = False  # ✅ FIX 4
            self.notify(f"📌 FIXED_SYMBOL={self.fixed_symbol} | AUTO_SYMBOL={self.auto_symbol}")
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

        if c0 == "/avoidrsi":
            # 사용법: /avoidrsi on|off
            v = (arg or "").lower()
            self.state["avoid_low_rsi"] = (v in ("on", "1", "true", "yes", "y"))
            self.notify(f"🧠 avoid_low_rsi={self.state['avoid_low_rsi']}")
            return

        if c0 == "/help":
            self.notify(self.help_text())
            return

        if c0 == "/why":
            self.notify(self.why_text())
            return

        if c0 == "/status":
            if self.state.get("tg_buttons_on", False):
                tg_send(self.status_text(), reply_markup=_tg_keyboard())
            else:
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

        if c0 == "/ui":
            v = (arg or "").lower()
            on = v in ("on", "1", "true", "yes", "y")

            self.state["tg_buttons_on"] = on

            if on:
                tg_send("🧩 UI ON", reply_markup=_tg_keyboard())
            else:
                tg_send("🧩 UI OFF", reply_markup={"remove_keyboard": True})
            return

        if c0.startswith("/"):
            self.notify("❓ 모르는 명령. /help")
            return

    def help_text(self):
        return (
            "📌 명령어\n"
            "/start /stop\n"
            "/safe /aggro\n"
            "/status /why /doctor\n"
            "/buy /short /sell /panic\n"
            "\n"
            "🧭 멀티코인\n"
            "/autosymbol on|off\n"
            "/symbols\n"
            "/add BTCUSDT,ETHUSDT\n"
            "/remove BTCUSDT\n"
            "/setsymbol BTCUSDT  (AUTO_SYMBOL 자동 OFF)\n"
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
            "\n"
            "🧠 옵션\n"
            "/avoidrsi on|off   (RSI<40 회피)\n"
        )

    def why_text(self):
        """Show the most likely reason the bot is not entering a trade."""
        try:
            mp = self._mp()
        except Exception:
            mp = {}

        last_scan = self.state.get("last_scan") or {}
        reasons = last_scan.get("reasons") or []
        picked = last_scan.get("picked")
        mtf = self.state.get("mtf") or {}
        last_skip = str(getattr(self, "_last_skip_reason", "") or self.state.get("last_skip_reason", "") or "")
        last_event = str(self.state.get("last_event", "") or "")
        cooldown_left = max(0, int(float(getattr(self, "_cooldown_until", 0) or 0) - time.time()))

        lines = []
        lines.append("🔍 WHY NO TRADE")
        lines.append(f"ON={bool(self.trading_enabled)} | MODE={self.mode} | DRY_RUN={DRY_RUN}")
        lines.append(f"enter_score>={mp.get('enter_score', '-')} | lev={mp.get('lev', '-')} | order_usdt={mp.get('order_usdt', '-')}")
        lines.append(f"AUTO_SYMBOL={self.auto_symbol} | DISCOVERY={self.auto_discovery} | DIVERSIFY={self.diversify} | MAX_POS={self.max_positions}")
        lines.append(f"positions={len(self.positions or [])}/{self.max_positions} | cooldown_left={cooldown_left}s | allowed_now={entry_allowed_now_utc()}")
        lines.append(f"filters: MTF={USE_MTF_FILTER} trend={mtf.get('trend', '-')} | LIQ={USE_LIQUIDITY_FILTER} | avoid_low_rsi={bool(self.state.get('avoid_low_rsi', False))}")

        if last_skip:
            lines.append(f"last_skip={last_skip}")
        if last_event:
            lines.append(f"last_event={last_event}")

        if picked:
            try:
                lines.append(f"picked={picked.get('symbol')} {picked.get('side')} score={picked.get('score')} strategy={picked.get('strategy', '-')}")
            except Exception:
                lines.append(f"picked={picked}")
        else:
            lines.append("picked=None")

        if reasons:
            lines.append("최근 스캔 차단/탈락:")
            for r in reasons[:12]:
                lines.append(f"- {r}")
        else:
            lines.append("최근 스캔 기록 없음. /status 확인 후 1~2틱 뒤 다시 /why")

        return "\n".join(lines)

    def status_text(self):
        total = self.win + self.loss
        winrate_day = (self.win / total * 100) if total else 0.0
        mp = self._mp()

        lines = []
        lines.append(f"🧠 DRY_RUN={DRY_RUN} | ON={self.trading_enabled} | MODE={self.mode} | AI_GROWTH={self.ai_growth}")
        lines.append(f"⚙️ lev={mp['lev']} | order_usdt={mp['order_usdt']} | enter_score>={mp['enter_score']}")
        lines.append(f"⏰ entry_hours_utc={TRADE_HOURS_UTC} | allowed_now={entry_allowed_now_utc()}")
        lines.append(f"💸 fee={FEE_RATE:.4%}/side | slip={SLIPPAGE_BPS:.1f}bps/side | partialTP={PARTIAL_TP_ON}({PARTIAL_TP_PCT:.0%})")
        lines.append(f"🌐 base={BYBIT_BASE_URL} | proxy={'ON' if PROXIES else 'OFF'} | settleCoin={SETTLE_COIN}")
        lines.append(f"🧭 AUTO_SYMBOL={self.auto_symbol} FIXED={self.fixed_symbol} | DISCOVERY={self.auto_discovery}")
        lines.append(f"🧩 DIVERSIFY={self.diversify} MAX_POS={self.max_positions} | universe={len(self.symbols)}")
        lines.append(f"🧠 avoid_low_rsi={bool(self.state.get('avoid_low_rsi', False))} | risk_engine={'ON' if (USE_RISK_ENGINE and calc_position_size is not None) else 'OFF'}")

        if self.state.get("last_scan"):
            picked = (self.state["last_scan"] or {}).get("picked")
            if picked:
                lines.append(f"🔎 last_pick={picked.get('symbol')} {picked.get('side')} score={picked.get('score')}")

        if self.positions:
            for p in self.positions[:5]:
                lines.append(
                    f"📍 POS {p['symbol']} {p['side']} entry={p['entry_price']:.6f} stop={p['stop_price']:.6f} tp={p['tp_price']:.6f} tp1={p['tp1_price']}"
                )
        else:
            lines.append("📍 POS=None")
            lines.append(f"📈 day_profit≈{self.day_profit:.2f} | winrate={winrate_day:.1f}% (W{self.win}/L{self.loss}) | consec_losses={self.consec_losses}")

        if self.state.get("entry_reason"):
            lines.append(f"🧠 근거:\n{self.state['entry_reason']}")
        if self.state.get("last_event"):
            lines.append(f"📝 last={self.state['last_event']}")

        stats = get_ai_stats()
        lines.append(f"🤖 AI Winrate: {stats.get('winrate',0)}% ({stats.get('wins',0)}W/{stats.get('losses',0)}L)")
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
            ok, reason, score, sl, tp, a = compute_signal_and_exits(
                symbol, side, price, mp, avoid_low_rsi=bool(self.state.get("avoid_low_rsi", False))
            )
            self._enter(symbol, side, price, reason + "- manual=True\n", sl, tp)
        except Exception as e:
            self.err_throttled(f"❌ manual enter 실패: {e}")

    def manual_exit(self, why: str, force=False):
        try:
            if not self.positions and not force:
                self.notify("⚠️ 포지션 없음")
                return
            for idx in range(len(self.positions) - 1, -1, -1):
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
        ok, reason, score, sl_new, tp_new, a = compute_signal_and_exits(
            symbol, side, price, mp, avoid_low_rsi=bool(self.state.get("avoid_low_rsi", False))
        )

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
            eff_stop = max(eff_stop, pos["trail_price"]) if side == "LONG" else min(eff_stop, pos["trail_price"])

        # time exit
        if pos.get("entry_ts") and (time.time() - pos["entry_ts"]) > (TIME_EXIT_MIN * 60):
            self._exit_position(idx, "TIME EXIT")
            return

        # score drop
        if score <= EXIT_SCORE_DROP:
            self._exit_position(idx, f"SCORE DROP {score}")
            return

        # partial TP
        if PARTIAL_TP_ON and (not pos.get("tp1_done")) and pos.get("tp1_price") is not None and (not DRY_RUN):
            try:
                qty_total = get_position_size(symbol)
                if qty_total > 0:
                    hit_tp1 = (price >= pos["tp1_price"]) if side == "LONG" else (price <= pos["tp1_price"])
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

        # stop / tp
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
        # ===== KILL SWITCH / COOLDOWN =====
        if self._ks.in_cooldown():
            return

        msg = self._ks.check_losses(getattr(self, "consec_losses", 0))
        if msg:
            self.notify(msg)
            return

        msg = self._ks.check_daily_pnl(getattr(self, "daily_pnl", 0.0))
        if msg:
            self.notify(msg)
            return

        self._reset_day()

        # health/state
        self.state["trading_enabled"] = self.trading_enabled
        self.state["mode"] = self.mode
        self.state["bybit_base"] = BYBIT_BASE_URL
        self.state["proxy"] = "ON" if PROXIES else "OFF"

        if not self.trading_enabled:
            self.state["last_event"] = "거래 OFF"
            return

        if getattr(self, "consec_losses", 0) >= MAX_CONSEC_LOSSES:
            self.notify_throttled("🛑 연속 손실 제한 도달. 거래 중지")
            self.trading_enabled = False
            self.state["last_event"] = "STOP: consec losses"
            return

        # discovery + exchange sync
        try:
            self._refresh_discovery()
            self._cb_on_success()
        except Exception as e:
            self._cb_on_error("refresh_discovery", e)

        try:
            self._sync_real_positions()
            self._cb_on_success()
        except Exception as e:
            self._cb_on_error("sync_real_positions", e)
        # institutional portfolio weights (periodic)
        try:
            self._recalc_inst_weights()
        except Exception:
            pass


        # milestone notify (optional)
        try:
            msg = check_winrate_milestone()
            if msg:
                self.notify(msg)
        except Exception:
            pass

        # manage open positions first
        if self.positions:
            for idx in range(len(self.positions) - 1, -1, -1):
                try:
                    self._manage_one(idx)
                except Exception as e:
                    self.err_throttled(f"❌ manage 실패: {e}")
                    self._cb_on_error("manage", e)
            return

        # ===== runtime persist =====
        try:
            atomic_write_json(data_path("runtime_state.json"), {
                "consec_losses": int(getattr(self, "consec_losses", 0) or 0),
                "ts": int(time.time()),
            })
            atomic_write_json(data_path("daily_pnl.json"), {
                "pnl": float(getattr(self, "daily_pnl", 0.0) or 0.0),
                "ts": int(time.time()),
            })
        except Exception:
            pass

        # entry gating
        if time.time() < getattr(self, "_cooldown_until", 0):
            self.state["last_event"] = "대기: cooldown"
            _log_event("skip", why="cooldown")
            return

        if getattr(self, "_day_entries", 0) >= MAX_ENTRIES_PER_DAY:
            self.state["last_event"] = "대기: 일일 진입 제한"
            _log_event("skip", why="max_entries_per_day")
            return

        if not entry_allowed_now_utc():
            self.state["last_event"] = f"대기: 시간필터(UTC {TRADE_HOURS_UTC})"
            _log_event("skip", why="time_filter", trade_hours_utc=TRADE_HOURS_UTC)
            return

        # fixed symbol mode
        if not self.auto_symbol:
            symbol = self.fixed_symbol
            try:
                price = get_price(symbol)
                info = self._score_symbol(symbol, price)
                self.state["entry_reason"] = info.get("reason")

                if not info.get("ok"):
                    self.state["last_event"] = f"대기: {symbol} not ok"
                    return

                mp = self._mp()
                if int(info.get("score", 0)) < int(mp["enter_score"]):
                    self.state["last_event"] = f"대기: score={info.get('score')}"
                    return

                # --- strategy perf gate ---
                try:
                    if USE_STRATEGY_PERF and self._perf is not None:
                        okp, msgp = self._perf.allow(str(info.get("strategy") or ""))
                        if not okp:
                            self.state["last_event"] = f"대기: {msgp}"
                            return
                except Exception:
                    pass

                # --- local strategy guard gate (fallback) ---
                try:
                    if (not USE_STRATEGY_PERF or self._perf is None) and self._sg is not None:
                        if not self._sg.allow(str(info.get("strategy") or "")):
                            self.state["last_event"] = "대기: 전략 성과(로컬)"
                            return
                except Exception:
                    pass

                self._enter(symbol, info["side"], price, info["reason"], info["sl"], info["tp"], str(info.get("strategy") or ""), float(info.get("score") or 0.0), float(info.get("atr") or 0.0))
                self.state["last_event"] = f"ENTER {symbol} {info['side']}"
            except Exception as e:
                self.err_throttled(f"❌ entry 실패(fixed): {e}")
                self._cb_on_error("entry_fixed", e)
            return

        # scan mode
        pick = self.pick_best()
        if not pick:
            self.state["last_event"] = "대기: 스캔 결과 없음"
            return

        try:
            # --- strategy perf gate ---
            try:
                if USE_STRATEGY_PERF and self._perf is not None:
                    okp, msgp = self._perf.allow(str(pick.get("strategy") or ""))
                    if not okp:
                        self.state["last_event"] = f"대기: {msgp}"
                        return
            except Exception:
                pass

            # --- local strategy guard gate (fallback) ---
            try:
                if (not USE_STRATEGY_PERF or self._perf is None) and self._sg is not None:
                    if not self._sg.allow(str(pick.get("strategy") or "")):
                        self.state["last_event"] = "대기: 전략 성과(로컬)"
                        return
            except Exception:
                pass

            self._enter(pick["symbol"], pick["side"], pick["price"], pick["reason"], pick["sl"], pick["tp"], str(pick.get("strategy") or ""), float(pick.get("score") or 0.0), float(pick.get("atr") or 0.0))
        except Exception as e:
            self.err_throttled(f"❌ entry 실패(scan): {e}")
            self._cb_on_error("entry_scan", e)

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
            "avoid_low_rsi": bool(self.state.get("avoid_low_rsi", False)),
            "use_risk_engine": bool(USE_RISK_ENGINE and calc_position_size is not None),
        }
# ======================================================================
# FINAL 10/10 PATCH (ONE-PASTE) - Append to the VERY END of trader.py
# - Exchange-level idempotency (orderLinkId) + order confirm loop
# - Auto reconcile real positions on startup/tick (no more "internal empty")
# - Range-regime filter (skip entries in sideways regime)
# - Correlation exposure guard (avoid "fake diversification")
# - Persist more runtime settings across restarts
# ======================================================================

import uuid as _uuid

# ----------------------------
# ENV knobs (safe defaults)
# ----------------------------
FINAL10_ON = str(os.getenv("FINAL10_ON", "true")).lower() in ("1","true","yes","y","on")

# Regime filter: skip entries if EMA_slow slope is too flat (sideways)
REGIME_FILTER_ON = str(os.getenv("REGIME_FILTER_ON", "true")).lower() in ("1","true","yes","y","on")
REGIME_LOOKBACK = int(os.getenv("REGIME_LOOKBACK", "20"))            # bars back to measure slope
REGIME_MIN_SLOPE_BPS = float(os.getenv("REGIME_MIN_SLOPE_BPS", "6")) # 6 bps (~0.06%) over lookback -> trend-ish

# Correlation groups: prevent multiple positions in same "moves-together" bucket
# Format: "BTCUSDT|ETHUSDT|SOLUSDT;XRPUSDT|ADAUSDT" (semicolon = group)
CORR_GROUPS = os.getenv("CORR_GROUPS", "BTCUSDT|ETHUSDT|SOLUSDT")
CORR_GUARD_ON = str(os.getenv("CORR_GUARD_ON", "true")).lower() in ("1","true","yes","y","on")

# Order confirm loop
CONFIRM_TRIES = int(os.getenv("ORDER_CONFIRM_TRIES", "12"))
CONFIRM_SLEEP = float(os.getenv("ORDER_CONFIRM_SLEEP", "0.5"))

# Persist more settings
PERSIST_MORE_STATE = str(os.getenv("PERSIST_MORE_STATE", "true")).lower() in ("1","true","yes","y","on")

def _final10_parse_corr_groups(spec: str):
    groups = []
    try:
        for g in (spec or "").split(";"):
            g = g.strip()
            if not g:
                continue
            syms = [x.strip().upper() for x in g.split("|") if x.strip()]
            if syms:
                groups.append(set(syms))
    except Exception:
        pass
    return groups

_FINAL10_CORR_GROUPS = _final10_parse_corr_groups(CORR_GROUPS)

def _final10_regime_slope_bps(symbol: str):
    """
    Returns slope in bps of EMA_slow over REGIME_LOOKBACK bars.
    If data insufficient, returns None (do not block).
    """
    try:
        kl = get_klines(symbol, ENTRY_INTERVAL, max(KLINE_LIMIT, EMA_SLOW * 3 + REGIME_LOOKBACK + 10))
        if not kl or len(kl) < (EMA_SLOW * 3 + REGIME_LOOKBACK + 5):
            return None
        kl = list(reversed(kl))
        closes = [float(x[4]) for x in kl]
        # EMA slow now vs EMA slow (lookback) bars ago
        es_now = ema(closes[-EMA_SLOW * 3 :], EMA_SLOW)
        es_old = ema(closes[-(EMA_SLOW * 3 + REGIME_LOOKBACK) : -REGIME_LOOKBACK], EMA_SLOW)
        if es_old <= 0:
            return None
        slope_bps = abs((es_now - es_old) / es_old) * 10000.0
        return slope_bps
    except Exception:
        return None

def _final10_confirm_order(symbol: str, order_id: str = None, order_link_id: str = None):
    """
    Confirm order final status via /v5/order/realtime.
    Returns: ("Filled"/"Rejected"/"Cancelled"/"Unknown", raw_order_dict_or_None)
    """
    if DRY_RUN:
        return ("Filled", {"dry_run": True})
    last = None
    for _ in range(max(1, CONFIRM_TRIES)):
        try:
            params = {"category": CATEGORY, "symbol": symbol}
            if order_id:
                params["orderId"] = order_id
            if order_link_id:
                params["orderLinkId"] = order_link_id
            j = http.request("GET", "/v5/order/realtime", params, auth=True)
            lst = (j.get("result") or {}).get("list") or []
            if lst:
                o = lst[0]
                last = o
                st = (o.get("orderStatus") or "").strip()
                if st in ("Filled", "Rejected", "Cancelled"):
                    return (st, o)
        except Exception:
            pass
        time.sleep(CONFIRM_SLEEP)
    return ("Unknown", last)

# ----------------------------
# ORDER: Exchange-level idempotency + confirm loop
# This redefines the global order_market() used by Trader._enter/_close_qty
# ----------------------------
def order_market(symbol: str, side: str, qty: float, reduce_only=False):
    if not FINAL10_ON:
        # fallback to original behavior if disabled
        if DRY_RUN:
            return {"retCode": 0, "retMsg": "DRY_RUN"}
        qty = fix_qty(qty, symbol)
        body = {
            "category": CATEGORY,
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "IOC",
        }
        _pidx = _position_idx_for(side)
        if _pidx is not None:
            body["positionIdx"] = _pidx
        if reduce_only:
            body["reduceOnly"] = True
        resp = http.request("POST", "/v5/order/create", body, auth=True)
        if (resp or {}).get("retCode") != 0:
            raise Exception(f"ORDER FAILED: {resp}")
        return resp

    if DRY_RUN:
        return {"retCode": 0, "retMsg": "DRY_RUN"}

    qty = fix_qty(qty, symbol)

    # Use orderLinkId so retries won't duplicate orders at exchange level.
    order_link_id = f"bot-{int(time.time()*1000)}-{_uuid.uuid4().hex[:10]}"

    body = {
        "category": CATEGORY,
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "IOC",
        "orderLinkId": order_link_id,
    }
    _pidx = _position_idx_for(side)
    if _pidx is not None:
        body["positionIdx"] = _pidx
    if reduce_only:
        body["reduceOnly"] = True

    # 1) Create
    resp = http.request("POST", "/v5/order/create", body, auth=True)
    ret = str((resp or {}).get("retCode", "0"))
    if ret != "0":
        raise Exception(f"ORDER CREATE FAILED: {resp}")

    oid = (resp.get("result") or {}).get("orderId")

    # 2) Confirm (by orderId first, fallback by orderLinkId)
    st, od = _final10_confirm_order(symbol, order_id=oid, order_link_id=order_link_id)
    if st == "Filled":
        return resp
    if st in ("Rejected", "Cancelled"):
        raise Exception(f"ORDER {st}: {od or resp}")

    # 3) Unknown -> last chance by link id
    st2, od2 = _final10_confirm_order(symbol, order_id=None, order_link_id=order_link_id)
    if st2 == "Filled":
        return resp
    if st2 in ("Rejected", "Cancelled"):
        raise Exception(f"ORDER {st2}: {od2 or resp}")
    # If still unknown, try a last-resort position check.
    # This reduces false "UNKNOWN" errors caused by transient order-status lookup failures.
    try:
        plist = get_positions_all()
        for p in plist or []:
            if (p.get("symbol") or "").upper() != (symbol or "").upper():
                continue
            sz = float(p.get("size") or 0.0)
            ps = (p.get("side") or "").strip()
            # For entry: if we now have any size on that symbol, assume filled.
            if (not reduce_only) and sz > 0:
                return resp
            # For reduce-only exit: if size is zero, assume closed.
            if reduce_only and sz == 0:
                return resp
    except Exception:
        pass

    # If still unknown, treat as failure (safer than assuming filled)
    raise Exception(f"ORDER STATUS UNKNOWN (safer stop): symbol={symbol} side={side} qty={qty} linkId={order_link_id}")


# ----------------------------
# Trader patches (monkey patch)
# - reconcile real positions into internal state
# - regime filter + corr guard before entry
# - persist more settings
# ----------------------------
def _final10_reconcile_into_internal(self):
    """
    If exchange has open positions but self.positions is empty (or missing some),
    import them and compute stop/tp using current logic so bot can manage/exits.
    """
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

        # Keep for /health visibility
        self.state["real_positions"] = real[:8]

        # Build a lookup for existing internal positions
        existing = set()
        for pos in (self.positions or []):
            existing.add(((pos.get("symbol") or "").upper(), pos.get("side")))

        # Import missing ones
        imported = 0
        for rp in real:
            key = (rp["symbol"], rp["side"])
            if key in existing:
                continue

            price = get_price(rp["symbol"])
            mp = self._mp()
            ok, reason, score, sl, tp, a = compute_signal_and_exits(
                rp["symbol"], rp["side"], price, mp, avoid_low_rsi=bool(self.state.get("avoid_low_rsi", False))
            )

            # If we couldn't compute sl/tp, fall back to conservative ATR-based from current price
            if sl is None or tp is None:
                # crude fallback using ATR% if 'a' missing
                a = a if (a is not None and a > 0) else price * 0.01
                stop_dist = a * mp["stop_atr"]
                tp_dist = stop_dist * mp["tp_r"]
                if rp["side"] == "LONG":
                    sl = price - stop_dist
                    tp = price + tp_dist
                else:
                    sl = price + stop_dist
                    tp = price - tp_dist

            tp1_price = None
            if PARTIAL_TP_ON:
                if rp["side"] == "LONG":
                    tp1_price = price + (tp - price) * TP1_FRACTION
                else:
                    tp1_price = price - (price - tp) * TP1_FRACTION

            self.positions.append({
                "symbol": rp["symbol"],
                "side": rp["side"],
                "entry_price": rp["entry_price"] if rp["entry_price"] > 0 else price,
                "entry_ts": time.time(),  # unknown original ts -> set now (safe for management)
                "stop_price": sl,
                "tp_price": tp,
                "trail_price": None,
                "tp1_price": tp1_price,
                "tp1_done": False,
                "last_order_usdt": None,
                "last_lev": None,
                "imported_from_exchange": True,
            })
            imported += 1

            if imported > 0 and not getattr(self, "_import_once", False):
                pass
    except Exception as e:
        self.state["reconcile_error"] = str(e)

def _final10_corr_guard_block(self, symbol: str):
    if not (CORR_GUARD_ON and _FINAL10_CORR_GROUPS):
        return None
    sym = (symbol or "").upper()
    held = set()
    for p in (self.positions or []):
        held.add((p.get("symbol") or "").upper())
    # Find group containing sym
    for g in _FINAL10_CORR_GROUPS:
        if sym in g:
            # if any other symbol in same group already held -> block
            for h in held:
                if h in g and h != sym:
                    return f"CORR_GUARD: {h} already held in group"
    return None

# Wrap _score_symbol to add regime filter
if FINAL10_ON:
    _orig_score_symbol = getattr(Trader, "_score_symbol", None)
    if _orig_score_symbol:
        def _score_symbol_final10(self, symbol: str, price: float):
            # Regime filter only matters for entries (not for managing existing positions)
            if REGIME_FILTER_ON:
                slope = _final10_regime_slope_bps(symbol)
                if slope is not None and slope < REGIME_MIN_SLOPE_BPS:
                    return {"ok": False, "reason": f"RANGE_REGIME slope={slope:.2f}bps < {REGIME_MIN_SLOPE_BPS:.2f}bps"}

            return _orig_score_symbol(self, symbol, price)

        Trader._score_symbol = _score_symbol_final10

# Wrap _enter to add correlation guard and ensure leverage set cache resets properly
if FINAL10_ON:
    _orig_enter = getattr(Trader, "_enter", None)
    if _orig_enter:
        def _enter_final10(self, symbol: str, side: str, price: float, reason: str, sl: float, tp: float, *args, **kwargs):
            block = _final10_corr_guard_block(self, symbol)
            if block:
                self.state["last_event"] = f"대기: {block}"
                self.notify_throttled(f"⛔ 진입 차단({symbol}): {block}", 120)
                return
            return _orig_enter(self, symbol, side, price, reason, sl, tp, *args, **kwargs)
        Trader._enter = _enter_final10

# Wrap tick: reconcile early + persist more settings always
if FINAL10_ON:
    _orig_tick = getattr(Trader, "tick", None)
    if _orig_tick:
        def _tick_final10(self):
            try:
                # Reconcile FIRST so bot can manage imported positions immediately.
                _final10_reconcile_into_internal(self)
            except Exception:
                pass

            try:
                return _orig_tick(self)
            finally:
                if not PERSIST_MORE_STATE:
                    return
                # Persist runtime settings that users expect to survive restarts
                try:
                    atomic_write_json(
                        data_path("runtime_state.json"),
                        {
                            "consec_losses": int(getattr(self, "consec_losses", 0) or 0),
                            "daily_pnl": float(getattr(self, "daily_pnl", 0.0) or 0.0),
                            "trading_enabled": bool(getattr(self, "trading_enabled", True)),
                            "mode": str(getattr(self, "mode", MODE_DEFAULT)),
                            "allow_long": bool(getattr(self, "allow_long", ALLOW_LONG_DEFAULT)),
                            "allow_short": bool(getattr(self, "allow_short", ALLOW_SHORT_DEFAULT)),
                            "auto_symbol": bool(getattr(self, "auto_symbol", AUTO_SYMBOL_DEFAULT)),
                            "fixed_symbol": str(getattr(self, "fixed_symbol", FIXED_SYMBOL_DEFAULT)),
                            "auto_discovery": bool(getattr(self, "auto_discovery", AUTO_DISCOVERY_DEFAULT)),
                            "diversify": bool(getattr(self, "diversify", DIVERSIFY_DEFAULT)),
                            "max_positions": int(getattr(self, "max_positions", MAX_POSITIONS_DEFAULT) or 1),
                            "symbols": list(getattr(self, "symbols", SYMBOLS_ENV) or []),
                            "tune": dict(getattr(self, "tune", {}) or {}),
                            "ai_growth": bool(getattr(self, "ai_growth", AI_GROWTH_DEFAULT)),
                            "avoid_low_rsi": bool(self.state.get("avoid_low_rsi", False)),
                            "ts": int(time.time()),
                        },
                    )
                    # keep daily_pnl.json also (compat)
                    atomic_write_json(data_path("daily_pnl.json"), {"pnl": float(getattr(self, "daily_pnl", 0.0) or 0.0), "ts": int(time.time())})
                except Exception:
                    pass

        Trader.tick = _tick_final10

# Load persisted settings on __init__ without editing original __init__ block (safe monkey patch)
if FINAL10_ON:
    _orig_init = getattr(Trader, "__init__", None)
    if _orig_init:
        def _init_final10(self, state=None):
            _orig_init(self, state)

            # Load more settings if available
            try:
                persisted = safe_read_json(data_path("runtime_state.json"), {}) or {}
                if "trading_enabled" in persisted:
                    self.trading_enabled = bool(persisted.get("trading_enabled"))
                if "mode" in persisted and str(persisted.get("mode")):
                    self.mode = str(persisted.get("mode")).upper()
                if "allow_long" in persisted:
                    self.allow_long = bool(persisted.get("allow_long"))
                if "allow_short" in persisted:
                    self.allow_short = bool(persisted.get("allow_short"))
                if "auto_symbol" in persisted:
                    self.auto_symbol = bool(persisted.get("auto_symbol"))
                if "fixed_symbol" in persisted and str(persisted.get("fixed_symbol")):
                    self.fixed_symbol = str(persisted.get("fixed_symbol")).upper()
                if "auto_discovery" in persisted:
                    self.auto_discovery = bool(persisted.get("auto_discovery"))
                if "diversify" in persisted:
                    self.diversify = bool(persisted.get("diversify"))
                if "max_positions" in persisted:
                    self.max_positions = int(_clamp(int(persisted.get("max_positions") or 1), 1, 5))
                if "symbols" in persisted and isinstance(persisted.get("symbols"), list) and persisted.get("symbols"):
                    self.symbols = list(dict.fromkeys([str(s).upper() for s in persisted["symbols"] if str(s).strip()]))
                if "tune" in persisted and isinstance(persisted.get("tune"), dict) and persisted.get("tune"):
                    self.tune = persisted["tune"]
                if "ai_growth" in persisted:
                    self.ai_growth = bool(persisted.get("ai_growth"))
                if "avoid_low_rsi" in persisted:
                    self.state["avoid_low_rsi"] = bool(persisted.get("avoid_low_rsi"))

                # Restore daily pnl in-memory as well
                if "daily_pnl" in persisted:
                    try:
                        self.daily_pnl = float(persisted.get("daily_pnl") or 0.0)
                    except Exception:
                        pass
            except Exception:
                pass

            # Immediate reconcile after init
            try:
                _final10_reconcile_into_internal(self)
            except Exception:
                pass

        Trader.__init__ = _init_final10

# ======================================================================
# END FINAL 10/10 PATCH
# ======================================================================



# ======================================================================
# ULTRA PATCH PACK v2 - recovery + realized pnl + strategy auto-weighting
# ======================================================================
ADVANCED_RUNTIME_FILE = data_path("advanced_runtime.json")
ADV_STRAT_WINDOW = int(os.getenv("ADV_STRAT_WINDOW", "24"))
ADV_STRAT_MIN_TRADES = int(os.getenv("ADV_STRAT_MIN_TRADES", "8"))
ADV_STRAT_DISABLE_BELOW = float(os.getenv("ADV_STRAT_DISABLE_BELOW", "0.40"))
ADV_STRAT_DISABLE_FOR_MIN = int(os.getenv("ADV_STRAT_DISABLE_FOR_MIN", "180"))
ADV_STRAT_MAX_UP = float(os.getenv("ADV_STRAT_MAX_UP", "1.35"))
ADV_STRAT_MIN_DOWN = float(os.getenv("ADV_STRAT_MIN_DOWN", "0.55"))
ADV_RECOVER_NOTIFY_SEC = int(os.getenv("ADV_RECOVER_NOTIFY_SEC", "120"))
ADV_REALIZED_RETRY = int(os.getenv("ADV_REALIZED_RETRY", "4"))
ADV_REALIZED_SLEEP = float(os.getenv("ADV_REALIZED_SLEEP", "0.8"))


def _adv_load_runtime():
    return safe_read_json(ADVANCED_RUNTIME_FILE, {"by_strategy": {}, "ts": int(time.time())}) or {"by_strategy": {}, "ts": int(time.time())}


def _adv_save_runtime(st):
    st["ts"] = int(time.time())
    atomic_write_json(ADVANCED_RUNTIME_FILE, st)


class _AdvancedStrategyTuner:
    def __init__(self):
        self.st = _adv_load_runtime()

    def _bucket(self, strategy: str):
        strategy = str(strategy or "unknown")
        return (self.st.setdefault("by_strategy", {})).setdefault(strategy, {
            "pnl_hist": [],
            "disabled_until": 0,
            "score_adj": 0,
            "usdt_mult": 1.0,
            "last_pnl": 0.0,
            "updated_at": int(time.time()),
        })

    def _recalc(self, strategy: str):
        b = self._bucket(strategy)
        hist = [float(x) for x in (b.get("pnl_hist") or [])[-ADV_STRAT_WINDOW:]]
        b["pnl_hist"] = hist
        wins = sum(1 for x in hist if x > 0)
        losses = sum(1 for x in hist if x <= 0)
        total = len(hist)
        wr = (wins / total) if total else 0.0
        avg = (sum(hist) / total) if total else 0.0
        avg_abs = (sum(abs(x) for x in hist) / total) if total else 0.0

        score_adj = 0
        usdt_mult = 1.0
        now = int(time.time())

        if total >= ADV_STRAT_MIN_TRADES:
            if wr < ADV_STRAT_DISABLE_BELOW and avg <= 0:
                b["disabled_until"] = max(int(b.get("disabled_until", 0) or 0), now + ADV_STRAT_DISABLE_FOR_MIN * 60)
            edge = 0.0
            if avg_abs > 0:
                edge = max(-1.0, min(1.0, avg / avg_abs))
            score_adj = int(round(-3 * edge))
            usdt_mult = 1.0 + (0.35 * edge)
            if wr >= 0.62 and avg > 0:
                score_adj -= 1
                usdt_mult += 0.10
            if wr <= 0.45 and avg < 0:
                score_adj += 2
                usdt_mult -= 0.15

        b["score_adj"] = int(max(-4, min(6, score_adj)))
        b["usdt_mult"] = float(max(ADV_STRAT_MIN_DOWN, min(ADV_STRAT_MAX_UP, usdt_mult)))
        b["wins"] = wins
        b["losses"] = losses
        b["trades"] = total
        b["winrate"] = round(wr * 100.0, 2)
        b["avg_pnl"] = round(avg, 4)
        b["updated_at"] = now
        return b

    def record(self, strategy: str, pnl_usdt: float):
        strategy = str(strategy or "unknown")
        b = self._bucket(strategy)
        hist = b.get("pnl_hist") or []
        hist.append(float(pnl_usdt))
        b["pnl_hist"] = hist[-ADV_STRAT_WINDOW:]
        b["last_pnl"] = float(pnl_usdt)
        self._recalc(strategy)
        _adv_save_runtime(self.st)

    def allow(self, strategy: str):
        strategy = str(strategy or "unknown")
        b = self._recalc(strategy)
        _adv_save_runtime(self.st)
        until = int(b.get("disabled_until", 0) or 0)
        if until > int(time.time()):
            return False, f"ADV_BLOCK: {strategy} disabled until {until}"
        return True, "ADV_OK"

    def score_adj(self, strategy: str) -> int:
        return int(self._recalc(strategy).get("score_adj", 0) or 0)

    def usdt_mult(self, strategy: str) -> float:
        return float(self._recalc(strategy).get("usdt_mult", 1.0) or 1.0)

    def summary(self):
        out = {}
        for k in sorted((self.st.get("by_strategy") or {}).keys()):
            b = self._recalc(k)
            out[k] = {
                "trades": int(b.get("trades", 0) or 0),
                "winrate": float(b.get("winrate", 0.0) or 0.0),
                "avg_pnl": float(b.get("avg_pnl", 0.0) or 0.0),
                "score_adj": int(b.get("score_adj", 0) or 0),
                "usdt_mult": float(b.get("usdt_mult", 1.0) or 1.0),
                "disabled_until": int(b.get("disabled_until", 0) or 0),
            }
        _adv_save_runtime(self.st)
        return out


def _adv_real_positions_map():
    try:
        plist = get_positions_all()
        out = {}
        for p in plist:
            size = float(p.get("size") or 0)
            if size <= 0:
                continue
            sym = (p.get("symbol") or "").upper()
            side = "LONG" if (p.get("side") == "Buy") else "SHORT"
            out[(sym, side)] = {
                "symbol": sym,
                "side": side,
                "size": size,
                "entry_price": float(p.get("avgPrice") or p.get("entryPrice") or 0),
            }
        return out
    except Exception:
        return {}


def _adv_sum_closed_pnl(symbol: str, entry_ts: float, retries: int = None):
    if DRY_RUN or (not USE_REALIZED_PNL):
        return None
    retries = int(retries or ADV_REALIZED_RETRY)
    entry_ms = int(float(entry_ts or 0) * 1000) if entry_ts else 0
    for attempt in range(max(1, retries)):
        try:
            end_ms = int(time.time() * 1000)
            lookback_ms = int(max(60, REALIZED_PNL_LOOKBACK_MIN) * 60 * 1000)
            start_ms = max(0, end_ms - lookback_ms)
            if entry_ms:
                start_ms = max(start_ms, entry_ms - 10 * 60 * 1000)
            params = {
                "category": CATEGORY,
                "symbol": symbol,
                "limit": "100",
                "startTime": str(start_ms),
                "endTime": str(end_ms),
            }
            j = http.request("GET", "/v5/position/closed-pnl", params, auth=True)
            lst = (j.get("result") or {}).get("list") or []
            total = 0.0
            matched = 0
            for r in lst:
                t_ms = 0
                for k in ("updatedTime", "createdTime", "closeTime", "execTime"):
                    v = r.get(k)
                    if v is None:
                        continue
                    try:
                        vv = float(v)
                        t_ms = int(vv if vv > 10_000_000_000 else vv * 1000)
                        break
                    except Exception:
                        continue
                if entry_ms and t_ms and t_ms < entry_ms:
                    continue
                try:
                    pnl = r.get("closedPnl")
                    if pnl is None:
                        pnl = r.get("pnl")
                    if pnl is None:
                        continue
                    total += float(pnl)
                    matched += 1
                except Exception:
                    continue
            if matched > 0:
                return float(total)
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(ADV_REALIZED_SLEEP)
    return None


def _adv_estimate_trade_pnl(pos: dict, exit_price: float):
    entry_price = float(pos.get("entry_price") or 0)
    side = str(pos.get("side") or "LONG")
    notional = float(pos.get("last_order_usdt") or 0) * float(pos.get("last_lev") or 0)
    return estimate_pnl_usdt(side, entry_price, float(exit_price or 0), notional)


def _adv_sync_runtime(self):
    try:
        atomic_write_json(
            data_path("runtime_state.json"),
            {
                "consec_losses": int(getattr(self, "consec_losses", 0) or 0),
                "daily_pnl": float(getattr(self, "daily_pnl", 0.0) or 0.0),
                "trading_enabled": bool(getattr(self, "trading_enabled", True)),
                "mode": str(getattr(self, "mode", MODE_DEFAULT)),
                "allow_long": bool(getattr(self, "allow_long", ALLOW_LONG_DEFAULT)),
                "allow_short": bool(getattr(self, "allow_short", ALLOW_SHORT_DEFAULT)),
                "auto_symbol": bool(getattr(self, "auto_symbol", AUTO_SYMBOL_DEFAULT)),
                "fixed_symbol": str(getattr(self, "fixed_symbol", FIXED_SYMBOL_DEFAULT)),
                "auto_discovery": bool(getattr(self, "auto_discovery", AUTO_DISCOVERY_DEFAULT)),
                "diversify": bool(getattr(self, "diversify", DIVERSIFY_DEFAULT)),
                "max_positions": int(getattr(self, "max_positions", MAX_POSITIONS_DEFAULT) or 1),
                "symbols": list(getattr(self, "symbols", SYMBOLS_ENV) or []),
                "tune": dict(getattr(self, "tune", {}) or {}),
                "ai_growth": bool(getattr(self, "ai_growth", AI_GROWTH_DEFAULT)),
                "avoid_low_rsi": bool(self.state.get("avoid_low_rsi", False)),
                "ts": int(time.time()),
            },
        )
        atomic_write_json(data_path("daily_pnl.json"), {"pnl": float(getattr(self, "daily_pnl", 0.0) or 0.0), "ts": int(time.time())})
    except Exception:
        pass


def _adv_import_real_position(self, rp: dict):
    symbol = rp["symbol"]
    side = rp["side"]
    try:
        price = get_price(symbol)
    except Exception:
        price = float(rp.get("entry_price") or 0)
    mp = self._mp()
    ok, reason, score, sl, tp, a = compute_signal_and_exits(
        symbol, side, price, mp, avoid_low_rsi=bool(self.state.get("avoid_low_rsi", False))
    )
    if sl is None or tp is None:
        a = a if (a is not None and a > 0) else price * 0.01
        stop_dist = a * mp["stop_atr"]
        tp_dist = stop_dist * mp["tp_r"]
        if side == "LONG":
            sl = price - stop_dist
            tp = price + tp_dist
        else:
            sl = price + stop_dist
            tp = price - tp_dist
    tp1_price = None
    if PARTIAL_TP_ON:
        if side == "LONG":
            tp1_price = price + (tp - price) * TP1_FRACTION
        else:
            tp1_price = price - (price - tp) * TP1_FRACTION
    self.positions.append({
        "symbol": symbol,
        "side": side,
        "entry_price": float(rp.get("entry_price") or price or 0),
        "entry_ts": time.time(),
        "stop_price": sl,
        "tp_price": tp,
        "trail_price": None,
        "tp1_price": tp1_price,
        "tp1_done": False,
        "last_order_usdt": None,
        "last_lev": None,
        "strategy": "recovered",
        "entry_score": 0.0,
        "imported_from_exchange": True,
        "last_known_qty": float(rp.get("size") or 0.0),
        "realized_pnl_partial": 0.0,
        "realized_qty": 0.0,
        "closed_pnl_booked": 0.0,
        "entry_price_real": float(rp.get("entry_price") or price or 0),
    })


def _adv_reconcile(self, notify: bool = False):
    if DRY_RUN:
        return
    real_map = _adv_real_positions_map()
    self.state["real_positions"] = list(real_map.values())[:8]
    internal_map = {((p.get("symbol") or "").upper(), p.get("side")): p for p in (self.positions or [])}
    imported = 0
    removed = 0

    for key, rp in real_map.items():
        pos = internal_map.get(key)
        if pos is None:
            _adv_import_real_position(self, rp)
            imported += 1
            continue
        pos["last_known_qty"] = float(rp.get("size") or 0.0)
        if float(rp.get("entry_price") or 0) > 0:
            pos["entry_price_real"] = float(rp.get("entry_price") or 0)

    for idx in range(len(self.positions) - 1, -1, -1):
        pos = self.positions[idx]
        key = ((pos.get("symbol") or "").upper(), pos.get("side"))
        if key in real_map:
            continue
        # internal ghost: exchange says flat
        pos["ghost_closed"] = True
        pos["ghost_closed_ts"] = int(time.time())
        if pos.get("imported_from_exchange") or pos.get("tp1_done") or pos.get("last_known_qty"):
            # cleanup aggressively for stale/recovered positions
            self.positions.pop(idx)
            removed += 1

    if notify:
        if imported:
            self.notify_throttled(f"🔄 실포지션 {imported}개 자동 복구 완료", ADV_RECOVER_NOTIFY_SEC)
        if removed:
            self.notify_throttled(f"🧹 내부 ghost 포지션 {removed}개 정리 완료", ADV_RECOVER_NOTIFY_SEC)


if FINAL10_ON:
    _ultra_prev_init = Trader.__init__
    def _ultra_init(self, state=None):
        _ultra_prev_init(self, state)
        self._adv_tuner = _AdvancedStrategyTuner()
        self._last_skip_reason = ""
        self._last_runtime_save_ts = 0
        self._last_recover_ts = 0
        self._doctor_last = {}
    Trader.__init__ = _ultra_init

    _ultra_prev_score = Trader._score_symbol
    def _ultra_score_symbol(self, symbol: str, price: float):
        info = _ultra_prev_score(self, symbol, price)
        if not isinstance(info, dict):
            return info
        if not info.get("ok"):
            self._last_skip_reason = str(info.get("reason") or "not_ok")
            return info
        strategy = str(info.get("strategy") or "unknown")
        try:
            ok_adv, msg_adv = self._adv_tuner.allow(strategy)
            if not ok_adv:
                self._last_skip_reason = msg_adv
                return {"ok": False, "reason": msg_adv, "strategy": strategy}
            adj = int(self._adv_tuner.score_adj(strategy))
            mult = float(self._adv_tuner.usdt_mult(strategy))
            info["score_raw"] = float(info.get("score", 0) or 0)
            info["score"] = float(info.get("score", 0) or 0) + adj
            info["strategy_weight_mult"] = mult
            if adj:
                info["reason"] = f"{info.get('reason','')}\nADV_STRAT score_adj={adj} mult={mult:.2f}".strip()
        except Exception as e:
            info["reason"] = f"{info.get('reason','')}\nADV_STRAT_ERR={e}".strip()
        return info
    Trader._score_symbol = _ultra_score_symbol

    _ultra_prev_enter = Trader._enter
    def _ultra_enter(self, symbol: str, side: str, price: float, reason: str, sl: float, tp: float, strategy: str = "", score: float = 0.0, atr: float = 0.0, *args, **kwargs):
        strategy = str(strategy or "unknown")
        mult = 1.0
        try:
            mult = float(self._adv_tuner.usdt_mult(strategy))
        except Exception:
            mult = 1.0
        mode_tune = self.tune.get(self.mode, {}).copy()
        orig_usdt = mode_tune.get("order_usdt")
        if orig_usdt is not None:
            self.tune[self.mode]["order_usdt"] = float(orig_usdt) * float(mult)
        try:
            rv = _ultra_prev_enter(self, symbol, side, price, reason, sl, tp, strategy, score, atr, *args, **kwargs)
            if self.positions:
                pos = self.positions[-1]
                if (pos.get("symbol") == symbol) and (pos.get("side") == side):
                    pos.setdefault("strategy", strategy)
                    pos["strategy_weight_mult"] = float(mult)
                    pos["realized_pnl_partial"] = float(pos.get("realized_pnl_partial") or 0.0)
                    pos["realized_qty"] = float(pos.get("realized_qty") or 0.0)
                    pos["closed_pnl_booked"] = float(pos.get("closed_pnl_booked") or 0.0)
                    pos["last_known_qty"] = float(pos.get("last_known_qty") or 0.0)
            _adv_sync_runtime(self)
            return rv
        finally:
            if orig_usdt is not None:
                self.tune[self.mode]["order_usdt"] = orig_usdt
    Trader._enter = _ultra_enter

    _ultra_prev_manage = Trader._manage_one
    def _ultra_manage_one(self, idx: int):
        before_tp1 = False
        before_qty = None
        pos = None
        if 0 <= idx < len(self.positions):
            pos = self.positions[idx]
            before_tp1 = bool(pos.get("tp1_done"))
            before_qty = float(pos.get("last_known_qty") or 0.0)
        rv = _ultra_prev_manage(self, idx)
        if 0 <= idx < len(self.positions):
            pos = self.positions[idx]
            if (not before_tp1) and bool(pos.get("tp1_done")):
                try:
                    total_real = _adv_sum_closed_pnl(pos["symbol"], float(pos.get("entry_ts") or 0), retries=2)
                    if total_real is not None:
                        pos["realized_pnl_partial"] = float(total_real)
                        pos["closed_pnl_booked"] = float(total_real)
                    if not DRY_RUN:
                        real_map = _adv_real_positions_map()
                        rp = real_map.get(((pos.get("symbol") or "").upper(), pos.get("side")))
                        if rp:
                            now_qty = float(rp.get("size") or 0.0)
                            pos["realized_qty"] = max(0.0, float(before_qty or pos.get("last_known_qty") or 0.0) - now_qty)
                            pos["last_known_qty"] = now_qty
                except Exception:
                    pass
                _adv_sync_runtime(self)
        return rv
    Trader._manage_one = _ultra_manage_one

    def _ultra_exit_position(self, idx: int, why: str, force=False):
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

        realized_before = None
        try:
            realized_before = _adv_sum_closed_pnl(symbol, float(pos.get("entry_ts") or 0), retries=1)
        except Exception:
            realized_before = None

        if not DRY_RUN:
            try:
                qty = get_position_size(symbol)
                if qty > 0:
                    self._close_qty(symbol, side, qty)
            except Exception as e:
                self.err_throttled(f"❌ 실청산 실패: {symbol} {e}")

        entry_price = float(pos.get("entry_price") or 0)
        pnl_real_total = _adv_sum_closed_pnl(symbol, float(pos.get("entry_ts") or 0), retries=ADV_REALIZED_RETRY)
        pnl_est = _adv_estimate_trade_pnl(pos, price)
        if pnl_real_total is not None:
            pnl_final = float(pnl_real_total)
            self.state["last_pnl_source"] = "REALIZED_SUM"
        elif realized_before is not None:
            pnl_final = float(realized_before)
            self.state["last_pnl_source"] = "REALIZED_BEFORE"
        else:
            pnl_final = float(pnl_est)
            self.state["last_pnl_source"] = "ESTIMATED"

        self.day_profit += pnl_final
        _ai_record_pnl(pnl_final)

        strategy = str(pos.get("strategy") or "")
        try:
            if USE_STRATEGY_PERF and self._perf is not None:
                self._perf.record_trade(strategy, float(pnl_final))
        except Exception:
            pass
        try:
            if (not USE_STRATEGY_PERF or self._perf is None) and self._sg is not None:
                self._sg.record(strategy, float(pnl_final))
        except Exception:
            pass
        try:
            self._adv_tuner.record(strategy, float(pnl_final))
        except Exception:
            pass

        self._trade_count_total += 1
        self._recent_results.append(pnl_final)
        if len(self._recent_results) > 30:
            self._recent_results = self._recent_results[-30:]

        if pnl_final >= 0:
            self.win += 1
            self.consec_losses = 0
        else:
            self.loss += 1
            self.consec_losses += 1

        self.notify(f"✅ EXIT {symbol} {side} ({why}) price={price:.6f} pnl≈{pnl_final:.2f} day≈{self.day_profit:.2f} (W{self.win}/L{self.loss})")
        _log_event(
            "exit",
            symbol=symbol,
            side=side,
            why=why,
            exit_price=price,
            entry_price=entry_price,
            pnl=float(pnl_final),
            pnl_source=self.state.get("last_pnl_source"),
            strategy=strategy,
            realized_partial=float(pos.get("realized_pnl_partial") or 0.0),
        )
        self.positions.pop(idx)
        _adv_sync_runtime(self)
        self._maybe_ai_grow()
    Trader._exit_position = _ultra_exit_position

    _ultra_prev_handle = Trader.handle_command
    def _ultra_handle_command(self, text: str):
        cmd = (text or "").strip()
        low = cmd.lower()
        if low in ("/sync", "/recover"):
            _adv_reconcile(self, notify=True)
            _adv_sync_runtime(self)
            self.notify("✅ recover/sync 완료")
            return
        if low == "/perf":
            perf_lines = ["📊 Strategy Perf"]
            try:
                summary = self._adv_tuner.summary()
                if not summary:
                    perf_lines.append("- 데이터 없음")
                else:
                    for k, v in list(summary.items())[:12]:
                        perf_lines.append(f"- {k}: WR {v['winrate']:.1f}% | n={v['trades']} | avg={v['avg_pnl']:.2f} | scoreAdj={v['score_adj']} | x{v['usdt_mult']:.2f}")
            except Exception as e:
                perf_lines.append(f"- error: {e}")
            self.notify("\n".join(perf_lines))
            return
        if low == "/help":
            try:
                self.notify(self.help_text())
            except Exception as e:
                self.notify(f"❌ help 오류: {e}")
            return
        if low == "/why":
            try:
                self.notify(self.why_text())
            except Exception as e:
                self.notify(f"❌ why 오류: {e}")
            return
        if low == "/weights":
            lines = ["⚖️ Strategy Weights"]
            try:
                summary = self._adv_tuner.summary()
                if summary:
                    for k, v in list(summary.items())[:12]:
                        lines.append(f"- {k}: order x{v['usdt_mult']:.2f}, scoreAdj {v['score_adj']}")
                else:
                    lines.append("- 데이터 없음")
            except Exception as e:
                lines.append(f"- error: {e}")
            self.notify("\n".join(lines))
            return
        if low == "/doctor":
            doc = {
                "positions_internal": len(self.positions or []),
                "positions_real": len(self.state.get("real_positions") or []),
                "cb_err_count": int(getattr(self, "_cb_err_count", 0) or 0),
                "cooldown_left": max(0, int(float(getattr(self, "_cooldown_until", 0) or 0) - time.time())),
                "mode": self.mode,
                "trading_enabled": bool(self.trading_enabled),
                "last_skip_reason": str(getattr(self, "_last_skip_reason", "") or ""),
            }
            self._doctor_last = doc
            self.notify("🩺 doctor\n" + "\n".join(f"- {k}: {v}" for k, v in doc.items()))
            return
        return _ultra_prev_handle(self, text)
    Trader.handle_command = _ultra_handle_command

    _ultra_prev_status = Trader.status_text
    def _ultra_status_text(self):
        base = _ultra_prev_status(self)
        lines = [base]
        cooldown_left = max(0, int(float(getattr(self, "_cooldown_until", 0) or 0) - time.time()))
        lines.append(f"🛡️ cb_err_count={int(getattr(self, '_cb_err_count', 0) or 0)} | cooldown_left={cooldown_left}s")
        lines.append(f"💾 runtime_saved_ts={int((safe_read_json(data_path('runtime_state.json'), {}) or {}).get('ts', 0) or 0)} | last_skip={getattr(self, '_last_skip_reason', '')}")
        try:
            summary = self._adv_tuner.summary()
            if summary:
                top = []
                for k, v in list(summary.items())[:4]:
                    top.append(f"{k}:WR{v['winrate']:.0f}% n{v['trades']} x{v['usdt_mult']:.2f}")
                lines.append("🎯 strat=" + " | ".join(top))
        except Exception:
            pass
        if isinstance(getattr(self, '_inst_weights', None), dict) and self._inst_weights:
            try:
                topw = sorted(self._inst_weights.items(), key=lambda x: x[1], reverse=True)[:4]
                lines.append("🏦 inst=" + " | ".join(f"{k}:{v:.2f}" for k, v in topw))
            except Exception:
                pass
        if self.state.get("real_positions"):
            try:
                lines.append("🔁 real=" + ", ".join(f"{p['symbol']}:{p['side']}:{float(p['size']):g}" for p in (self.state.get("real_positions") or [])[:4]))
            except Exception:
                pass
        return "\n".join(lines)
    Trader.status_text = _ultra_status_text

    _ultra_prev_tick = Trader.tick
    def _ultra_tick(self):
        try:
            _adv_reconcile(self, notify=False)
        except Exception:
            pass
        rv = _ultra_prev_tick(self)
        try:
            _adv_sync_runtime(self)
        except Exception:
            pass
        return rv
    Trader.tick = _ultra_tick
# ======================================================================
# END ULTRA PATCH PACK v2
# ======================================================================
# =====================================================================
# HARDENING PATCH - append to the VERY END of trader.py
# 목적:
# - 횡보장 진입 차단
# - ADX / ATR / EMA gap 필터 추가
# - enter_score 하한 강화
# - avoid_low_rsi 기본 ON
# =====================================================================

HARDENING_ON = str(os.getenv("HARDENING_ON", "true")).lower() in ("1","true","yes","y","on")
HARD_MIN_ADX = float(os.getenv("HARD_MIN_ADX", "18"))
HARD_MIN_ATR_PCT = float(os.getenv("HARD_MIN_ATR_PCT", "0.0035"))
HARD_MAX_ATR_PCT = float(os.getenv("HARD_MAX_ATR_PCT", "0.045"))
HARD_MIN_EMA_GAP_PCT = float(os.getenv("HARD_MIN_EMA_GAP_PCT", "0.0018"))
HARD_ENTER_SCORE_SAFE = int(os.getenv("HARD_ENTER_SCORE_SAFE", "70"))
HARD_ENTER_SCORE_AGGRO = int(os.getenv("HARD_ENTER_SCORE_AGGRO", "65"))


def _hard_adx(highs, lows, closes, period=14):
    if len(closes) < period + 2:
        return 0.0
    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, len(closes)):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)
    if len(tr_list) < period:
        return 0.0
    atr_sm = sum(tr_list[:period]) / period
    plus_sm = sum(plus_dm[:period]) / period
    minus_sm = sum(minus_dm[:period]) / period
    dx_values = []
    for i in range(period, len(tr_list)):
        atr_sm = ((atr_sm * (period - 1)) + tr_list[i]) / period
        plus_sm = ((plus_sm * (period - 1)) + plus_dm[i]) / period
        minus_sm = ((minus_sm * (period - 1)) + minus_dm[i]) / period
        if atr_sm <= 0:
            continue
        plus_di = 100.0 * (plus_sm / atr_sm)
        minus_di = 100.0 * (minus_sm / atr_sm)
        denom = plus_di + minus_di
        dx = 0.0 if denom <= 0 else 100.0 * abs(plus_di - minus_di) / denom
        dx_values.append(dx)
    if not dx_values:
        return 0.0
    tail = dx_values[-period:] if len(dx_values) >= period else dx_values
    return sum(tail) / len(tail)


if HARDENING_ON:
    _orig_compute_signal_and_exits_hard = compute_signal_and_exits

    def compute_signal_and_exits(symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False):
        avoid_low_rsi = True if avoid_low_rsi is False else avoid_low_rsi
        ok, reason, score, sl, tp, a = _orig_compute_signal_and_exits_hard(symbol, side, price, mp, avoid_low_rsi=avoid_low_rsi)
        if not ok:
            return ok, reason, score, sl, tp, a

        try:
            kl = get_klines(symbol, ENTRY_INTERVAL, max(KLINE_LIMIT, EMA_SLOW * 3 + 60))
            kl = list(reversed(kl))
            closes = [float(x[4]) for x in kl]
            highs = [float(x[2]) for x in kl]
            lows = [float(x[3]) for x in kl]
            adx_v = _hard_adx(highs, lows, closes, ATR_PERIOD)
            atr_pct = (float(a) / float(price)) if price else 0.0
            ef = ema(closes[-EMA_FAST * 3:], EMA_FAST)
            es = ema(closes[-EMA_SLOW * 3:], EMA_SLOW)
            ema_gap_pct = abs(ef - es) / max(price, 1e-9)
            self_need = max(int(mp.get("enter_score", 0)), HARD_ENTER_SCORE_AGGRO if str(globals().get("MODE_DEFAULT", "SAFE")).upper() == "AGGRO" else HARD_ENTER_SCORE_SAFE)

            if score < self_need:
                return False, f"HARD_SCORE_BLOCK score={score} need={self_need}", score, sl, tp, a
            if adx_v < HARD_MIN_ADX:
                return False, f"HARD_ADX_BLOCK adx={adx_v:.2f} < {HARD_MIN_ADX}", score, sl, tp, a
            if atr_pct < HARD_MIN_ATR_PCT:
                return False, f"HARD_ATR_LOW atr_pct={atr_pct:.4f} < {HARD_MIN_ATR_PCT:.4f}", score, sl, tp, a
            if atr_pct > HARD_MAX_ATR_PCT:
                return False, f"HARD_ATR_HIGH atr_pct={atr_pct:.4f} > {HARD_MAX_ATR_PCT:.4f}", score, sl, tp, a
            if ema_gap_pct < HARD_MIN_EMA_GAP_PCT:
                return False, f"HARD_EMA_GAP_BLOCK gap={ema_gap_pct:.4f} < {HARD_MIN_EMA_GAP_PCT:.4f}", score, sl, tp, a
            return True, reason + f" | ADX={adx_v:.2f} ATR%={atr_pct:.4f} GAP%={ema_gap_pct:.4f}", score, sl, tp, a
        except Exception as e:
            return False, f"HARD_FILTER_ERR {e}", score, sl, tp, a

    _orig_apply_strategy_to_mp_hard = apply_strategy_to_mp

    def apply_strategy_to_mp(symbol: str, mp: dict):
        ok, msg, strategy = _orig_apply_strategy_to_mp_hard(symbol, mp)
        if not ok:
            return ok, msg, strategy
        if strategy == "mean_reversion":
            return False, "STRATEGY_BLOCK: mean_reversion disabled in hardening", strategy
        if strategy in ("trend_long", "trend_short"):
            mp["enter_score"] = max(int(mp.get("enter_score", 0)), HARD_ENTER_SCORE_SAFE)
            mp["tp_r"] = max(float(mp.get("tp_r", 1.0)), 1.8)
            mp["stop_atr"] = max(float(mp.get("stop_atr", 1.0)), 1.6)
        return True, msg, strategy

    _orig_init_hard = getattr(Trader, "__init__")

    def _init_hard(self, state=None):
        _orig_init_hard(self, state)
        self.state["avoid_low_rsi"] = True
        try:
            if "SAFE" in self.tune:
                self.tune["SAFE"]["enter_score"] = max(int(self.tune["SAFE"].get("enter_score", 0)), HARD_ENTER_SCORE_SAFE)
            if "AGGRO" in self.tune:
                self.tune["AGGRO"]["enter_score"] = max(int(self.tune["AGGRO"].get("enter_score", 0)), HARD_ENTER_SCORE_AGGRO)
        except Exception:
            pass

    Trader.__init__ = _init_hard
# =========================================================
# ENTRY HARDENING PATCH v1
# 목적:
# - 횡보장/약추세/저변동성 구간 진입 차단
# - 기존 구조 최대한 안 건드리고, 진입 전에 필터만 추가
# =========================================================

try:
    _entry_hard_prev_compute = getattr(Trader, "compute_signal_and_exits", None)
    if _entry_hard_prev_compute is None:
        _entry_hard_prev_compute = globals().get("compute_signal_and_exits")

    def _safe_float(v, default=0.0):
        try:
            return float(v)
        except Exception:
            return default

    def _ema_series(values, period: int):
        if not values:
            return []
        k = 2.0 / (period + 1.0)
        out = []
        prev = float(values[0])
        for x in values:
            x = float(x)
            prev = x * k + prev * (1.0 - k)
            out.append(prev)
        return out

    def _atr_pct_from_candles(candles, period: int = 14):
        try:
            if not candles or len(candles) < period + 2:
                return 0.0

            trs = []
            prev_close = _safe_float(candles[0].get("close"))
            for c in candles[1:]:
                h = _safe_float(c.get("high"))
                l = _safe_float(c.get("low"))
                cl = _safe_float(c.get("close"))
                tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
                trs.append(tr)
                prev_close = cl

            if len(trs) < period:
                return 0.0

            atr = sum(trs[-period:]) / period
            last_close = _safe_float(candles[-1].get("close"), 1.0)
            if last_close <= 0:
                return 0.0
            return atr / last_close
        except Exception:
            return 0.0

    def _trend_strength_bps_from_closes(closes, fast=20, slow=50):
        try:
            if len(closes) < slow + 5:
                return 0.0
            ef = _ema_series(closes, fast)
            es = _ema_series(closes, slow)
            px = float(closes[-1]) if float(closes[-1]) != 0 else 1.0
            gap = abs(ef[-1] - es[-1]) / px
            return gap * 10000.0
        except Exception:
            return 0.0

    def _rsi(values, period=14):
        try:
            if len(values) < period + 1:
                return 50.0
            gains, losses = [], []
            for i in range(1, len(values)):
                diff = float(values[i]) - float(values[i - 1])
                gains.append(max(diff, 0.0))
                losses.append(max(-diff, 0.0))
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return 100.0 - (100.0 / (1.0 + rs))
        except Exception:
            return 50.0

    def _entry_hardening_gate(self, symbol: str, side_hint: str = ""):
        """
        return: (ok: bool, why: str)
        """
        try:
            # 환경변수 기본값
            hard_on = str(os.getenv("HARDENING_ON", "true")).lower() in ("1", "true", "yes", "y", "on")
            if not hard_on:
                return True, ""

            min_atr_pct = _safe_float(os.getenv("HARD_MIN_ATR_PCT", "0.0045"))
            max_atr_pct = _safe_float(os.getenv("HARD_MAX_ATR_PCT", "0.03"))
            min_ema_gap_pct = _safe_float(os.getenv("HARD_MIN_EMA_GAP_PCT", "0.0025"))
            min_trend_bps = _safe_float(os.getenv("REGIME_MIN_SLOPE_BPS", "8"))
            low_rsi_block = str(os.getenv("AVOID_LOW_RSI", "true")).lower() in ("1", "true", "yes", "y", "on")

            # 기존 프로젝트에서 이미 쓰는 캔들 조회 함수 우선 사용
            candles = None
            for fn_name in ("get_klines", "get_candles", "fetch_klines", "fetch_candles"):
                fn = globals().get(fn_name)
                if callable(fn):
                    try:
                        # 흔한 시그니처 대응
                        try:
                            candles = fn(symbol, "15", 120)
                        except Exception:
                            try:
                                candles = fn(symbol, 120)
                            except Exception:
                                candles = fn(symbol)
                        if candles:
                            break
                    except Exception:
                        pass

            if not candles or len(candles) < 60:
                return False, "hardening:no_candles"

            closes = [_safe_float(c.get("close")) for c in candles if c]
            if len(closes) < 60:
                return False, "hardening:too_short"

            atr_pct = _atr_pct_from_candles(candles, 14)
            trend_bps = _trend_strength_bps_from_closes(closes, 20, 50)

            ef = _ema_series(closes, 20)
            es = _ema_series(closes, 50)
            price = closes[-1] if closes[-1] != 0 else 1.0
            ema_gap_pct = abs(ef[-1] - es[-1]) / price
            rsi14 = _rsi(closes, 14)

            # 1) 변동성 너무 낮음 = 횡보장 가능성 큼
            if atr_pct < min_atr_pct:
                return False, f"hardening:atr_low({atr_pct:.4f})"

            # 2) 변동성 너무 높음 = 과열/잡음
            if max_atr_pct > 0 and atr_pct > max_atr_pct:
                return False, f"hardening:atr_high({atr_pct:.4f})"

            # 3) EMA 간격이 너무 좁음 = 추세 약함
            if ema_gap_pct < min_ema_gap_pct:
                return False, f"hardening:ema_gap({ema_gap_pct:.4f})"

            # 4) 추세 강도 부족
            if trend_bps < min_trend_bps:
                return False, f"hardening:trend_bps({trend_bps:.2f})"

            # 5) RSI 역행/약세 차단
            side_u = str(side_hint or "").upper()
            if low_rsi_block:
                if side_u in ("BUY", "LONG") and rsi14 < 52:
                    return False, f"hardening:rsi_long({rsi14:.1f})"
                if side_u in ("SELL", "SHORT") and rsi14 > 48:
                    return False, f"hardening:rsi_short({rsi14:.1f})"

            return True, ""
        except Exception as e:
            return False, f"hardening:error({e})"

    def _entry_hard_compute_signal_and_exits(self, *args, **kwargs):
        if _entry_hard_prev_compute is None:
            return {"signal": None, "why": "hardening:no_base_compute"}
        if getattr(_entry_hard_prev_compute, "__self__", None) is None and getattr(_entry_hard_prev_compute, "__name__", "") == "compute_signal_and_exits":
            out = _entry_hard_prev_compute(*args, **kwargs)
        else:
            out = _entry_hard_prev_compute(self, *args, **kwargs)

        try:
            # 기존 반환이 dict라고 가정
            if not isinstance(out, dict):
                return out

            signal = str(out.get("signal") or "").upper()
            symbol = str(out.get("symbol") or kwargs.get("symbol") or "")
            if signal not in ("BUY", "SELL", "LONG", "SHORT"):
                return out

            ok, why = _entry_hardening_gate(self, symbol, signal)
            if not ok:
                out["signal"] = None
                out["why"] = why
                try:
                    self.state["last_skip_reason"] = why
                except Exception:
                    pass
            return out
        except Exception:
            return out

    Trader.compute_signal_and_exits = _entry_hard_compute_signal_and_exits

except Exception as _entry_hard_patch_e:
    print(f"[ENTRY_HARDENING_PATCH] load failed: {_entry_hard_patch_e}")
# =========================================================
# ETH-ONLY HARDENING PATCH (append to END of trader.py)
# 목적:
# - ETHUSDT에만 보수형 하드닝 적용
# - SOL/BTC 등 다른 심볼은 기존 로직 유지
# - 백테스트에서 맞춘 방향(과진입 축소)을 실전에도 반영
# =========================================================

try:
    import os as _eth_hard_os

    def _eth_hard_bool(name: str, default: bool = False) -> bool:
        try:
            v = str(_eth_hard_os.getenv(name, str(default))).strip().lower()
            return v in ("1", "true", "yes", "y", "on")
        except Exception:
            return default

    def _eth_hard_float(name: str, default: float) -> float:
        try:
            return float(_eth_hard_os.getenv(name, str(default)))
        except Exception:
            return float(default)

    def _eth_hard_targets():
        raw = str(_eth_hard_os.getenv("HARD_TARGET_SYMBOLS", "ETHUSDT")).strip()
        if not raw:
            return {"ETHUSDT"}
        return {x.strip().upper() for x in raw.split(",") if x.strip()}

    _ETH_HARD_ON = _eth_hard_bool("HARDENING_ON", True)
    _ETH_HARD_TARGETS = _eth_hard_targets()

    # -----------------------------------------------------
    # 1) __init__ 보강: ETH-only 하드닝용 기본 상태 강제
    # -----------------------------------------------------
    _orig_init_eth_hard = getattr(Trader, "__init__", None)
    if callable(_orig_init_eth_hard):
        def _eth_hard_init(self, *args, **kwargs):
            _orig_init_eth_hard(self, *args, **kwargs)

            try:
                # low RSI 회피는 항상 켠다
                if isinstance(getattr(self, "state", None), dict):
                    self.state["avoid_low_rsi"] = True
            except Exception:
                pass

            try:
                # FIXED_SYMBOL이 ETHUSDT면 auto discovery 끔
                fixed = str(_eth_hard_os.getenv("FIXED_SYMBOL", "")).upper()
                if fixed in _ETH_HARD_TARGETS:
                    self.auto_discovery = False
            except Exception:
                pass

        Trader.__init__ = _eth_hard_init

    # -----------------------------------------------------
    # 2) _mp() 보강: ETH 대상일 때 보수값 강제
    # -----------------------------------------------------
    _orig_mp_eth_hard = getattr(Trader, "_mp", None)
    if callable(_orig_mp_eth_hard):
        def _eth_hard_mp(self, *args, **kwargs):
            mp = _orig_mp_eth_hard(self, *args, **kwargs)

            try:
                if not isinstance(mp, dict):
                    return mp

                fixed = str(_eth_hard_os.getenv("FIXED_SYMBOL", "")).upper()
                target_mode = fixed in _ETH_HARD_TARGETS

                # FIXED가 ETH가 아니면 기존 로직 유지
                if not target_mode:
                    return mp

                mode_u = str(getattr(self, "mode", "SAFE")).upper()

                # 백테스트에서 통과한 ETH 보수값
                mp["lev"] = 2
                mp["order_usdt"] = 5.0
                mp["enter_score"] = 78 if mode_u == "SAFE" else 72

                # 손절/익절 기본값
                mp["stop_atr"] = max(float(mp.get("stop_atr", 1.8) or 1.8), 1.8)
                mp["tp_r"] = max(float(mp.get("tp_r", 2.4) or 2.4), 2.4)
            except Exception:
                pass

            return mp

        Trader._mp = _eth_hard_mp

    # -----------------------------------------------------
    # 3) apply_strategy_to_mp 보강:
    #    ETH 대상일 때 mean_reversion 차단 + trend 계열만 강화
    # -----------------------------------------------------
    _orig_apply_strategy_to_mp_eth_hard = globals().get("apply_strategy_to_mp")
    if callable(_orig_apply_strategy_to_mp_eth_hard):
        def apply_strategy_to_mp(symbol: str, mp: dict, *args, **kwargs):
            ok, msg, strategy = _orig_apply_strategy_to_mp_eth_hard(symbol, mp, *args, **kwargs)
            if not ok:
                return ok, msg, strategy

            try:
                sym_u = str(symbol or "").upper()
                if not _ETH_HARD_ON or sym_u not in _ETH_HARD_TARGETS:
                    return ok, msg, strategy

                # ETH에선 mean reversion 계열 배제
                if strategy == "mean_reversion":
                    return False, "STRATEGY_BLOCKED_ETH_HARDENING", strategy

                # trend 계열만 보수 강화
                if strategy in ("trend_long", "trend_breakout", "trend_follow", "momentum_long"):
                    if isinstance(mp, dict):
                        mp["enter_score"] = max(int(mp.get("enter_score", 0) or 0), 78)
                        mp["tp_r"] = max(float(mp.get("tp_r", 0) or 0), 2.4)
                        mp["stop_atr"] = max(float(mp.get("stop_atr", 0) or 0), 1.8)

                return True, msg, strategy
            except Exception:
                return ok, msg, strategy

        globals()["apply_strategy_to_mp"] = apply_strategy_to_mp

    # -----------------------------------------------------
    # 4) low RSI, slope, target symbol 점검 헬퍼
    #    entry 직전 추가 차단용
    # -----------------------------------------------------
    _orig_enter_eth_hard = getattr(Trader, "_enter", None)
    if callable(_orig_enter_eth_hard):
        def _eth_hard_enter(self, symbol, *args, **kwargs):
            try:
                sym_u = str(symbol or "").upper()
                if _ETH_HARD_ON and sym_u in _ETH_HARD_TARGETS:
                    # low RSI 회피
                    try:
                        if isinstance(getattr(self, "state", None), dict):
                            self.state["avoid_low_rsi"] = True
                    except Exception:
                        pass

                    # slope 필터는 기존 코드가 이미 쓰는 REGIME_MIN_SLOPE_BPS를 존중
                    # 여기선 FIXED_SYMBOL이 ETH일 때만 entry 허용
                    fixed = str(_eth_hard_os.getenv("FIXED_SYMBOL", "")).upper()
                    if fixed and fixed != sym_u:
                        return False

                return _orig_enter_eth_hard(self, symbol, *args, **kwargs)
            except Exception:
                try:
                    return _orig_enter_eth_hard(self, symbol, *args, **kwargs)
                except Exception:
                    return False

        Trader._enter = _eth_hard_enter

except Exception as _eth_hard_patch_err:
    print(f"[ETH_ONLY_HARDENING_PATCH] load failed: {_eth_hard_patch_err}")
# =========================================================
# DEBUG PATCH: show current mp / skip reason / fixed symbol
# append to END of trader.py
# =========================================================

try:
    import os as _dbg_os
    import time as _dbg_time

    def _dbg_safe(v, d=None):
        try:
            return v if v is not None else d
        except Exception:
            return d

    def _dbg_env(name, default=""):
        try:
            return _dbg_os.getenv(name, default)
        except Exception:
            return default

    # -------------------------------
    # status_text 확장
    # -------------------------------
    _orig_status_text_dbg = getattr(Trader, "status_text", None)
    if callable(_orig_status_text_dbg):
        def _dbg_status_text(self, *args, **kwargs):
            base = _orig_status_text_dbg(self, *args, **kwargs)
            try:
                mp = {}
                try:
                    if hasattr(self, "_mp") and callable(self._mp):
                        mp = self._mp() or {}
                except Exception:
                    mp = {}

                fixed = str(_dbg_env("FIXED_SYMBOL", "")).upper() or "NONE"
                last_skip = _dbg_safe(getattr(self, "state", {}).get("last_skip_reason"), "NONE")
                last_event = _dbg_safe(getattr(self, "state", {}).get("last_event"), "NONE")
                last_sym = _dbg_safe(getattr(self, "state", {}).get("last_symbol"), fixed)
                auto_disc = _dbg_safe(getattr(self, "auto_discovery", None), False)

                extra = []
                extra.append(f"🧪 DBG fixed={fixed} auto={auto_disc} sym={last_sym}")
                extra.append(
                    f"🧮 DBG mp lev={_dbg_safe(mp.get('lev'), '?')} "
                    f"usdt={_dbg_safe(mp.get('order_usdt'), '?')} "
                    f"score>={_dbg_safe(mp.get('enter_score'), '?')} "
                    f"stop_atr={_dbg_safe(mp.get('stop_atr'), '?')} "
                    f"tp_r={_dbg_safe(mp.get('tp_r'), '?')}"
                )
                extra.append(f"🚫 DBG skip={last_skip}")
                extra.append(f"📌 DBG event={last_event}")

                return str(base) + "\n" + "\n".join(extra)
            except Exception:
                return base

        Trader.status_text = _dbg_status_text

    # -------------------------------
    # tick 확장: 30초마다 bot.log에 현재 상태 출력
    # -------------------------------
    _orig_tick_dbg = getattr(Trader, "tick", None)
    if callable(_orig_tick_dbg):
        def _dbg_tick(self, *args, **kwargs):
            rv = _orig_tick_dbg(self, *args, **kwargs)

            try:
                now = _dbg_time.time()
                last_ts = float(getattr(self, "_dbg_last_log_ts", 0.0) or 0.0)
                if now - last_ts >= 30:
                    setattr(self, "_dbg_last_log_ts", now)

                    mp = {}
                    try:
                        if hasattr(self, "_mp") and callable(self._mp):
                            mp = self._mp() or {}
                    except Exception:
                        mp = {}

                    fixed = str(_dbg_env("FIXED_SYMBOL", "")).upper() or "NONE"
                    last_skip = _dbg_safe(getattr(self, "state", {}).get("last_skip_reason"), "NONE")
                    last_event = _dbg_safe(getattr(self, "state", {}).get("last_event"), "NONE")
                    last_sym = _dbg_safe(getattr(self, "state", {}).get("last_symbol"), fixed)
                    auto_disc = _dbg_safe(getattr(self, "auto_discovery", None), False)

                    print(
                        "[DBG] "
                        f"fixed={fixed} auto={auto_disc} sym={last_sym} "
                        f"lev={_dbg_safe(mp.get('lev'), '?')} "
                        f"usdt={_dbg_safe(mp.get('order_usdt'), '?')} "
                        f"enter>={_dbg_safe(mp.get('enter_score'), '?')} "
                        f"stop_atr={_dbg_safe(mp.get('stop_atr'), '?')} "
                        f"tp_r={_dbg_safe(mp.get('tp_r'), '?')} "
                        f"skip={last_skip} event={last_event}",
                        flush=True,
                    )
            except Exception:
                pass

            return rv

        Trader.tick = _dbg_tick

except Exception as _dbg_patch_err:
    print(f"[DEBUG_PATCH] load failed: {_dbg_patch_err}")

# ===== STABILITY PATCH (safe append) =====
try:
    import time as _stb_time
    import os as _stb_os

    def _stb_set_skip(self, reason: str):
        try:
            if not hasattr(self, "state") or not isinstance(self.state, dict):
                self.state = {}
            self.state["last_skip_reason"] = str(reason)
        except Exception:
            pass

    def _stb_detect_regime_safe(self, symbol: str) -> str:
        try:
            try:
                from market_regime import detect_regime as _detect_regime_fn  # type: ignore
                r = _detect_regime_fn(symbol)
                return str(r).lower()
            except Exception:
                pass
            if hasattr(self, "market_regime") and callable(getattr(self, "market_regime")):
                r = self.market_regime(symbol)
                return str(r).lower()
        except Exception:
            pass
        return "unknown"

    def _stb_after_close_update_risk(self, pnl_pct: float):
        now = _stb_time.time()
        try:
            if not hasattr(self, "state") or not isinstance(self.state, dict):
                self.state = {}
        except Exception:
            self.state = {}
        if not hasattr(self, "consec_losses"):
            self.consec_losses = 0
        if not hasattr(self, "cooldown_until_ts"):
            self.cooldown_until_ts = 0
        if pnl_pct > 0:
            self.consec_losses = 0
            self.cooldown_until_ts = 0
            self.state["last_risk_event"] = "win_reset"
            return
        self.consec_losses += 1
        if self.consec_losses == 1:
            cd = 15 * 60
        elif self.consec_losses == 2:
            cd = 60 * 60
        else:
            cd = 4 * 60 * 60
        self.cooldown_until_ts = now + cd
        self.state["last_risk_event"] = f"loss_cooldown:{cd}s"

    Trader._set_skip = _stb_set_skip
    Trader._detect_regime_safe = _stb_detect_regime_safe
    Trader._after_close_update_risk = _stb_after_close_update_risk

    _orig_init_stb = getattr(Trader, "__init__", None)
    if callable(_orig_init_stb):
        def __init__stb(self, *args, **kwargs):
            _orig_init_stb(self, *args, **kwargs)
            try:
                if not hasattr(self, "state") or not isinstance(self.state, dict):
                    self.state = {}
            except Exception:
                self.state = {}
            if not hasattr(self, "consec_losses"):
                self.consec_losses = 0
            if not hasattr(self, "cooldown_until_ts"):
                self.cooldown_until_ts = 0
        Trader.__init__ = __init__stb

    _orig_tick_stb = getattr(Trader, "tick", None)
    if callable(_orig_tick_stb):
        def _tick_stb(self, *args, **kwargs):
            now_ts = _stb_time.time()
            try:
                if not hasattr(self, "state") or not isinstance(self.state, dict):
                    self.state = {}
            except Exception:
                self.state = {}
            if not hasattr(self, "consec_losses"):
                self.consec_losses = 0
            if not hasattr(self, "cooldown_until_ts"):
                self.cooldown_until_ts = 0
            if now_ts < getattr(self, "cooldown_until_ts", 0):
                left = int(self.cooldown_until_ts - now_ts)
                self._set_skip(f"loss_cooldown_left:{left}s")
                return None
            try:
                symbol = (
                    str(_stb_os.getenv("FIXED_SYMBOL", "")).strip().upper()
                    or str(getattr(self, "fixed_symbol", "")).strip().upper()
                    or str(getattr(self, "symbol", "")).strip().upper()
                )
                if symbol:
                    regime = self._detect_regime_safe(symbol)
                    self.state["last_regime"] = regime
                    if regime in ("range", "chop", "sideways"):
                        self._set_skip(f"regime_block:{regime}")
                        return None
            except Exception:
                pass
            return _orig_tick_stb(self, *args, **kwargs)
        Trader.tick = _tick_stb

    _orig_status_text_stb = getattr(Trader, "status_text", None)
    if callable(_orig_status_text_stb):
        def _status_text_stb(self, *args, **kwargs):
            base = _orig_status_text_stb(self, *args, **kwargs)
            try:
                last_skip = getattr(self, "state", {}).get("last_skip_reason", "-")
                last_regime = getattr(self, "state", {}).get("last_regime", "-")
                last_risk = getattr(self, "state", {}).get("last_risk_event", "-")
                cooldown_left = max(0, int(getattr(self, "cooldown_until_ts", 0) - _stb_time.time()))
                extra = [
                    f"⏭️ last_skip={last_skip}",
                    f"🌊 regime={last_regime}",
                    f"🧯 risk={last_risk}",
                    f"⏳ cooldown_left={cooldown_left}s",
                ]
                return str(base) + "\n" + "\n".join(extra)
            except Exception:
                return base
        Trader.status_text = _status_text_stb

except Exception as _stb_patch_err:
    print(f"[STABILITY_PATCH] load failed: {_stb_patch_err}")

# ===== LOG THROTTLE PATCH =====
import time
import logging

# Flask 로그 줄이기
try:
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
except Exception:
    pass

# 로그 쓰로틀
__last_log_ts = {}

def log_throttled(key, msg, sec=60):
    now = time.time()
    last = __last_log_ts.get(key, 0)
    if now - last >= sec:
        print(msg)
        __last_log_ts[key] = now

# 기존 print 대체용 (선택)
def safe_log(msg, key="default", sec=60):
    try:
        log_throttled(key, msg, sec)
    except Exception:
        print(msg)
# ===== DEBUG LOG LIMIT PATCH =====
import builtins
import time

__last_dbg_ts = 0

_orig_print = builtins.print

def _patched_print(*args, **kwargs):
    global __last_dbg_ts
    msg = " ".join(str(a) for a in args)

    # DBG 로그만 제한
    if "[DBG]" in msg:
        now = time.time()
        if now - __last_dbg_ts < 10:   # 10초에 한번만
            return
        __last_dbg_ts = now

    _orig_print(*args, **kwargs)

builtins.print = _patched_print


# === FINAL STABILITY PATCH (assistant) ===
try:
    _orig_reset_day_final = getattr(Trader, "_reset_day", None)
    if callable(_orig_reset_day_final):
        def _reset_day_final(self, *args, **kwargs):
            _orig_reset_day_final(self, *args, **kwargs)
            try:
                if not hasattr(self, "state") or not isinstance(self.state, dict):
                    self.state = {}
                self.state["day_key"] = getattr(self, "_day_key", self.state.get("day_key"))
                self.state["day_profit"] = float(getattr(self, "day_profit", 0.0) or 0.0)
                self.state["win"] = int(getattr(self, "win", 0) or 0)
                self.state["loss"] = int(getattr(self, "loss", 0) or 0)
                self.state["consec_losses"] = int(getattr(self, "consec_losses", 0) or 0)
            except Exception:
                pass
        Trader._reset_day = _reset_day_final
except Exception:
    pass

try:
    _orig_ensure_leverage_final = getattr(Trader, "_ensure_leverage", None)
    if callable(_orig_ensure_leverage_final):
        def _ensure_leverage_final(self, symbol: str):
            try:
                return _orig_ensure_leverage_final(self, symbol)
            except Exception as e:
                msg = str(e)
                if _is_bybit_lev_not_modified("110043" if "110043" in msg else "", msg):
                    try:
                        mp = self._mp() if hasattr(self, "_mp") else {"lev": "?"}
                        key = f"{symbol}:{getattr(self, 'mode', 'UNK')}:{int(mp.get('lev', 0) or 0)}"
                        if hasattr(self, "_lev_set_cache") and isinstance(self._lev_set_cache, dict):
                            self._lev_set_cache[key] = True
                    except Exception:
                        pass
                    return
                raise
        Trader._ensure_leverage = _ensure_leverage_final
except Exception:
    pass


# =====================================================================
# ULTIMATE FUSION PATCH (assistant)
# 목적:
# - 기존 파일 기반으로 안정화 + 런타임 복구 + 포지션/상태 동기화 + 진입 중복 방지 + 실전 persistence 강화
# - 기존 구조를 갈아엎지 않고, 맨 아래 monkey patch 방식으로만 덧댐
# =====================================================================
try:
    import time as _uf_time
    import json as _uf_json

    _UF_RUNTIME_FILE = data_path("runtime_state_ultimate.json")
    _UF_AUDIT_FILE = data_path("trade_audit.json")

    def _uf_now():
        return int(_uf_time.time())

    def _uf_read_json(path, default):
        try:
            return safe_read_json(path, default)
        except Exception:
            return default

    def _uf_write_json(path, payload):
        try:
            atomic_write_json(path, payload)
        except Exception:
            pass

    def _uf_runtime_payload(self):
        try:
            return {
                "ts": _uf_now(),
                "mode": str(getattr(self, "mode", MODE_DEFAULT) or MODE_DEFAULT),
                "trading_enabled": bool(getattr(self, "trading_enabled", True)),
                "allow_long": bool(getattr(self, "allow_long", ALLOW_LONG_DEFAULT)),
                "allow_short": bool(getattr(self, "allow_short", ALLOW_SHORT_DEFAULT)),
                "auto_symbol": bool(getattr(self, "auto_symbol", AUTO_SYMBOL_DEFAULT)),
                "fixed_symbol": str(getattr(self, "fixed_symbol", FIXED_SYMBOL_DEFAULT) or FIXED_SYMBOL_DEFAULT),
                "auto_discovery": bool(getattr(self, "auto_discovery", AUTO_DISCOVERY_DEFAULT)),
                "diversify": bool(getattr(self, "diversify", DIVERSIFY_DEFAULT)),
                "max_positions": int(getattr(self, "max_positions", MAX_POSITIONS_DEFAULT) or MAX_POSITIONS_DEFAULT),
                "symbols": list(getattr(self, "symbols", SYMBOLS_ENV) or []),
                "ai_growth": bool(getattr(self, "ai_growth", AI_GROWTH_DEFAULT)),
                "consec_losses": int(getattr(self, "consec_losses", 0) or 0),
                "cooldown_until": float(getattr(self, "_cooldown_until", 0) or 0),
                "day_key": str(getattr(self, "_day_key", "") or ""),
                "day_profit": float(getattr(self, "day_profit", 0.0) or 0.0),
                "win": int(getattr(self, "win", 0) or 0),
                "loss": int(getattr(self, "loss", 0) or 0),
                "day_entries": int(getattr(self, "_day_entries", 0) or 0),
                "tune": dict(getattr(self, "tune", {}) or {}),
                "avoid_low_rsi": bool((getattr(self, "state", {}) or {}).get("avoid_low_rsi", False)),
                "last_skip_reason": str((getattr(self, "state", {}) or {}).get("last_skip_reason", "") or ""),
                "last_event": str((getattr(self, "state", {}) or {}).get("last_event", "") or ""),
            }
        except Exception:
            return {"ts": _uf_now()}

    def _uf_sync_runtime(self, force=False):
        try:
            last = float(getattr(self, "_uf_last_runtime_sync", 0) or 0)
            if (not force) and (_uf_time.time() - last < 15):
                return
            self._uf_last_runtime_sync = _uf_time.time()
            _uf_write_json(_UF_RUNTIME_FILE, _uf_runtime_payload(self))
        except Exception:
            pass

    def _uf_append_audit(kind: str, payload: dict):
        try:
            arr = _uf_read_json(_UF_AUDIT_FILE, [])
            if not isinstance(arr, list):
                arr = []
            row = {"ts": _uf_now(), "kind": kind}
            try:
                row.update(dict(payload or {}))
            except Exception:
                pass
            arr.append(row)
            arr = arr[-200:]
            _uf_write_json(_UF_AUDIT_FILE, arr)
        except Exception:
            pass

    def _uf_real_symbol_side_set():
        out = set()
        try:
            for p in get_positions_all() or []:
                try:
                    size = float(p.get("size") or 0)
                    if size <= 0:
                        continue
                    sym = str(p.get("symbol") or "").upper()
                    side = "LONG" if str(p.get("side") or "") == "Buy" else "SHORT"
                    out.add((sym, side))
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _uf_real_symbol_any(symbol: str) -> bool:
        try:
            symbol = str(symbol or "").upper()
            for p in get_positions_all() or []:
                try:
                    if str(p.get("symbol") or "").upper() != symbol:
                        continue
                    if float(p.get("size") or 0) > 0:
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _uf_is_lev_not_modified(err) -> bool:
        msg = str(err or "")
        return ("110043" in msg) or ("leverage not modified" in msg.lower()) or ("set leverage not modified" in msg.lower())

    _uf_prev_init = getattr(Trader, "__init__", None)
    if callable(_uf_prev_init):
        def _uf_init(self, state=None):
            _uf_prev_init(self, state)
            rt = _uf_read_json(_UF_RUNTIME_FILE, {})
            try:
                if isinstance(rt, dict) and rt:
                    self.mode = str(rt.get("mode") or getattr(self, "mode", MODE_DEFAULT)).upper()
                    self.trading_enabled = bool(rt.get("trading_enabled", getattr(self, "trading_enabled", True)))
                    self.allow_long = bool(rt.get("allow_long", getattr(self, "allow_long", ALLOW_LONG_DEFAULT)))
                    self.allow_short = bool(rt.get("allow_short", getattr(self, "allow_short", ALLOW_SHORT_DEFAULT)))
                    self.auto_symbol = bool(rt.get("auto_symbol", getattr(self, "auto_symbol", AUTO_SYMBOL_DEFAULT)))
                    self.fixed_symbol = str(rt.get("fixed_symbol") or getattr(self, "fixed_symbol", FIXED_SYMBOL_DEFAULT)).upper()
                    self.auto_discovery = bool(rt.get("auto_discovery", getattr(self, "auto_discovery", AUTO_DISCOVERY_DEFAULT)))
                    self.diversify = bool(rt.get("diversify", getattr(self, "diversify", DIVERSIFY_DEFAULT)))
                    self.max_positions = int(rt.get("max_positions", getattr(self, "max_positions", MAX_POSITIONS_DEFAULT)) or 1)
                    syms = rt.get("symbols") or []
                    if isinstance(syms, list) and syms:
                        self.symbols = [str(x).upper() for x in syms if str(x).strip()]
                    tune = rt.get("tune") or {}
                    if isinstance(tune, dict) and tune:
                        self.tune.update(tune)
                    self.ai_growth = bool(rt.get("ai_growth", getattr(self, "ai_growth", AI_GROWTH_DEFAULT)))
                    self.consec_losses = int(rt.get("consec_losses", getattr(self, "consec_losses", 0)) or 0)
                    self._cooldown_until = float(rt.get("cooldown_until", getattr(self, "_cooldown_until", 0)) or 0)
                    self._day_key = rt.get("day_key") or getattr(self, "_day_key", None)
                    self.day_profit = float(rt.get("day_profit", getattr(self, "day_profit", 0.0)) or 0.0)
                    self.win = int(rt.get("win", getattr(self, "win", 0)) or 0)
                    self.loss = int(rt.get("loss", getattr(self, "loss", 0)) or 0)
                    self._day_entries = int(rt.get("day_entries", getattr(self, "_day_entries", 0)) or 0)
                    if not hasattr(self, "state") or not isinstance(self.state, dict):
                        self.state = {}
                    self.state["avoid_low_rsi"] = bool(rt.get("avoid_low_rsi", self.state.get("avoid_low_rsi", False)))
                    if rt.get("last_skip_reason"):
                        self.state["last_skip_reason"] = str(rt.get("last_skip_reason"))
                    if rt.get("last_event"):
                        self.state["last_event"] = str(rt.get("last_event"))
            except Exception:
                pass
            self._uf_last_runtime_sync = 0.0
            self._uf_last_reconcile = 0.0
            self._uf_last_auto_resume = 0.0
            self._uf_entry_dedupe = {}
            self._uf_last_health = 0.0
            _uf_sync_runtime(self, force=True)
        Trader.__init__ = _uf_init

    _uf_prev_reset_day = getattr(Trader, "_reset_day", None)
    if callable(_uf_prev_reset_day):
        def _uf_reset_day(self, *args, **kwargs):
            rv = _uf_prev_reset_day(self, *args, **kwargs)
            try:
                if not hasattr(self, "state") or not isinstance(self.state, dict):
                    self.state = {}
                self.state["day_key"] = getattr(self, "_day_key", self.state.get("day_key"))
                self.state["day_profit"] = float(getattr(self, "day_profit", 0.0) or 0.0)
                self.state["win"] = int(getattr(self, "win", 0) or 0)
                self.state["loss"] = int(getattr(self, "loss", 0) or 0)
                self.state["consec_losses"] = int(getattr(self, "consec_losses", 0) or 0)
            except Exception:
                pass
            _uf_sync_runtime(self, force=True)
            return rv
        Trader._reset_day = _uf_reset_day

    _uf_prev_ensure_leverage = getattr(Trader, "_ensure_leverage", None)
    if callable(_uf_prev_ensure_leverage):
        def _uf_ensure_leverage(self, symbol: str):
            try:
                return _uf_prev_ensure_leverage(self, symbol)
            except Exception as e:
                if _uf_is_lev_not_modified(e):
                    try:
                        mp = self._mp() if hasattr(self, "_mp") else {"lev": 0}
                        key = f"{str(symbol).upper()}:{getattr(self, 'mode', 'UNK')}:{int((mp or {}).get('lev', 0) or 0)}"
                        if not hasattr(self, "_lev_set_cache") or not isinstance(self._lev_set_cache, dict):
                            self._lev_set_cache = {}
                        self._lev_set_cache[key] = True
                        self.state["last_lev_result"] = "IGNORED_110043"
                    except Exception:
                        pass
                    return
                raise
        Trader._ensure_leverage = _uf_ensure_leverage

    _uf_prev_handle = getattr(Trader, "handle_command", None)
    if callable(_uf_prev_handle):
        def _uf_handle(self, text: str):
            cmd = str(text or "").strip().lower()
            if cmd in ("/runtime", "/state"):
                rt = _uf_runtime_payload(self)
                self.notify("📦 runtime\n" + _uf_json.dumps(rt, ensure_ascii=False, indent=2)[:3500])
                return
            if cmd in ("/audit", "/trades"):
                arr = _uf_read_json(_UF_AUDIT_FILE, [])
                if not arr:
                    self.notify("📒 audit 비어있음")
                else:
                    tail = arr[-8:]
                    lines = ["📒 recent audit"]
                    for r in tail:
                        lines.append(f"- {r.get('kind')} {r.get('symbol','')} {r.get('side','')} pnl={r.get('pnl','-')} why={r.get('why','-')}")
                    self.notify("\n".join(lines))
                return
            if cmd in ("/forcesync", "/fullsync"):
                try:
                    if ' _adv_reconcile' != '_adv_reconcile':
                        pass
                except Exception:
                    pass
                try:
                    if '_adv_reconcile' in globals() and callable(globals().get('_adv_reconcile')):
                        globals()['_adv_reconcile'](self, notify=True)
                    elif hasattr(self, '_reconcile_positions_safe') and callable(self._reconcile_positions_safe):
                        self._reconcile_positions_safe(notify=True)
                    self.notify("✅ force sync 완료")
                except Exception as e:
                    self.notify(f"❌ force sync 실패: {e}")
                _uf_sync_runtime(self, force=True)
                return
            rv = _uf_prev_handle(self, text)
            try:
                if cmd.startswith("/safe") or cmd.startswith("/aggro") or cmd.startswith("/attack"):
                    self._lev_set_cache = {}
                if cmd.startswith("/setsymbol"):
                    self.auto_symbol = False
                if cmd.startswith("/setlev"):
                    self._lev_set_cache = {}
            except Exception:
                pass
            _uf_sync_runtime(self, force=True)
            return rv
        Trader.handle_command = _uf_handle

    _uf_prev_enter = getattr(Trader, "_enter", None)
    if callable(_uf_prev_enter):
        def _uf_enter(self, symbol: str, side: str, price: float, reason: str, sl: float, tp: float, strategy: str = "", score: float = 0.0, atr: float = 0.0, *args, **kwargs):
            symbol = str(symbol or "").upper()
            side = str(side or "").upper()
            now = _uf_time.time()
            try:
                self._uf_entry_dedupe = getattr(self, "_uf_entry_dedupe", {}) or {}
            except Exception:
                self._uf_entry_dedupe = {}
            last = float(self._uf_entry_dedupe.get((symbol, side), 0) or 0)
            if now - last < 20:
                try:
                    self.state["last_skip_reason"] = f"UF_DEDUPE:{symbol}:{side}"
                except Exception:
                    pass
                return None
            # 내부 포지션 중복 차단
            try:
                for p in list(getattr(self, "positions", []) or []):
                    if str(p.get("symbol") or "").upper() == symbol:
                        try:
                            self.state["last_skip_reason"] = f"UF_HAVE_INTERNAL:{symbol}"
                        except Exception:
                            pass
                        return None
            except Exception:
                pass
            # 실거래 포지션 중복 차단
            if (not DRY_RUN) and _uf_real_symbol_any(symbol):
                try:
                    self.state["last_skip_reason"] = f"UF_HAVE_REAL:{symbol}"
                except Exception:
                    pass
                return None
            rv = _uf_prev_enter(self, symbol, side, price, reason, sl, tp, strategy, score, atr, *args, **kwargs)
            self._uf_entry_dedupe[(symbol, side)] = now
            try:
                self.state["last_event"] = f"UF_ENTER {symbol} {side}"
            except Exception:
                pass
            _uf_append_audit("enter", {
                "symbol": symbol,
                "side": side,
                "price": float(price or 0),
                "strategy": str(strategy or ""),
                "score": float(score or 0),
            })
            _uf_sync_runtime(self, force=True)
            return rv
        Trader._enter = _uf_enter

    _uf_prev_exit = getattr(Trader, "_exit_position", None)
    if callable(_uf_prev_exit):
        def _uf_exit(self, idx: int, why: str, force=False):
            pos = None
            try:
                if 0 <= idx < len(getattr(self, "positions", []) or []):
                    pos = dict((self.positions or [])[idx])
            except Exception:
                pos = None
            rv = _uf_prev_exit(self, idx, why, force=force)
            try:
                _uf_append_audit("exit", {
                    "symbol": (pos or {}).get("symbol", ""),
                    "side": (pos or {}).get("side", ""),
                    "why": str(why or ""),
                    "strategy": (pos or {}).get("strategy", ""),
                    "pnl": float((getattr(self, "day_profit", 0.0) or 0.0)),
                })
            except Exception:
                pass
            _uf_sync_runtime(self, force=True)
            return rv
        Trader._exit_position = _uf_exit

    _uf_prev_tick = getattr(Trader, "tick", None)
    if callable(_uf_prev_tick):
        def _uf_tick(self, *args, **kwargs):
            now = _uf_time.time()
            # 자동 재개: circuit breaker cooldown 끝나면 다시 ON
            try:
                if (not bool(getattr(self, "trading_enabled", True))) and float(getattr(self, "_cooldown_until", 0) or 0) > 0:
                    if now >= float(getattr(self, "_cooldown_until", 0) or 0):
                        if now - float(getattr(self, "_uf_last_auto_resume", 0) or 0) > 30:
                            self.trading_enabled = True
                            self._cb_err_count = 0
                            self._cooldown_until = 0
                            self._uf_last_auto_resume = now
                            try:
                                self.notify_throttled("✅ cooldown 종료 → 거래 자동 재개", 60)
                            except Exception:
                                pass
            except Exception:
                pass
            # 주기적 실포지션 동기화
            try:
                if (not DRY_RUN) and (now - float(getattr(self, "_uf_last_reconcile", 0) or 0) >= 45):
                    if '_adv_reconcile' in globals() and callable(globals().get('_adv_reconcile')):
                        globals()['_adv_reconcile'](self, notify=False)
                    self._uf_last_reconcile = now
            except Exception:
                pass
            rv = _uf_prev_tick(self, *args, **kwargs)
            # 내부 ghost 정리 보조
            try:
                if (not DRY_RUN) and len(getattr(self, "positions", []) or []) > 0:
                    real_keys = _uf_real_symbol_side_set()
                    removed = 0
                    for i in range(len(self.positions) - 1, -1, -1):
                        p = self.positions[i]
                        key = (str(p.get("symbol") or "").upper(), str(p.get("side") or "").upper())
                        if key not in real_keys and p.get("ghost_closed"):
                            self.positions.pop(i)
                            removed += 1
                    if removed:
                        self.state["last_event"] = f"UF_GHOST_CLEANUP:{removed}"
            except Exception:
                pass
            _uf_sync_runtime(self, force=False)
            return rv
        Trader.tick = _uf_tick
except Exception as e:
    print("ULTIMATE FUSION PATCH FAIL:", e)
# =========================
# 🔥 AI CAPITAL MANAGER
# =========================

try:
    if not hasattr(Trader, "_ai_capital"):
        Trader._ai_capital = {
            "size_mult": 1.0,
            "lev": 15,
            "win_streak": 0,
            "lose_streak": 0
        }

    def _ai_adjust(self, pnl):
        cap = self._ai_capital

        if pnl > 0:
            cap["win_streak"] += 1
            cap["lose_streak"] = 0
            cap["size_mult"] = min(cap["size_mult"] * 1.1, 2.0)
            cap["lev"] = min(cap["lev"] + 1, 30)

        else:
            cap["lose_streak"] += 1
            cap["win_streak"] = 0
            cap["size_mult"] = max(cap["size_mult"] * 0.8, 0.3)
            cap["lev"] = max(cap["lev"] - 2, 5)

    # ---------- entry hook ----------
    _orig_entry = getattr(Trader, "_entry", None)

    if callable(_orig_entry):
        def _patched_entry(self, symbol, *args, **kwargs):
            cap = self._ai_capital

            # 비중 조절
            if "qty" in kwargs:
                kwargs["qty"] *= cap["size_mult"]

            # 레버리지 적용
            if hasattr(self, "mode"):
                if not hasattr(self, "_mp"):
                    pass
                else:
                    try:
                        mp = self._mp()
                        mp["lev"] = cap["lev"]
                    except:
                        pass

            return _orig_entry(self, symbol, *args, **kwargs)

        Trader._entry = _patched_entry

    # ---------- exit hook ----------
    _orig_exit = getattr(Trader, "_exit_position", None)

    if callable(_orig_exit):
        def _patched_exit(self, idx, why, *a, **k):
            try:
                pos = self.positions[idx]
                entry = pos.get("entry_price", 0)
                price = self._get_price(pos["symbol"])
                pnl = (price - entry) * pos.get("qty", 0)
                if pos.get("side") == "SHORT":
                    pnl *= -1

                self._ai_adjust(pnl)
            except:
                pass

            return _orig_exit(self, idx, why, *a, **k)

        Trader._exit_position = _patched_exit

except Exception as e:
    print("AI CAPITAL PATCH FAIL:", e)
# =========================
# 🔥 CIRCUIT BREAKER AUTO RECOVER PATCH
# =========================

try:
    _orig_tick_cb = getattr(Trader, "tick", None)

    if callable(_orig_tick_cb):
        def _patched_tick_cb(self, *args, **kwargs):

            # --- cooldown 끝났으면 자동 복구 ---
            try:
                now_ts = time.time()

                if hasattr(self, "_cooldown_until"):
                    if self._cooldown_until and now_ts >= self._cooldown_until:
                        if hasattr(self, "trading_enabled") and not self.trading_enabled:
                            self.trading_enabled = True
                            self._cooldown_until = 0

                            try:
                                self._notify("🔄 Circuit Breaker 자동 복구됨")
                            except:
                                pass

            except Exception as e:
                print("CB AUTO RECOVER ERROR:", e)

            return _orig_tick_cb(self, *args, **kwargs)

        Trader.tick = _patched_tick_cb

except Exception as e:
    print("CB PATCH FAIL:", e)
# === AI UPGRADE PATCH (LEVERAGE / CORRELATION / SLIPPAGE / ONLINE LEARNING) ===

try:
    from leverage_ai import calc_leverage
    from correlation_filter import is_correlated
    from slippage_ai import get_slippage_factor, is_slippage_too_high, record_slippage
    from online_learning import get_learning_factor, record_trade
except Exception:
    calc_leverage = None
    is_correlated = None
    get_slippage_factor = None
    is_slippage_too_high = None
    record_slippage = None
    record_trade = None


try:
    _orig_enter_ai = getattr(Trader, "_enter", None)

    if callable(_orig_enter_ai):
        def _enter_ai(self, symbol, side, *args, **kwargs):

            try:
                # === correlation filter ===
                if is_correlated and hasattr(self, "positions"):
                    open_pos = list(getattr(self, "positions", {}).keys())
                    if is_correlated(symbol, open_pos):
                        self.state["last_skip_reason"] = "CORRELATION_BLOCK"
                        return

                # === slippage filter ===
                if is_slippage_too_high and is_slippage_too_high():
                    self.state["last_skip_reason"] = "SLIPPAGE_TOO_HIGH"
                    return

                # === online learning factor ===
                learn_factor = 1.0
                if get_learning_factor:
                    learn_factor = float(get_learning_factor())

                # === slippage factor ===
                slip_factor = 1.0
                if get_slippage_factor:
                    slip_factor = float(get_slippage_factor())

                # === apply order size ===
                if hasattr(self, "_mp"):
                    mp = self._mp()
                    if isinstance(mp, dict):
                        base_usdt = float(mp.get("order_usdt", 0))
                        new_usdt = base_usdt * learn_factor * slip_factor
                        mp["order_usdt"] = max(1, new_usdt)

                # === leverage AI ===
                if calc_leverage:
                    volatility = getattr(self, "last_volatility", 0.02)
                    lev = calc_leverage(volatility)
                    setattr(self, "ai_leverage", lev)

            except Exception as e:
                try:
                    self.err_throttled(f"AI_PATCH_ERR {e}")
                except Exception:
                    pass

            return _orig_enter_ai(self, symbol, side, *args, **kwargs)

        Trader._enter = _enter_ai

except Exception:
    pass


# === PNL RECORD PATCH ===

try:
    _orig_close_ai = getattr(Trader, "_close_position", None)

    if callable(_orig_close_ai):
        def _close_position_ai(self, *args, **kwargs):

            result = _orig_close_ai(self, *args, **kwargs)

            try:
                pnl = getattr(self, "last_pnl", None)
                if pnl is not None and record_trade:
                    record_trade(float(pnl))
            except Exception:
                pass

            return result

        Trader._close_position = _close_position_ai

except Exception:
    pass

# === END AI UPGRADE PATCH ===
# === AI STATUS PATCH ===
try:
    from online_learning import get_learning_state
except Exception:
    get_learning_state = None

try:
    _orig_status_ai = getattr(Trader, "status_text", None)

    if callable(_orig_status_ai):
        def _status_text_ai(self, *args, **kwargs):
            text = _orig_status_ai(self, *args, **kwargs)

            try:
                lines = []

                # learning 상태
                if get_learning_state:
                    st = get_learning_state()
                    lines.append(
                        f"🤖 AI trades={st.get('count')} "
                        f"winrate={st.get('winrate')} "
                        f"factor={st.get('factor')}"
                    )

                # leverage 상태
                lev = getattr(self, "ai_leverage", None)
                if lev:
                    lines.append(f"⚡ AI leverage={lev}x")

                # 마지막 skip 이유
                last_skip = getattr(self, "state", {}).get("last_skip_reason", None)
                if last_skip:
                    lines.append(f"⛔ skip={last_skip}")

                if lines:
                    text += "\n" + "\n".join(lines)

            except Exception:
                pass

            return text

        Trader.status_text = _status_text_ai

except Exception:
    pass

# === END AI STATUS PATCH ===
# === MULTI-COIN PORTFOLIO AI PATCH ===

try:
    from symbol_weight import get_symbol_weight
except Exception:
    get_symbol_weight = None

try:
    _orig_enter_port_ai = getattr(Trader, "_enter", None)

    if callable(_orig_enter_port_ai):
        def _enter_port_ai(self, symbol, side, *args, **kwargs):
            try:
                state = getattr(self, "state", {})
                if not isinstance(state, dict):
                    state = {}

                # 현재 열려있는 포지션 수 추정
                open_count = 0
                open_symbols = []

                positions_obj = getattr(self, "positions", None)
                if isinstance(positions_obj, dict):
                    for k, v in positions_obj.items():
                        try:
                            qty = 0.0
                            if isinstance(v, dict):
                                qty = abs(float(v.get("qty", 0) or 0))
                            if qty > 0:
                                open_count += 1
                                open_symbols.append(str(k).upper())
                        except Exception:
                            continue

                # fallback
                if open_count == 0:
                    try:
                        plist = getattr(self, "position", {}) or {}
                        if isinstance(plist, dict) and abs(float(plist.get("qty", 0) or 0)) > 0:
                            open_count = 1
                            open_symbols = [str(symbol).upper()]
                    except Exception:
                        pass

                # 최대 동시 포지션
                try:
                    max_pos = int(getattr(self, "max_positions", 0) or 0)
                except Exception:
                    max_pos = 0
                if max_pos <= 0:
                    try:
                        import os as _os
                        max_pos = int(_os.getenv("MAX_POSITIONS", "3"))
                    except Exception:
                        max_pos = 3

                # 이미 꽉 찼으면 진입 차단
                if open_count >= max_pos:
                    state["last_skip_reason"] = f"MAX_POSITIONS_REACHED:{open_count}/{max_pos}"
                    self.state = state
                    return

                # 기본 주문 크기 가져오기
                mp = {}
                if hasattr(self, "_mp") and callable(getattr(self, "_mp")):
                    try:
                        mp = self._mp() or {}
                    except Exception:
                        mp = {}
                if not isinstance(mp, dict):
                    mp = {}

                base_usdt = float(mp.get("order_usdt", 0) or 0)
                if base_usdt <= 0:
                    base_usdt = float(getattr(self, "order_usdt", 0) or 0)

                # 포지션이 많을수록 자동 축소
                # 예: 0개=100%, 1개=85%, 2개=70%, 3개+=55%
                if open_count <= 0:
                    diversify_factor = 1.00
                elif open_count == 1:
                    diversify_factor = 0.85
                elif open_count == 2:
                    diversify_factor = 0.70
                else:
                    diversify_factor = 0.55

                # 종목별 weight 반영
                weight = 1.0
                if get_symbol_weight:
                    try:
                        weight = float(get_symbol_weight(str(symbol).upper()))
                    except Exception:
                        weight = 1.0

                new_usdt = max(1.0, float(base_usdt) * float(diversify_factor) * float(weight))
                mp["order_usdt"] = new_usdt

                # 상태 저장
                state["portfolio_open_count"] = open_count
                state["portfolio_max_positions"] = max_pos
                state["portfolio_diversify_factor"] = round(diversify_factor, 4)
                state["portfolio_symbol_weight"] = round(weight, 4)
                state["portfolio_order_usdt"] = round(new_usdt, 4)
                state["portfolio_open_symbols"] = ",".join(open_symbols[:10]) if open_symbols else "NONE"
                self.state = state

            except Exception as e:
                try:
                    self.err_throttled(f"PORT_AI_ERR {e}")
                except Exception:
                    pass

            return _orig_enter_port_ai(self, symbol, side, *args, **kwargs)

        Trader._enter = _enter_port_ai

except Exception:
    pass


# === PORTFOLIO STATUS PATCH ===

try:
    _orig_status_port_ai = getattr(Trader, "status_text", None)

    if callable(_orig_status_port_ai):
        def _status_text_port_ai(self, *args, **kwargs):
            text = _orig_status_port_ai(self, *args, **kwargs)

            try:
                st = getattr(self, "state", {})
                if not isinstance(st, dict):
                    st = {}

                oc = st.get("portfolio_open_count", None)
                mp = st.get("portfolio_max_positions", None)
                df = st.get("portfolio_diversify_factor", None)
                sw = st.get("portfolio_symbol_weight", None)
                ou = st.get("portfolio_order_usdt", None)
                osyms = st.get("portfolio_open_symbols", None)

                extra = []
                if oc is not None and mp is not None:
                    extra.append(f"📊 PORT {oc}/{mp}")
                if df is not None:
                    extra.append(f"📉 div_factor={df}")
                if sw is not None:
                    extra.append(f"⚖️ sym_weight={sw}")
                if ou is not None:
                    extra.append(f"💵 port_usdt={ou}")
                if osyms:
                    extra.append(f"🧺 open={osyms}")

                if extra:
                    text += "\n" + "\n".join(extra)

            except Exception:
                pass

            return text

        Trader.status_text = _status_text_port_ai

except Exception:
    pass

# === END MULTI-COIN PORTFOLIO AI PATCH ===
# === DOCTOR PATCH ===

try:
    _orig_handle_cmd = getattr(Trader, "handle_telegram_command", None)

    if callable(_orig_handle_cmd):
        def _handle_cmd_doctor(self, text, *args, **kwargs):

            if isinstance(text, str) and text.strip().lower() == "/doctor":
                try:
                    lines = []
                    lines.append("🩺 BOT DOCTOR")

                    # 기본 상태
                    lines.append(f"RUNNING: True")

                    # leverage
                    lev = getattr(self, "ai_leverage", None)
                    if lev:
                        lines.append(f"LEVERAGE_AI: {lev}x")

                    # last skip
                    last_skip = getattr(self, "state", {}).get("last_skip_reason", None)
                    if last_skip:
                        lines.append(f"LAST_SKIP: {last_skip}")

                    # 포지션 수
                    oc = getattr(self, "state", {}).get("portfolio_open_count", None)
                    if oc is not None:
                        lines.append(f"OPEN_POS: {oc}")

                    # learning factor
                    try:
                        from online_learning import get_learning_state
                        st = get_learning_state()
                        lines.append(f"AI_TRADES: {st.get('count')}")
                        lines.append(f"AI_WINRATE: {st.get('winrate')}")
                    except Exception:
                        pass

                    msg = "\n".join(lines)

                    try:
                        self.tg_send(msg)
                    except Exception:
                        pass

                    return

                except Exception:
                    pass

            return _orig_handle_cmd(self, text, *args, **kwargs)

        Trader.handle_telegram_command = _handle_cmd_doctor

except Exception:
    pass

# === END DOCTOR PATCH ===
# =========================
# KULAMAGI / QULLAMAGGIE STYLE APPEND-ONLY PATCH
# paste this at the VERY BOTTOM of trader.py
# =========================
try:
    import os as _kg_os
    import math as _kg_math

    def _kg_env_bool(name, default=False):
        v = str(_kg_os.getenv(name, str(default))).strip().lower()
        return v in ("1", "true", "yes", "y", "on")

    def _kg_env_int(name, default):
        try:
            return int(str(_kg_os.getenv(name, default)).strip())
        except Exception:
            return int(default)

    def _kg_env_float(name, default):
        try:
            return float(str(_kg_os.getenv(name, default)).strip())
        except Exception:
            return float(default)

    def _kg_is_df(x):
        try:
            cols = getattr(x, "columns", None)
            return cols is not None and ("close" in cols) and ("high" in cols) and ("low" in cols)
        except Exception:
            return False

    def _kg_find_df(self, *args, **kwargs):
        for v in kwargs.values():
            if _kg_is_df(v):
                return v
        for a in args:
            if _kg_is_df(a):
                return a
        for name in ("df", "_df", "klines", "_klines", "candles", "_candles", "data"):
            v = getattr(self, name, None)
            if _kg_is_df(v):
                return v
        return None

    def _kg_tail_mean(series, n, default=0.0):
        try:
            s = series.dropna()
            if len(s) == 0:
                return float(default)
            n = max(1, min(int(n), len(s)))
            return float(s.iloc[-n:].mean())
        except Exception:
            return float(default)

    def _kg_safe_float(x, default=0.0):
        try:
            if x is None:
                return float(default)
            return float(x)
        except Exception:
            return float(default)

    def _kg_ema(series, span):
        try:
            return series.ewm(span=int(span), adjust=False).mean()
        except Exception:
            return series

    def _kg_atr(df, period=14):
        h = df["high"]
        l = df["low"]
        c = df["close"]
        pc = c.shift(1)
        tr1 = (h - l).abs()
        tr2 = (h - pc).abs()
        tr3 = (l - pc).abs()
        tr = tr1.combine(tr2, max).combine(tr3, max)
        return tr.rolling(int(period)).mean()

    def _kg_infer_side(*args, **kwargs):
        for k in ("side", "direction", "dir", "signal_side"):
            v = kwargs.get(k, None)
            if isinstance(v, str):
                vv = v.strip().lower()
                if vv in ("long", "buy", "bull", "up"):
                    return "long"
                if vv in ("short", "sell", "bear", "down"):
                    return "short"
        for a in args:
            if isinstance(a, str):
                aa = a.strip().lower()
                if aa in ("long", "buy", "bull", "up"):
                    return "long"
                if aa in ("short", "sell", "bear", "down"):
                    return "short"
        for k in ("is_long", "long_mode"):
            if kwargs.get(k) is True:
                return "long"
        for k in ("is_short", "short_mode"):
            if kwargs.get(k) is True:
                return "short"
        return None

    def _kg_signal_from_df(df):
        """
        Qullamaggie-ish:
        1) trend in place (10/20/50 EMA alignment)
        2) prior impulse exists
        3) recent range compression / box
        4) breakout candle with larger body
        """
        out = {
            "ok": False,
            "long_ok": False,
            "short_ok": False,
            "long_bonus": 0.0,
            "short_bonus": 0.0,
            "reason": "KULA_NA",
            "box_high": None,
            "box_low": None,
            "long_stop_anchor": None,
            "short_stop_anchor": None,
        }

        if df is None or len(df) < 80:
            out["reason"] = "KULA_DF_SHORT"
            return out

        try:
            lookback_box = max(4, _kg_env_int("KULA_BOX_BARS", 8))
            trend_lookback = max(20, _kg_env_int("KULA_TREND_BARS", 48))
            atr_period = max(5, _kg_env_int("KULA_ATR_PERIOD", 14))
            body_mult = max(1.0, _kg_env_float("KULA_BODY_MULT", 1.35))
            min_impulse_atr = max(0.5, _kg_env_float("KULA_MIN_IMPULSE_ATR", 3.0))
            max_box_atr = max(0.2, _kg_env_float("KULA_MAX_BOX_ATR", 2.2))
            pullback_to_ema50_atr = max(0.2, _kg_env_float("KULA_PULLBACK_50_ATR", 2.5))
            breakout_buffer_bps = _kg_env_float("KULA_BREAKOUT_BUFFER_BPS", 5.0)
            breakout_buffer = breakout_buffer_bps / 10000.0
            bonus = _kg_env_float("KULA_SCORE_BONUS", 22.0)

            close = df["close"].astype(float)
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            open_ = df["open"].astype(float) if "open" in df.columns else close.shift(1).fillna(close)

            ema10 = _kg_ema(close, 10)
            ema20 = _kg_ema(close, 20)
            ema50 = _kg_ema(close, 50)
            atr = _kg_atr(df, atr_period)

            if len(close.dropna()) < 60 or len(atr.dropna()) < 20:
                out["reason"] = "KULA_IND_SHORT"
                return out

            i = -1
            prev_slice = slice(-(lookback_box + 1), -1)

            last_close = _kg_safe_float(close.iloc[i])
            last_open = _kg_safe_float(open_.iloc[i])
            last_high = _kg_safe_float(high.iloc[i])
            last_low = _kg_safe_float(low.iloc[i])
            last_ema10 = _kg_safe_float(ema10.iloc[i])
            last_ema20 = _kg_safe_float(ema20.iloc[i])
            last_ema50 = _kg_safe_float(ema50.iloc[i])
            last_atr = max(_kg_safe_float(atr.iloc[i], 0.0), 1e-12)

            prev_box_high = _kg_safe_float(high.iloc[prev_slice].max(), 0.0)
            prev_box_low = _kg_safe_float(low.iloc[prev_slice].min(), 0.0)
            out["box_high"] = prev_box_high
            out["box_low"] = prev_box_low

            recent_body_avg = _kg_tail_mean((close - open_).abs().iloc[-10:-1], 9, default=0.0)
            last_body = abs(last_close - last_open)

            box_range = max(prev_box_high - prev_box_low, 0.0)
            compressed = (box_range / last_atr) <= max_box_atr

            # prior impulse
            impulse_hi = _kg_safe_float(high.iloc[-trend_lookback-1:-1].max(), last_high)
            impulse_lo = _kg_safe_float(low.iloc[-trend_lookback-1:-1].min(), last_low)
            impulse_up_atr = (impulse_hi - impulse_lo) / last_atr if last_atr > 0 else 0.0
            impulse_dn_atr = (impulse_hi - impulse_lo) / last_atr if last_atr > 0 else 0.0

            long_trend = (last_ema10 > last_ema20 > last_ema50)
            short_trend = (last_ema10 < last_ema20 < last_ema50)

            # orderly pullback around ema10/20 but not broken too deep below/above ema50
            long_hold_50 = (last_low >= (last_ema50 - pullback_to_ema50_atr * last_atr))
            short_hold_50 = (last_high <= (last_ema50 + pullback_to_ema50_atr * last_atr))

            # higher lows / lower highs feel
            long_tight = _kg_safe_float(low.iloc[-3]) >= _kg_safe_float(low.iloc[-6:].min(), last_low) - 0.5 * last_atr
            short_tight = _kg_safe_float(high.iloc[-3]) <= _kg_safe_float(high.iloc[-6:].max(), last_high) + 0.5 * last_atr

            # breakout candle
            bull_break = (last_close > prev_box_high * (1.0 + breakout_buffer)) and (last_close > last_open)
            bear_break = (last_close < prev_box_low * (1.0 - breakout_buffer)) and (last_close < last_open)
            body_ok = last_body >= max(recent_body_avg * body_mult, 0.15 * last_atr)

            long_ok = all([
                long_trend,
                long_hold_50,
                compressed,
                long_tight,
                bull_break,
                body_ok,
                impulse_up_atr >= min_impulse_atr,
            ])

            short_ok = all([
                short_trend,
                short_hold_50,
                compressed,
                short_tight,
                bear_break,
                body_ok,
                impulse_dn_atr >= min_impulse_atr,
            ])

            out["long_ok"] = bool(long_ok)
            out["short_ok"] = bool(short_ok)
            out["ok"] = bool(long_ok or short_ok)
            out["long_bonus"] = float(bonus if long_ok else 0.0)
            out["short_bonus"] = float(bonus if short_ok else 0.0)
            out["long_stop_anchor"] = min(prev_box_low, last_low, last_ema50)
            out["short_stop_anchor"] = max(prev_box_high, last_high, last_ema50)

            if long_ok:
                out["reason"] = "KULA_LONG_OK"
            elif short_ok:
                out["reason"] = "KULA_SHORT_OK"
            else:
                why = []
                if not (long_trend or short_trend):
                    why.append("TREND")
                if not compressed:
                    why.append("NO_COMPRESSION")
                if not body_ok:
                    why.append("WEAK_BODY")
                if not (bull_break or bear_break):
                    why.append("NO_BREAKOUT")
                if not why:
                    why.append("SETUP_FAIL")
                out["reason"] = "KULA_" + "_".join(why[:3])

            return out
        except Exception as e:
            out["reason"] = f"KULA_ERR:{e}"
            return out

    def _kg_store_state(self, sig):
        try:
            if not hasattr(self, "state") or not isinstance(self.state, dict):
                self.state = {}
            self.state["last_kula_reason"] = sig.get("reason", "KULA_NA")
            self.state["last_kula_long_ok"] = bool(sig.get("long_ok", False))
            self.state["last_kula_short_ok"] = bool(sig.get("short_ok", False))
            self.state["last_kula_box_high"] = sig.get("box_high")
            self.state["last_kula_box_low"] = sig.get("box_low")
            if sig.get("long_ok"):
                self.state["last_event"] = "KULAMAGI LONG SETUP"
            elif sig.get("short_ok"):
                self.state["last_event"] = "KULAMAGI SHORT SETUP"
        except Exception:
            pass

    def _kg_kulamagic_signal(self, df=None):
        if not _kg_env_bool("KULA_ON", True):
            return {
                "ok": False,
                "long_ok": False,
                "short_ok": False,
                "long_bonus": 0.0,
                "short_bonus": 0.0,
                "reason": "KULA_OFF",
            }
        if df is None:
            df = _kg_find_df(self)
        sig = _kg_signal_from_df(df)
        _kg_store_state(self, sig)
        return sig

    # bind helper method to Trader
    try:
        setattr(Trader, "kulamagic_signal", _kg_kulamagic_signal)
    except Exception:
        pass

    def _kg_wrap_score_method(method_name):
        orig = getattr(Trader, method_name, None)
        if not callable(orig):
            return False

        def _wrapped(self, *args, **kwargs):
            base = orig(self, *args, **kwargs)
            try:
                if not _kg_env_bool("KULA_ON", True):
                    return base

                df = _kg_find_df(self, *args, **kwargs)
                sig = self.kulamagic_signal(df)
                side = _kg_infer_side(*args, **kwargs)

                # numeric score
                if isinstance(base, (int, float)):
                    if side == "long" and sig.get("long_ok"):
                        return float(base) + float(sig.get("long_bonus", 0.0))
                    if side == "short" and sig.get("short_ok"):
                        return float(base) + float(sig.get("short_bonus", 0.0))
                    return base

                # tuple/list with first element numeric
                if isinstance(base, (tuple, list)) and len(base) >= 1 and isinstance(base[0], (int, float)):
                    b0 = float(base[0])
                    if side == "long" and sig.get("long_ok"):
                        b0 += float(sig.get("long_bonus", 0.0))
                    elif side == "short" and sig.get("short_ok"):
                        b0 += float(sig.get("short_bonus", 0.0))
                    if isinstance(base, tuple):
                        return (b0,) + tuple(base[1:])
                    out = list(base)
                    out[0] = b0
                    return out

                # dict-like score payload
                if isinstance(base, dict):
                    out = dict(base)
                    if "long_score" in out and sig.get("long_ok"):
                        out["long_score"] = _kg_safe_float(out.get("long_score", 0.0)) + _kg_safe_float(sig.get("long_bonus", 0.0))
                    if "short_score" in out and sig.get("short_ok"):
                        out["short_score"] = _kg_safe_float(out.get("short_score", 0.0)) + _kg_safe_float(sig.get("short_bonus", 0.0))
                    if side == "long" and "score" in out and sig.get("long_ok"):
                        out["score"] = _kg_safe_float(out.get("score", 0.0)) + _kg_safe_float(sig.get("long_bonus", 0.0))
                    if side == "short" and "score" in out and sig.get("short_ok"):
                        out["score"] = _kg_safe_float(out.get("score", 0.0)) + _kg_safe_float(sig.get("short_bonus", 0.0))
                    out["kulamagic"] = sig
                    return out

                return base
            except Exception:
                return base

        setattr(Trader, method_name, _wrapped)
        return True

    def _kg_wrap_gate_method(method_name, side):
        orig = getattr(Trader, method_name, None)
        if not callable(orig):
            return False

        def _wrapped(self, *args, **kwargs):
            base = orig(self, *args, **kwargs)
            try:
                if not _kg_env_bool("KULA_ON", True):
                    return base
                if not _kg_env_bool("KULA_HARD_FILTER", True):
                    return base

                ok_base = bool(base)
                if not ok_base:
                    return base

                df = _kg_find_df(self, *args, **kwargs)
                sig = self.kulamagic_signal(df)

                if side == "long":
                    if not sig.get("long_ok", False):
                        try:
                            if not hasattr(self, "state") or not isinstance(self.state, dict):
                                self.state = {}
                            self.state["last_skip_reason"] = sig.get("reason", "KULA_LONG_BLOCK")
                        except Exception:
                            pass
                        return False
                    try:
                        self.state["last_kula_long_stop_anchor"] = sig.get("long_stop_anchor")
                    except Exception:
                        pass
                    return True

                if side == "short":
                    if not sig.get("short_ok", False):
                        try:
                            if not hasattr(self, "state") or not isinstance(self.state, dict):
                                self.state = {}
                            self.state["last_skip_reason"] = sig.get("reason", "KULA_SHORT_BLOCK")
                        except Exception:
                            pass
                        return False
                    try:
                        self.state["last_kula_short_stop_anchor"] = sig.get("short_stop_anchor")
                    except Exception:
                        pass
                    return True

                return base
            except Exception:
                return base

        setattr(Trader, method_name, _wrapped)
        return True

    _kg_wrapped_any = False

    for _name in (
        "_entry_score",
        "entry_score",
        "calc_entry_score",
        "score_entry",
        "_score_signal",
        "_signal_score",
        "_calc_entry_score",
    ):
        try:
            if _kg_wrap_score_method(_name):
                _kg_wrapped_any = True
        except Exception:
            pass

    for _name in (
        "should_enter_long",
        "_should_enter_long",
        "can_enter_long",
        "_can_enter_long",
    ):
        try:
            if _kg_wrap_gate_method(_name, "long"):
                _kg_wrapped_any = True
        except Exception:
            pass

    for _name in (
        "should_enter_short",
        "_should_enter_short",
        "can_enter_short",
        "_can_enter_short",
    ):
        try:
            if _kg_wrap_gate_method(_name, "short"):
                _kg_wrapped_any = True
        except Exception:
            pass

    # status text patch
    try:
        _kg_orig_status_text = getattr(Trader, "status_text", None)
        if callable(_kg_orig_status_text):
            def _kg_status_text(self, *args, **kwargs):
                txt = _kg_orig_status_text(self, *args, **kwargs)
                try:
                    sig = self.kulamagic_signal()
                    extra = []
                    extra.append(f"🎯 KULA={'ON' if _kg_env_bool('KULA_ON', True) else 'OFF'} HARD={'ON' if _kg_env_bool('KULA_HARD_FILTER', True) else 'OFF'}")
                    extra.append(f"📦 boxH={sig.get('box_high')} boxL={sig.get('box_low')}")
                    extra.append(f"🧠 kula_reason={sig.get('reason', 'KULA_NA')}")
                    return str(txt) + "\n" + "\n".join(extra)
                except Exception:
                    return txt
            setattr(Trader, "status_text", _kg_status_text)
    except Exception:
        pass

    try:
        print(f"[KULA PATCH] loaded wrapped_any={_kg_wrapped_any}")
    except Exception:
        pass

except Exception as _kula_patch_e:
    try:
        print("[KULA PATCH] load fail:", _kula_patch_e)
    except Exception:
        pass
# =========================
# END KULAMAGI PATCH
# =========================

# =========================
# STABILITY_PATCH_V2 - ops/why/decision-log patch
# - detailed /why
# - decision_logger integration
# - trade_journal integration
# - safe append-only monkey patch; trading logic unchanged
# =========================
try:
    import os as _v2_os
    import time as _v2_time

    try:
        from decision_logger import log_decision as _v2_log_decision, tail_decisions as _v2_tail_decisions, decision_log_path as _v2_decision_log_path
    except Exception:
        def _v2_log_decision(*a, **k): return False
        def _v2_tail_decisions(*a, **k): return []
        def _v2_decision_log_path(): return "-"

    try:
        from trade_journal import log_trade_event as _v2_log_trade_event, trade_journal_path as _v2_trade_journal_path
    except Exception:
        def _v2_log_trade_event(*a, **k): return False
        def _v2_trade_journal_path(): return "-"

    def _v2_float(x, default=None):
        try:
            if x is None:
                return default
            return float(x)
        except Exception:
            return default

    def _v2_int(x, default=0):
        try:
            return int(float(x))
        except Exception:
            return default

    def _v2_clip(s, n=96):
        try:
            s = str(s or "").replace("\n", " | ").strip()
        except Exception:
            s = ""
        if len(s) > n:
            return s[: max(0, n - 1)] + "…"
        return s

    def _v2_now_age(ts):
        try:
            ts = float(ts or 0)
            if ts <= 0:
                return "-"
            return f"{max(0, int(_v2_time.time() - ts))}s ago"
        except Exception:
            return "-"

    def _v2_ensure_state(self):
        try:
            if not hasattr(self, "state") or not isinstance(self.state, dict):
                self.state = {}
            return self.state
        except Exception:
            return {}

    if not getattr(Trader, "_stability_v2_applied", False):
        Trader._stability_v2_applied = True

        _v2_prev_score_symbol = getattr(Trader, "_score_symbol", None)
        if callable(_v2_prev_score_symbol):
            def _v2_score_symbol(self, symbol: str, price: float, *args, **kwargs):
                ts = int(_v2_time.time())
                try:
                    info = _v2_prev_score_symbol(self, symbol, price, *args, **kwargs)
                except Exception as e:
                    try:
                        st = _v2_ensure_state(self)
                        rec = {
                            "ts": ts,
                            "symbol": str(symbol or "").upper(),
                            "price": _v2_float(price, 0.0),
                            "ok": False,
                            "reason": f"SCORE_ERR:{e}",
                        }
                        st.setdefault("last_decisions", []).append(rec)
                        st["last_decisions"] = st.get("last_decisions", [])[-40:]
                        st["last_decision_ts"] = ts
                        st["last_skip_reason"] = rec["reason"]
                        _v2_log_decision("score_error", **rec)
                    except Exception:
                        pass
                    raise

                try:
                    st = _v2_ensure_state(self)
                    info_d = dict(info) if isinstance(info, dict) else {"raw": repr(info)}
                    try:
                        mp = self._mp()
                    except Exception:
                        mp = {}
                    need = _v2_float(mp.get("enter_score"), None)
                    score = _v2_float(info_d.get("score"), None)
                    ok = bool(info_d.get("ok", False))
                    side = str(info_d.get("side") or "-").upper()
                    reason = str(info_d.get("reason") or "")
                    shortage = None
                    if score is not None and need is not None:
                        shortage = round(float(need) - float(score), 4)

                    rec = {
                        "ts": ts,
                        "symbol": str(symbol or "").upper(),
                        "side": side,
                        "price": _v2_float(price, 0.0),
                        "ok": ok,
                        "score": score,
                        "need": need,
                        "shortage": shortage,
                        "strategy": info_d.get("strategy") or "-",
                        "reason": _v2_clip(reason, 220),
                        "mtf": ((st.get("mtf") or {}).get("trend") if isinstance(st.get("mtf"), dict) else None),
                        "kula": st.get("last_kula_reason"),
                    }
                    arr = st.setdefault("last_decisions", [])
                    arr.append(rec)
                    st["last_decisions"] = arr[-50:]
                    st["last_decision_ts"] = ts
                    if not ok and reason:
                        st["last_skip_reason"] = _v2_clip(reason, 180)
                    _v2_log_decision("score", **rec)
                except Exception:
                    pass
                return info
            Trader._score_symbol = _v2_score_symbol

        _v2_prev_enter = getattr(Trader, "_enter", None)
        if callable(_v2_prev_enter):
            def _v2_enter(self, symbol: str, side: str, price: float, reason: str, sl: float, tp: float, strategy: str = "", score: float = 0.0, atr: float = 0.0, *args, **kwargs):
                try:
                    _v2_log_decision("enter_attempt", symbol=symbol, side=side, price=price, reason=_v2_clip(reason, 260), sl=sl, tp=tp, strategy=strategy, score=score, atr=atr)
                    _v2_log_trade_event("enter_attempt", symbol=symbol, side=side, price=price, reason=_v2_clip(reason, 260), sl=sl, tp=tp, strategy=strategy, score=score, atr=atr)
                except Exception:
                    pass
                try:
                    rv = _v2_prev_enter(self, symbol, side, price, reason, sl, tp, strategy, score, atr, *args, **kwargs)
                    try:
                        _v2_log_trade_event("enter_done", symbol=symbol, side=side, price=price, strategy=strategy, score=score, positions=len(getattr(self, "positions", []) or []))
                    except Exception:
                        pass
                    return rv
                except Exception as e:
                    try:
                        _v2_log_decision("enter_error", symbol=symbol, side=side, price=price, strategy=strategy, score=score, error=str(e))
                        _v2_log_trade_event("enter_error", symbol=symbol, side=side, price=price, strategy=strategy, score=score, error=str(e))
                    except Exception:
                        pass
                    raise
            Trader._enter = _v2_enter

        _v2_prev_exit = getattr(Trader, "_exit_position", None)
        if callable(_v2_prev_exit):
            def _v2_exit_position(self, idx: int, why: str, force=False, *args, **kwargs):
                pos_snapshot = {}
                try:
                    pos = (getattr(self, "positions", []) or [])[idx]
                    if isinstance(pos, dict):
                        pos_snapshot = dict(pos)
                except Exception:
                    pos_snapshot = {}
                try:
                    _v2_log_trade_event("exit_attempt", idx=idx, why=why, force=force, position=pos_snapshot)
                except Exception:
                    pass
                try:
                    rv = _v2_prev_exit(self, idx, why, force=force, *args, **kwargs)
                    try:
                        _v2_log_trade_event("exit_done", idx=idx, why=why, force=force, position=pos_snapshot, day_profit=getattr(self, "day_profit", None), win=getattr(self, "win", None), loss=getattr(self, "loss", None))
                    except Exception:
                        pass
                    return rv
                except Exception as e:
                    try:
                        _v2_log_trade_event("exit_error", idx=idx, why=why, force=force, position=pos_snapshot, error=str(e))
                    except Exception:
                        pass
                    raise
            Trader._exit_position = _v2_exit_position

        _v2_prev_tick = getattr(Trader, "tick", None)
        if callable(_v2_prev_tick):
            def _v2_tick(self, *args, **kwargs):
                before_event = ""
                try:
                    before_event = str((getattr(self, "state", {}) or {}).get("last_event", "") or "")
                except Exception:
                    before_event = ""
                try:
                    rv = _v2_prev_tick(self, *args, **kwargs)
                    try:
                        st = _v2_ensure_state(self)
                        after_event = str(st.get("last_event", "") or "")
                        if after_event and after_event != before_event and after_event.startswith("대기"):
                            _v2_log_decision("gate", reason=after_event, last_skip=st.get("last_skip_reason"), mode=getattr(self, "mode", None), trading_enabled=getattr(self, "trading_enabled", None))
                    except Exception:
                        pass
                    return rv
                except Exception as e:
                    try:
                        _v2_log_decision("tick_error", error=str(e), mode=getattr(self, "mode", None), trading_enabled=getattr(self, "trading_enabled", None))
                    except Exception:
                        pass
                    raise
            Trader.tick = _v2_tick

        def _v2_why_text(self):
            try:
                mp = self._mp()
            except Exception:
                mp = {}
            st = _v2_ensure_state(self)
            last_scan = st.get("last_scan") or {}
            reasons = last_scan.get("reasons") or []
            picked = last_scan.get("picked")
            decisions = list(st.get("last_decisions") or [])
            mtf = st.get("mtf") if isinstance(st.get("mtf"), dict) else {}
            last_skip = str(getattr(self, "_last_skip_reason", "") or st.get("last_skip_reason", "") or "")
            last_event = str(st.get("last_event", "") or "")
            try:
                cooldown_left = max(0, int(float(getattr(self, "_cooldown_until", 0) or 0) - _v2_time.time()))
            except Exception:
                cooldown_left = 0
            try:
                allowed_now = entry_allowed_now_utc()
            except Exception:
                allowed_now = "?"
            try:
                pos_n = len(getattr(self, "positions", []) or [])
            except Exception:
                pos_n = 0

            lines = []
            lines.append("🔍 WHY / 진입 판단")
            lines.append(f"ON={bool(getattr(self, 'trading_enabled', False))} | MODE={getattr(self, 'mode', '-')} | DRY_RUN={DRY_RUN}")
            lines.append(f"score>={mp.get('enter_score', '-')} | lev={mp.get('lev', '-')} | usdt={mp.get('order_usdt', '-')} | pos={pos_n}/{getattr(self, 'max_positions', '-')}")
            lines.append(f"AUTO={getattr(self, 'auto_symbol', '-')} | DISC={getattr(self, 'auto_discovery', '-')} | DIV={getattr(self, 'diversify', '-')} | universe={len(getattr(self, 'symbols', []) or [])}")
            lines.append(f"allowed_now={allowed_now} | cooldown={cooldown_left}s | day_entries={getattr(self, '_day_entries', 0)}/{MAX_ENTRIES_PER_DAY}")
            lines.append(f"filters: MTF={USE_MTF_FILTER}({mtf.get('trend','-')}) LIQ={USE_LIQUIDITY_FILTER} spread<={MAX_SPREAD_BPS}bps avoidRSI={bool(st.get('avoid_low_rsi', False))}")
            try:
                lines.append(f"hard={globals().get('HARDENING_ON', '-')} adx>={globals().get('HARD_MIN_ADX', '-')} atr%={globals().get('HARD_MIN_ATR_PCT', '-')}-{globals().get('HARD_MAX_ATR_PCT', '-')}")
            except Exception:
                pass
            try:
                lines.append(f"kula={_kg_env_bool('KULA_ON', True)} hard={_kg_env_bool('KULA_HARD_FILTER', True)} reason={st.get('last_kula_reason', '-')}")
            except Exception:
                pass

            blockers = []
            if not bool(getattr(self, 'trading_enabled', False)):
                blockers.append("거래 OFF")
            if pos_n >= int(getattr(self, 'max_positions', 1) or 1):
                blockers.append("MAX_POSITIONS 도달")
            if cooldown_left > 0:
                blockers.append(f"쿨다운 {cooldown_left}s")
            if allowed_now is False:
                blockers.append(f"시간필터 UTC {TRADE_HOURS_UTC}")
            if int(getattr(self, '_day_entries', 0) or 0) >= int(MAX_ENTRIES_PER_DAY):
                blockers.append("일일 진입 제한")
            if blockers:
                lines.append("🚫 즉시차단: " + ", ".join(blockers))

            if last_skip:
                lines.append(f"last_skip={_v2_clip(last_skip, 120)}")
            if last_event:
                lines.append(f"last_event={_v2_clip(last_event, 120)}")

            if picked:
                try:
                    lines.append(f"picked={picked.get('symbol')} {picked.get('side')} score={picked.get('score')} strategy={picked.get('strategy','-')}")
                except Exception:
                    lines.append(f"picked={_v2_clip(picked, 100)}")
            else:
                lines.append("picked=None")

            # Top current decisions by score, keeping recent scan records.
            fresh = []
            now = _v2_time.time()
            for r in decisions:
                try:
                    if now - float(r.get("ts", 0) or 0) <= 600:
                        fresh.append(r)
                except Exception:
                    pass
            if fresh:
                def _sort_key(r):
                    sc = _v2_float(r.get("score"), -9999.0)
                    okv = 1 if r.get("ok") else 0
                    return (okv, sc if sc is not None else -9999.0)
                top = sorted(fresh[-30:], key=_sort_key, reverse=True)[:10]
                lines.append("최근 후보 TOP:")
                for r in top:
                    sc = r.get("score")
                    need = r.get("need")
                    shortage = r.get("shortage")
                    status = "OK" if r.get("ok") else "NO"
                    lack = ""
                    try:
                        if shortage is not None and float(shortage) > 0:
                            lack = f" 부족 {float(shortage):.0f}"
                    except Exception:
                        pass
                    lines.append(f"- {r.get('symbol','-')} {r.get('side','-')} {status} {sc}/{need}{lack} {r.get('strategy','-')} | {_v2_clip(r.get('reason'), 68)}")
            elif reasons:
                lines.append("최근 스캔 차단/탈락:")
                for r in reasons[:12]:
                    lines.append(f"- {_v2_clip(r, 100)}")
            else:
                lines.append("최근 스캔 기록 없음. /status 후 1~2틱 뒤 /why")

            try:
                lines.append(f"log=decisions.jsonl | age={_v2_now_age(st.get('last_decision_ts'))}")
            except Exception:
                pass
            return "\n".join(lines)

        Trader.why_text = _v2_why_text

        try:
            print("[STABILITY_PATCH_V2] loaded decision_logger=True detailed_why=True")
        except Exception:
            pass

except Exception as _stability_v2_e:
    try:
        print("[STABILITY_PATCH_V2] load fail:", _stability_v2_e)
    except Exception:
        pass
# =========================
# END STABILITY_PATCH_V2
# =========================

