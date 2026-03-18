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

    if strategy == "low_risk":
        return False, f"STRATEGY_BLOCK: {regime} -> low_risk", strategy

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
            ["📊 상태 /status", "❓ 도움말 /help"],
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
        key = f"{symbol}:{self.mode}:{mp['lev']}"
        if self._lev_set_cache.get(key):
            return
        if not DRY_RUN:
            try:
                set_leverage(symbol, int(mp["lev"]))
            except Exception as e:
                # ✅ FIX 5: double-safe
                msg = str(e)
                if "110043" in msg or "leverage not modified" in msg.lower():
                    pass
                else:
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
        mtf_block_long = (mtf == "DOWN")
        mtf_block_short = (mtf == "UP")

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
            "/status\n"
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

        if imported > 0:
            self.notify_throttled(f"🔄 실계정 포지션 {imported}개 자동-import 완료 (재시작/불일치 복구)", 120)

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
    _entry_hard_prev_compute = Trader.compute_signal_and_exits

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

except Exception as _stb_e:
    print(f"[STABILITY_PATCH_ERR] {_stb_e}")
# ===== QUANT PATCH (APPEND SAFE) =====
try:
    import time
    from datetime import datetime

    _orig_tick = Trader.tick

    def _quant_tick(self, *args, **kwargs):

        # ===== FILTER =====
        hour = datetime.utcnow().hour
        if 3 <= hour <= 8:
            self.state["last_skip_reason"] = "time_filter"
            return

        last_loss_ts = self.state.get("last_loss_ts")
        if last_loss_ts and time.time() - last_loss_ts < 600:
            self.state["last_skip_reason"] = "loss_cooldown"
            return

        # ===== ORIGINAL =====
        return _orig_tick(self, *args, **kwargs)

    Trader.tick = _quant_tick

except Exception as e:
    print("quant patch fail:", e)
# ===== QUANT PATCH 2 (ADX + VOLUME) =====
try:
    _orig_tick_quant2 = Trader.tick

    def _quant_tick2(self, *args, **kwargs):
        try:
            if not hasattr(self, "state") or not isinstance(self.state, dict):
                self.state = {}

            # ===== ADX 필터 =====
            adx = self.state.get("adx")
            if adx is not None:
                try:
                    if float(adx) < 20:
                        self.state["last_skip_reason"] = "low_trend_adx"
                        return
                except Exception:
                    pass

            # ===== 볼륨 필터 =====
            vol = self.state.get("volume")
            vol_ma = self.state.get("volume_ma")

            if vol is not None and vol_ma is not None:
                try:
                    if float(vol) < float(vol_ma) * 1.2:
                        self.state["last_skip_reason"] = "low_volume"
                        return
                except Exception:
                    pass

        except Exception as e:
            try:
                self.state["last_skip_reason"] = f"quant2_err:{e}"
            except Exception:
                pass

        return _orig_tick_quant2(self, *args, **kwargs)

    Trader.tick = _quant_tick2

except Exception as e:
    print("quant2 patch fail:", e)
