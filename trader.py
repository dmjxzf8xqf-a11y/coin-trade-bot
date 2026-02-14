# trader.py (FULL QUANT COPY-PASTE)
# - Works on Railway/Render (single file replacement)
# - Bybit v5 (Linear)
# - LONG/SHORT auto decision with multi-signal scoring (quant-style)
# - SAFE / AGGRESSIVE modes (Telegram commands)
# - Entry/Exit reasons + confidence + daily PnL/WinRate tracking
#
# NOTE:
# - This is still rule-based "quant scoring" (not true ML). It is designed to be robust + explainable + cheap to run.
# - It will NOT "guarantee profit". Use DRY_RUN first.

import os
import time
import json
import math
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from datetime import datetime, timezone

# If you already have config.py, keep it.
# Required in config.py (or env):
# BYBIT_API_KEY, BYBIT_API_SECRET, CATEGORY, SYMBOL, ACCOUNT_TYPE, DRY_RUN
try:
    from config import *  # noqa
except Exception:
    pass

# -------------------------
# Helpers (safe config)
# -------------------------
def _cfg(name, default):
    try:
        return globals().get(name, default)
    except Exception:
        return default

def _env(name, default=None):
    v = os.getenv(name)
    return v if v is not None and str(v).strip() != "" else default

# -------------------------
# Network / Proxy / Headers
# -------------------------
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
PROXY = _env("HTTPS_PROXY") or _env("HTTP_PROXY") or ""
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

# -------------------------
# Bybit base URL
# -------------------------
try:
    _base = (_env("BYBIT_BASE_URL") or _cfg("BYBIT_BASE_URL", "https://api.bybit.com") or "https://api.bybit.com").strip()
except Exception:
    _base = "https://api.bybit.com"
BYBIT_BASE_URL = _base.rstrip("/")

# -------------------------
# Trading defaults
# -------------------------
CATEGORY = _env("CATEGORY", _cfg("CATEGORY", "linear"))
SYMBOL = _env("SYMBOL", _cfg("SYMBOL", "BTCUSDT"))
ACCOUNT_TYPE = _env("ACCOUNT_TYPE", _cfg("ACCOUNT_TYPE", "UNIFIED"))
DRY_RUN = str(_env("DRY_RUN", str(_cfg("DRY_RUN", "true")))).lower() in ("1", "true", "yes", "y", "on")

BYBIT_API_KEY = _env("BYBIT_API_KEY", _cfg("BYBIT_API_KEY", ""))
BYBIT_API_SECRET = _env("BYBIT_API_SECRET", _cfg("BYBIT_API_SECRET", ""))

BOT_TOKEN = _env("BOT_TOKEN", _cfg("BOT_TOKEN", ""))
CHAT_ID = _env("CHAT_ID", _cfg("CHAT_ID", ""))

# -------------------------
# Quant engine parameters
# -------------------------
ENTRY_INTERVAL = str(_env("ENTRY_INTERVAL", str(_cfg("ENTRY_INTERVAL", "15"))))  # minutes: "1","3","5","15","30","60"
KLINE_LIMIT = int(_env("KLINE_LIMIT", str(_cfg("KLINE_LIMIT", 300))))

# trend / momentum
EMA_FAST = int(_env("EMA_FAST", str(_cfg("EMA_FAST", 20))))
EMA_SLOW = int(_env("EMA_SLOW", str(_cfg("EMA_SLOW", 50))))
RSI_PERIOD = int(_env("RSI_PERIOD", str(_cfg("RSI_PERIOD", 14))))
MACD_FAST = int(_env("MACD_FAST", str(_cfg("MACD_FAST", 12))))
MACD_SLOW = int(_env("MACD_SLOW", str(_cfg("MACD_SLOW", 26))))
MACD_SIGNAL = int(_env("MACD_SIGNAL", str(_cfg("MACD_SIGNAL", 9))))

# filters
VOL_SPIKE_MULT = float(_env("VOL_SPIKE_MULT", str(_cfg("VOL_SPIKE_MULT", 2.2))))  # volume spike threshold
MAX_VOLATILITY_PCT = float(_env("MAX_VOLATILITY_PCT", str(_cfg("MAX_VOLATILITY_PCT", 1.2))))  # avoid super choppy, % of price

# risk management
LEVERAGE_SAFE = int(_env("LEVERAGE_SAFE", str(_cfg("LEVERAGE_SAFE", 3))))
LEVERAGE_AGGR = int(_env("LEVERAGE_AGGR", str(_cfg("LEVERAGE_AGGR", 7))))

RISK_SAFE = float(_env("RISK_SAFE", str(_cfg("RISK_SAFE", 0.02))))        # fraction of equity as "margin budget"
RISK_AGGR = float(_env("RISK_AGGR", str(_cfg("RISK_AGGR", 0.05))))

ATR_PERIOD = int(_env("ATR_PERIOD", str(_cfg("ATR_PERIOD", 14))))
STOP_ATR_MULT_SAFE = float(_env("STOP_ATR_MULT_SAFE", str(_cfg("STOP_ATR_MULT_SAFE", 1.8))))
STOP_ATR_MULT_AGGR = float(_env("STOP_ATR_MULT_AGGR", str(_cfg("STOP_ATR_MULT_AGGR", 1.3))))
TP_R_MULT_SAFE = float(_env("TP_R_MULT_SAFE", str(_cfg("TP_R_MULT_SAFE", 1.4))))
TP_R_MULT_AGGR = float(_env("TP_R_MULT_AGGR", str(_cfg("TP_R_MULT_AGGR", 2.0))))

COOLDOWN_SEC_SAFE = int(_env("COOLDOWN_SEC_SAFE", str(_cfg("COOLDOWN_SEC_SAFE", 60 * 30))))
COOLDOWN_SEC_AGGR = int(_env("COOLDOWN_SEC_AGGR", str(_cfg("COOLDOWN_SEC_AGGR", 60 * 10))))
MAX_ENTRIES_PER_DAY_SAFE = int(_env("MAX_ENTRIES_PER_DAY_SAFE", str(_cfg("MAX_ENTRIES_PER_DAY_SAFE", 4))))
MAX_ENTRIES_PER_DAY_AGGR = int(_env("MAX_ENTRIES_PER_DAY_AGGR", str(_cfg("MAX_ENTRIES_PER_DAY_AGGR", 10))))

MIN_NOTIONAL_USDT_FALLBACK = float(_env("MIN_NOTIONAL_USDT", str(_cfg("MIN_NOTIONAL_USDT", 5.0))))
ALERT_COOLDOWN_SEC = int(_env("ALERT_COOLDOWN_SEC", str(_cfg("ALERT_COOLDOWN_SEC", 60))))

# decision threshold
ENTER_PROB_THRESHOLD_SAFE = float(_env("ENTER_PROB_THRESHOLD_SAFE", str(_cfg("ENTER_PROB_THRESHOLD_SAFE", 0.70))))
ENTER_PROB_THRESHOLD_AGGR = float(_env("ENTER_PROB_THRESHOLD_AGGR", str(_cfg("ENTER_PROB_THRESHOLD_AGGR", 0.62))))

MAX_CONSEC_LOSSES = int(_env("MAX_CONSEC_LOSSES", str(_cfg("MAX_CONSEC_LOSSES", 3))))

# -------------------------
# Trader
# -------------------------
class Trader:
    """
    FULL QUANT bot (rule-based scoring)
    - LONG/SHORT decision via multi-signal probabilistic score
    - SAFE / AGGRESSIVE modes
    - Entry/Exit reasons, confidence, daily stats, alerts
    Telegram commands:
      /start /stop /status /help
      /safe /aggressive /mode
      /panic
      /buy /sell (manual)
    """

    def __init__(self, state):
        self.state = state

        # runtime toggles
        self.trading_enabled = bool(_cfg("TRADING_ENABLED_DEFAULT", True))
        self.mode = str(_env("MODE", "SAFE")).upper()
        if self.mode not in ("SAFE", "AGGRESSIVE"):
            self.mode = "SAFE"

        # internal position mirror
        self.position = None  # "LONG" / "SHORT" / None
        self.entry_price = None
        self.entry_ts = None
        self.entry_side = None  # "Buy"/"Sell"

        self.lev_set = False
        self.consec_losses = 0
        self._cooldown_until = 0

        # daily stats
        self._day_key = None
        self._day_entries = 0
        self.day_pnl_usdt = 0.0
        self.day_wins = 0
        self.day_losses = 0
        self._last_report_day = None

        # cache instrument rules
        self._rules_cache = None
        self._rules_cache_ts = 0

        # anti spam
        self._last_alert_ts = 0
        self._last_bybit_err_ts = 0

        # last explanations
        self.state["entry_reason"] = ""
        self.state["exit_reason"] = ""
        self.state["last_decision"] = ""
        self.state["mode"] = self.mode

    # -------------------------
    # Telegram
    # -------------------------
    def tg_send(self, msg: str):
        print(msg)
        if BOT_TOKEN and CHAT_ID:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data={"chat_id": CHAT_ID, "text": msg},
                    timeout=10,
                )
            except Exception:
                pass

    def tg_send_throttled(self, msg: str):
        if time.time() - self._last_alert_ts >= ALERT_COOLDOWN_SEC:
            self._last_alert_ts = time.time()
            self.tg_send(msg)

    def tg_send_bybit_err_throttled(self, msg: str):
        if time.time() - self._last_bybit_err_ts >= max(ALERT_COOLDOWN_SEC, 120):
            self._last_bybit_err_ts = time.time()
            self.tg_send(msg)

    # -------------------------
    # Bybit signing (v5)
    # -------------------------
    def _safe_json(self, r: requests.Response):
        text = r.text or ""
        if not text.strip():
            return {"_non_json": True, "raw": "", "status": r.status_code}
        try:
            return r.json()
        except Exception:
            return {"_non_json": True, "raw": text[:800], "status": r.status_code}

    def _sign_post(self, body: dict):
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

    def _sign_get(self, params: dict):
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

    def _bybit_get(self, path: str, params: dict):
        if DRY_RUN:
            return {"retCode": 0, "retMsg": "DRY_RUN", "result": {}}

        h, query = self._sign_get(params)
        url = BYBIT_BASE_URL + path + ("?" + query if query else "")
        r = requests.get(url, headers=h, timeout=15, proxies=PROXIES)
        data = self._safe_json(r)

        if r.status_code == 403:
            raise Exception(f"Bybit 403 blocked. base={BYBIT_BASE_URL} proxy={'ON' if PROXIES else 'OFF'} raw={data.get('raw')}")
        if r.status_code == 407:
            raise Exception("Proxy auth failed (407). íë¡ì ìì´ë/ë¹ë² íì¸")
        if data.get("_non_json"):
            raise Exception(f"Bybit non-JSON status={data.get('status')} raw={data.get('raw')}")
        return data

    def _bybit_post(self, path: str, body: dict):
        if DRY_RUN:
            return {"retCode": 0, "retMsg": "DRY_RUN", "result": {}}

        h, b = self._sign_post(body)
        url = BYBIT_BASE_URL + path
        r = requests.post(url, headers=h, data=b, timeout=15, proxies=PROXIES)
        data = self._safe_json(r)

        if r.status_code == 403:
            raise Exception(f"Bybit 403 blocked. base={BYBIT_BASE_URL} proxy={'ON' if PROXIES else 'OFF'} raw={data.get('raw')}")
        if r.status_code == 407:
            raise Exception("Proxy auth failed (407). íë¡ì ìì´ë/ë¹ë² íì¸")
        if data.get("_non_json"):
            raise Exception(f"Bybit non-JSON status={data.get('status')} raw={data.get('raw')}")
        return data

    # -------------------------
    # Market data
    # -------------------------
    def get_last_price(self) -> float:
        res = self._bybit_get("/v5/market/tickers", {"category": CATEGORY, "symbol": SYMBOL})
        if res.get("retCode") != 0:
            raise Exception(f"tickers retCode={res.get('retCode')} retMsg={res.get('retMsg')}")
        lst = (((res.get("result") or {}).get("list")) or [])
        if not lst:
            raise Exception("tickers empty")
        t = lst[0]
        p = t.get("markPrice") or t.get("lastPrice")
        return float(p)

    def get_klines(self, interval=None, limit=None):
        interval = str(interval or ENTRY_INTERVAL)
        limit = int(limit or KLINE_LIMIT)
        res = self._bybit_get("/v5/market/kline", {"category": CATEGORY, "symbol": SYMBOL, "interval": interval, "limit": limit})
        if res.get("retCode") != 0:
            raise Exception(f"kline retCode={res.get('retCode')} retMsg={res.get('retMsg')}")
        return (res.get("result") or {}).get("list") or []

    # instruments rules
    def get_instrument_rules(self, force=False):
        if (not force) and self._rules_cache and (time.time() - self._rules_cache_ts < 900):
            return self._rules_cache

        res = self._bybit_get("/v5/market/instruments-info", {"category": CATEGORY, "symbol": SYMBOL})
        if res.get("retCode") != 0:
            raise Exception(f"instruments-info retCode={res.get('retCode')} retMsg={res.get('retMsg')}")

        items = (((res.get("result") or {}).get("list")) or [])
        if not items:
            raise Exception("instruments-info empty")

        it = items[0]
        lot = it.get("lotSizeFilter") or {}
        min_qty = float(lot.get("minOrderQty") or 0.0001)
        qty_step = float(lot.get("qtyStep") or 0.0001)

        self._rules_cache = {"min_qty": min_qty, "qty_step": qty_step}
        self._rules_cache_ts = time.time()
        return self._rules_cache

    def _floor_to_step(self, x: float, step: float):
        if step <= 0:
            return x
        return math.floor(x / step) * step

    # -------------------------
    # Indicators
    # -------------------------
    def _ema_series(self, values, period):
        k = 2 / (period + 1)
        e = values[0]
        out = [e]
        for v in values[1:]:
            e = v * k + e * (1 - k)
            out.append(e)
        return out

    def _ema_last(self, values, period):
        return self._ema_series(values, period)[-1]

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains = 0.0
        losses = 0.0
        for i in range(-period, 0):
            d = closes[i] - closes[i - 1]
            if d >= 0:
                gains += d
            else:
                losses -= d
        avg_gain = gains / period
        avg_loss = losses / period
        rs = avg_gain / (avg_loss + 1e-12)
        return 100 - (100 / (1 + rs))

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(-period, 0):
            h = highs[i]
            l = lows[i]
            pc = closes[i - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        return sum(trs) / period

    def _macd(self, closes):
        if len(closes) < MACD_SLOW + MACD_SIGNAL + 5:
            return None
        ema_fast = self._ema_series(closes, MACD_FAST)
        ema_slow = self._ema_series(closes, MACD_SLOW)
        macd_line = [f - s for f, s in zip(ema_fast[-len(ema_slow):], ema_slow)]
        signal = self._ema_series(macd_line, MACD_SIGNAL)
        hist = macd_line[-1] - signal[-1]
        return {"macd": macd_line[-1], "signal": signal[-1], "hist": hist}

    # -------------------------
    # Account / Position
    # -------------------------
    def get_usdt_balance(self) -> float:
        if DRY_RUN:
            return float(self.state.get("paper_usdt", 30.0))

        res = self._bybit_get("/v5/account/wallet-balance", {"accountType": ACCOUNT_TYPE})
        if res.get("retCode") != 0:
            raise Exception(f"wallet-balance retCode={res.get('retCode')} retMsg={res.get('retMsg')}")

        lst = (((res.get("result") or {}).get("list")) or [])
        if not lst:
            return 0.0

        coins = (lst[0].get("coin") or [])
        for c in coins:
            if c.get("coin") == "USDT":
                for k in ("availableToWithdraw", "walletBalance", "equity"):
                    v = c.get(k)
                    if v is not None and str(v).strip() != "":
                        return float(v)
        return 0.0

    def get_position_info(self):
        if DRY_RUN:
            # mirror (paper)
            if self.position and self.entry_price:
                side = "Buy" if self.position == "LONG" else "Sell"
                return {"has_pos": True, "side": side, "size": float(self.state.get("last_qty") or 0.0), "avgPrice": float(self.entry_price), "unrealisedPnl": 0.0, "cumRealisedPnl": float(self.day_pnl_usdt)}
            return {"has_pos": False}

        res = self._bybit_get("/v5/position/list", {"category": CATEGORY, "symbol": SYMBOL})
        if res.get("retCode") != 0:
            raise Exception(f"position/list retCode={res.get('retCode')} retMsg={res.get('retMsg')}")

        items = (((res.get("result") or {}).get("list")) or [])
        if not items:
            return {"has_pos": False}

        p = items[0]
        size = float(p.get("size") or 0)
        side = p.get("side")
        avg = p.get("avgPrice") or p.get("entryPrice") or "0"
        upnl = p.get("unrealisedPnl") or 0
        rpnL = p.get("cumRealisedPnl") or p.get("curRealisedPnl") or 0

        return {"has_pos": size > 0, "side": side, "size": size, "avgPrice": float(avg), "unrealisedPnl": float(upnl), "cumRealisedPnl": float(rpnL)}

    def sync_position(self):
        try:
            info = self.get_position_info()
            if info.get("has_pos"):
                if info.get("side") == "Buy":
                    self.position = "LONG"
                    self.entry_side = "Buy"
                else:
                    self.position = "SHORT"
                    self.entry_side = "Sell"
                self.entry_price = float(info.get("avgPrice") or 0.0)
            else:
                self.position = None
                self.entry_side = None
                self.entry_price = None
                self.entry_ts = None
        except Exception as e:
            self.tg_send_bybit_err_throttled(f"â í¬ì§ì ì¡°í ì¤í¨: {e}")

    # -------------------------
    # Orders
    # -------------------------
    def set_leverage(self):
        lev = self._leverage()
        body = {"category": CATEGORY, "symbol": SYMBOL, "buyLeverage": str(lev), "sellLeverage": str(lev)}
        res = self._bybit_post("/v5/position/set-leverage", body)
        self.tg_send(f"âï¸ ë ë²ë¦¬ì§ {lev}x ì¤ì : {res.get('retMsg')} ({res.get('retCode')})")

    def order_market(self, side: str, qty: float, reduce_only=False):
        if DRY_RUN:
            self.tg_send(f"ð§ª DRY_RUN ì£¼ë¬¸: {side} qty={qty} reduceOnly={reduce_only}")
            return {"retCode": 0, "retMsg": "DRY_RUN"}

        body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty), "timeInForce": "IOC"}
        if reduce_only:
            body["reduceOnly"] = True

        res = self._bybit_post("/v5/order/create", body)
        self.tg_send(f"â ì£¼ë¬¸: {res.get('retMsg')} ({res.get('retCode')}) / qty={qty}")
        return res

    # -------------------------
    # Mode params
    # -------------------------
    def _risk(self):
        return RISK_SAFE if self.mode == "SAFE" else RISK_AGGR

    def _leverage(self):
        return LEVERAGE_SAFE if self.mode == "SAFE" else LEVERAGE_AGGR

    def _stop_atr_mult(self):
        return STOP_ATR_MULT_SAFE if self.mode == "SAFE" else STOP_ATR_MULT_AGGR

    def _tp_r_mult(self):
        return TP_R_MULT_SAFE if self.mode == "SAFE" else TP_R_MULT_AGGR

    def _cooldown(self):
        return COOLDOWN_SEC_SAFE if self.mode == "SAFE" else COOLDOWN_SEC_AGGR

    def _max_entries(self):
        return MAX_ENTRIES_PER_DAY_SAFE if self.mode == "SAFE" else MAX_ENTRIES_PER_DAY_AGGR

    def _enter_threshold(self):
        return ENTER_PROB_THRESHOLD_SAFE if self.mode == "SAFE" else ENTER_PROB_THRESHOLD_AGGR

    # -------------------------
    # Daily stats
    # -------------------------
    def _update_day_counter(self):
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._day_key != day_key:
            self._day_key = day_key
            self._day_entries = 0
            self.day_pnl_usdt = 0.0
            self.day_wins = 0
            self.day_losses = 0

    def _maybe_daily_report(self):
        # Send daily report once per UTC day when a new day starts (first tick)
        day_key = self._day_key
        if not day_key:
            return
        if self._last_report_day != day_key:
            self._last_report_day = day_key
            # initial daily report on new day
            self.tg_send_throttled(self.daily_report_text(prefix="ðï¸ New day report"))

    def daily_report_text(self, prefix="ð Daily report"):
        trades = self.day_wins + self.day_losses
        winrate = (self.day_wins / trades * 100) if trades > 0 else 0.0
        return (
            f"{prefix}\n"
            f"mode={self.mode} | DRY_RUN={DRY_RUN}\n"
            f"trades={trades} (W={self.day_wins}, L={self.day_losses}) winrate={winrate:.1f}%\n"
            f"dayPnLâ{self.day_pnl_usdt:.2f} USDT\n"
        )

    # -------------------------
    # Quant scoring engine (probability-like)
    # -------------------------
    def _sigmoid(self, x):
        # stable sigmoid
        if x < -60:
            return 0.0
        if x > 60:
            return 1.0
        return 1.0 / (1.0 + math.exp(-x))

    def score_signals(self, closes, highs, lows, vols):
        """
        Returns:
          {
            "p_long": 0..1,
            "p_short": 0..1,
            "confidence": "LOW/MED/HIGH",
            "reason": "... multiline ...",
            "atr": float,
            "ema_fast": float,
            "ema_slow": float,
            "rsi": float,
            "macd_hist": float,
            "vol_ok": bool,
            "chop_ok": bool
          }
        """
        price = closes[-1]
        ema_fast = self._ema_last(closes[-(EMA_FAST * 6):], EMA_FAST)
        ema_slow = self._ema_last(closes[-(EMA_SLOW * 6):], EMA_SLOW)
        rsi = self._rsi(closes, RSI_PERIOD)
        atr = self._atr(highs, lows, closes, ATR_PERIOD)
        macd = self._macd(closes)
        macd_hist = macd["hist"] if macd else 0.0

        # features (normalized)
        trend = (price - ema_slow) / max(atr or (price * 0.001), 1e-9)
        pullback = (price - ema_fast) / max(atr or (price * 0.001), 1e-9)

        # momentum: last close change vs atr
        mom = (closes[-1] - closes[-2]) / max(atr or (price * 0.001), 1e-9)

        # volume spike filter (avoid fakeouts)
        vol_ok = True
        if len(vols) >= 40:
            v_now = vols[-1]
            v_avg = sum(vols[-40:-1]) / max(len(vols[-40:-1]), 1)
            if v_avg > 0 and v_now > v_avg * VOL_SPIKE_MULT:
                # huge spike can be news -> too risky (esp. SAFE)
                if self.mode == "SAFE":
                    vol_ok = False

        # chop filter (avoid high volatility in percent)
        chop_ok = True
        if atr is not None:
            atr_pct = atr / max(price, 1e-9) * 100
            if atr_pct > MAX_VOLATILITY_PCT:
                chop_ok = False if self.mode == "SAFE" else True

        # Build linear score (logit)
        # Positive => LONG, Negative => SHORT
        # Keep it explainable weights
        rsi_term = 0.0
        if rsi is not None:
            # rsi around 50 neutral
            rsi_term = (50.0 - rsi) / 10.0  # >0 means oversold (LONG bias), <0 overbought (SHORT bias)
        macd_term = macd_hist / max(atr or (price * 0.001), 1e-9)

        # Pullback: in uptrend prefer mild pullback for LONG; in downtrend prefer pullback for SHORT
        # We'll encode via trend and pullback interactions
        logit = 0.0
        logit += 0.85 * trend            # trend strength
        logit += -0.35 * pullback        # pullback down from ema_fast favors LONG (negative pullback)
        logit += 0.25 * mom              # momentum continuation
        logit += 0.20 * macd_term        # macd histogram
        logit += 0.18 * rsi_term         # rsi mean reversion
        # penalties
        if not vol_ok:
            logit *= 0.55
        if not chop_ok:
            logit *= 0.65

        p_long = self._sigmoid(logit)
        p_short = 1.0 - p_long

        # Confidence heuristic
        edge = abs(p_long - 0.5) * 2  # 0..1
        confidence = "LOW"
        if edge >= 0.40:
            confidence = "HIGH"
        elif edge >= 0.25:
            confidence = "MEDIUM"

        # Reasons
        reasons = []
        reasons.append(f"price={price:.2f} ema{EMA_FAST}={ema_fast:.2f} ema{EMA_SLOW}={ema_slow:.2f}")
        if rsi is not None:
            reasons.append(f"rsi{RSI_PERIOD}={rsi:.1f} (50 neutral)")
        if atr is not None:
            reasons.append(f"atr{ATR_PERIOD}={atr:.2f} (atr%â{(atr/price*100):.2f}%)")
        if macd:
            reasons.append(f"macd_hist={macd_hist:.4f}")
        reasons.append(f"trend_norm={trend:.2f} pullback_norm={pullback:.2f} mom_norm={mom:.2f}")
        reasons.append(f"filters: vol_ok={vol_ok} chop_ok={chop_ok}")
        reasons.append(f"AI pLong={p_long*100:.1f}% pShort={p_short*100:.1f}% conf={confidence}")

        return {
            "p_long": p_long,
            "p_short": p_short,
            "confidence": confidence,
            "reason": "\n".join(reasons),
            "atr": atr,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "rsi": rsi,
            "macd_hist": macd_hist,
            "vol_ok": vol_ok,
            "chop_ok": chop_ok,
            "price": price,
        }

    # -------------------------
    # Position sizing
    # -------------------------
    def calc_qty(self, usdt_balance: float, price: float, stop_dist: float):
        """
        Linear contract approx:
          loss_at_stop â qty * stop_dist
        risk_budget_usdt = balance * risk
        qty_risk = risk_budget / stop_dist
        also cap by leverage notional: max_qty â (balance * lev)/price
        then fit to step/min qty
        """
        rules = self.get_instrument_rules()
        min_qty = float(rules.get("min_qty") or 0.0001)
        step = float(rules.get("qty_step") or 0.0001)

        risk_budget = max(usdt_balance * self._risk(), 0.0)
        lev = max(self._leverage(), 1)

        if stop_dist <= 0:
            stop_dist = max(price * 0.004, 1.0)

        qty_risk = risk_budget / stop_dist
        max_qty = (usdt_balance * lev) / max(price, 1e-12)
        qty = min(qty_risk, max_qty)

        # minimum notional filter
        if qty * price < MIN_NOTIONAL_USDT_FALLBACK:
            # try min_qty if it meets min notional
            qty = max(qty, min_qty)

        qty = self._floor_to_step(max(qty, 0.0), step)
        if qty < min_qty:
            qty = min_qty

        qty = self._floor_to_step(qty, step)
        return float(f"{qty:.6f}")

    # -------------------------
    # Entry / Exit logic
    # -------------------------
    def decide_entry(self, sig):
        """
        Returns: (decision_side, reason_text, stop_dist, tp_dist)
          decision_side: "Buy" (LONG), "Sell" (SHORT), or None
        """
        p_long = sig["p_long"]
        p_short = sig["p_short"]
        conf = sig["confidence"]
        price = sig["price"]
        atr = sig["atr"] or (price * 0.003)

        thr = self._enter_threshold()

        # mode-specific risk distances
        stop_atr = self._stop_atr_mult()
        tp_r = self._tp_r_mult()
        stop_dist = atr * stop_atr
        tp_dist = stop_dist * tp_r

        if p_long >= thr:
            return "Buy", f"ENTER LONG (p={p_long*100:.1f}%, conf={conf})\n{sig['reason']}", stop_dist, tp_dist
        if p_short >= thr:
            return "Sell", f"ENTER SHORT (p={p_short*100:.1f}%, conf={conf})\n{sig['reason']}", stop_dist, tp_dist
        return None, f"NO ENTRY (thr={thr*100:.0f}%)\n{sig['reason']}", stop_dist, tp_dist

    def decide_exit(self, sig, entry_price, side, stop_dist, tp_dist):
        """
        AI-ish exit:
        - hard stop & take profit from ATR distances
        - early exit if probability flips strongly against position
        """
        price = sig["price"]
        p_long = sig["p_long"]
        p_short = sig["p_short"]
        conf = sig["confidence"]

        if side == "Buy":
            stop_price = entry_price - stop_dist
            tp_price = entry_price + tp_dist

            if price <= stop_price:
                return True, f"STOP LONG: price={price:.2f} <= {stop_price:.2f}"
            if price >= tp_price:
                return True, f"TP LONG: price={price:.2f} >= {tp_price:.2f}"

            # early exit: if short prob high with medium/high confidence
            if p_short >= 0.72 and conf in ("MEDIUM", "HIGH"):
                return True, f"EARLY EXIT LONG: pShort={p_short*100:.1f}% conf={conf}"

        else:  # Sell/SHORT
            stop_price = entry_price + stop_dist
            tp_price = entry_price - tp_dist

            if price >= stop_price:
                return True, f"STOP SHORT: price={price:.2f} >= {stop_price:.2f}"
            if price <= tp_price:
                return True, f"TP SHORT: price={price:.2f} <= {tp_price:.2f}"

            if p_long >= 0.72 and conf in ("MEDIUM", "HIGH"):
                return True, f"EARLY EXIT SHORT: pLong={p_long*100:.1f}% conf={conf}"

        return False, ""

    # -------------------------
    # Manual commands
    # -------------------------
    def help_text(self):
        return (
            "ð ëªë ¹ì´\n"
            "/start  ê±°ë ON\n"
            "/stop   ê±°ë OFF\n"
            "/status ìí/ê·¼ê±°/ì¹ë¥ /ììµ\n"
            "/safe   SAFE ëª¨ë\n"
            "/aggressive ê³µê²© ëª¨ë\n"
            "/mode   íì¬ ëª¨ë\n"
            "/buy    ìë LONG\n"
            "/sell   ìë ì²­ì°(íì¬ í¬ì§ì)\n"
            "/panic  ê°ì ì²­ì° + ê±°ëOFF\n"
        )

    def status_text(self):
        trades = self.day_wins + self.day_losses
        winrate = (self.day_wins / trades * 100) if trades > 0 else 0.0
        pinfo = {}
        try:
            pinfo = self.get_position_info()
        except Exception:
            pinfo = {"has_pos": False}

        lines = []
        lines.append(f"ð§  DRY_RUN={DRY_RUN} | ON={self.trading_enabled} | mode={self.mode}")
        lines.append(f"âï¸ lev={self._leverage()} | risk={self._risk()*100:.1f}% | thr={self._enter_threshold()*100:.0f}%")
        lines.append(f"ð bybit_base={BYBIT_BASE_URL} | proxy={'ON' if PROXIES else 'OFF'}")
        if self.state.get("last_price") is not None:
            lines.append(f"ðµ price={self.state.get('last_price'):.2f}")
        if self.state.get("usdt_balance") is not None:
            lines.append(f"ð° USDT={self.state.get('usdt_balance'):.2f}")
        lines.append(f"ð dayPnLâ{self.day_pnl_usdt:.2f} | trades={trades} | winrate={winrate:.1f}% (W={self.day_wins}/L={self.day_losses})")
        if pinfo.get("has_pos"):
            lines.append(f"ð POS={'LONG' if pinfo.get('side')=='Buy' else 'SHORT'} size={pinfo.get('size')} avg={pinfo.get('avgPrice')}")
            lines.append(f"ð uPnL={pinfo.get('unrealisedPnl')} | rPnL={pinfo.get('cumRealisedPnl')}")
        else:
            lines.append("ð POS=None")
        if self.state.get("entry_reason"):
            lines.append("ð§¾ entry_reason:\n" + str(self.state.get("entry_reason"))[:1200])
        if self.state.get("exit_reason"):
            lines.append("ð§¾ exit_reason:\n" + str(self.state.get("exit_reason"))[:600])
        if self.state.get("last_event"):
            lines.append(f"ð last={self.state.get('last_event')}")
        return "\n".join(lines)

    def handle_command(self, text: str):
        cmd = (text or "").strip()

        if cmd == "/start":
            self.trading_enabled = True
            self.tg_send("â ê±°ë ON")
            return

        if cmd == "/stop":
            self.trading_enabled = False
            self.tg_send("ð ê±°ë OFF")
            return

        if cmd == "/safe":
            self.mode = "SAFE"
            self.lev_set = False
            self.tg_send("ð¢ SAFE ëª¨ëë¡ ì í")
            return

        if cmd == "/aggressive":
            self.mode = "AGGRESSIVE"
            self.lev_set = False
            self.tg_send("ð´ AGGRESSIVE ëª¨ëë¡ ì í")
            return

        if cmd == "/mode":
            self.tg_send(f"íì¬ ëª¨ë: {self.mode}")
            return

        if cmd == "/status":
            self.tg_send(self.status_text())
            return

        if cmd in ("/help", "help"):
            self.tg_send(self.help_text())
            return

        if cmd == "/buy":
            # manual LONG
            self.sync_position()
            if self.position is not None:
                self.tg_send("â ï¸ ì´ë¯¸ í¬ì§ì ìì. /status íì¸")
                return
            try:
                price = self.get_last_price()
                bal = self.get_usdt_balance()

                # need indicators to compute stop/tp distances
                kl = self.get_klines(interval=ENTRY_INTERVAL, limit=KLINE_LIMIT)
                kl = list(reversed(kl))
                closes = [float(x[4]) for x in kl]
                highs = [float(x[2]) for x in kl]
                lows = [float(x[3]) for x in kl]
                vols = [float(x[5]) for x in kl]
                sig = self.score_signals(closes, highs, lows, vols)

                atr = sig["atr"] or (price * 0.003)
                stop_dist = atr * self._stop_atr_mult()
                tp_dist = stop_dist * self._tp_r_mult()
                qty = self.calc_qty(bal, price, stop_dist)

                if not self.lev_set:
                    self.set_leverage()
                    self.lev_set = True

                self.state["last_qty"] = qty
                self.state["stop_dist"] = stop_dist
                self.state["tp_dist"] = tp_dist

                self.order_market("Buy", qty)
                self.position = "LONG"
                self.entry_side = "Buy"
                self.entry_price = price
                self.entry_ts = time.time()
                self._cooldown_until = time.time() + self._cooldown()

                self.tg_send(f"ð MANUAL LONG: {price:.2f} qty={qty}\nð§  {sig['reason']}")
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"â /buy ì¤í¨: {e}")
            return

        if cmd == "/sell":
            # manual close (reduce-only)
            self.sync_position()
            if self.position is None:
                self.tg_send("â ï¸ í¬ì§ì ìì")
                return
            try:
                p = self.get_position_info()
                qty = float(p.get("size") or 0.0)
                if qty <= 0:
                    self.tg_send("â ï¸ ì¤ì  í¬ì§ì size=0")
                    return
                side = "Sell" if p.get("side") == "Buy" else "Buy"
                self.order_market(side, qty, reduce_only=True)
                self._on_close(price_hint=None, qty=qty, side_closed=p.get("side"))
                self.tg_send("â ìë ì²­ì° ìë£")
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"â /sell ì¤í¨: {e}")
            return

        if cmd == "/panic":
            # close whatever exists then stop trading
            try:
                p = self.get_position_info()
                qty = float(p.get("size") or 0.0)
                if qty > 0:
                    side = "Sell" if p.get("side") == "Buy" else "Buy"
                    self.order_market(side, qty, reduce_only=True)
                    self._on_close(price_hint=None, qty=qty, side_closed=p.get("side"))
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"â /panic ì¤í¨: {e}")
                return
            self.position = None
            self.entry_price = None
            self.entry_ts = None
            self.entry_side = None
            self.trading_enabled = False
            self.tg_send("ð¨ PANIC: ê°ì ì²­ì° ìë + ê±°ë OFF")
            return

        if cmd.startswith("/"):
            self.tg_send("â ëªë ¹ì ëª¨ë¥´ê² ì. /help")
            return

    # -------------------------
    # Close bookkeeping (approx)
    # -------------------------
    def _on_close(self, price_hint, qty: float, side_closed: str):
        """
        Update day PnL approx when a position closes.
        If we have entry price and current price, compute pnl â (close - entry)*qty (LONG) or reverse for SHORT.
        """
        try:
            if self.entry_price and qty:
                if price_hint is None:
                    try:
                        price_hint = self.get_last_price()
                    except Exception:
                        price_hint = self.entry_price
                pnl = 0.0
                if side_closed == "Buy":  # closing LONG
                    pnl = (price_hint - self.entry_price) * qty
                else:  # closing SHORT
                    pnl = (self.entry_price - price_hint) * qty

                self.day_pnl_usdt += float(pnl)
                if pnl >= 0:
                    self.day_wins += 1
                    self.consec_losses = 0
                else:
                    self.day_losses += 1
                    self.consec_losses += 1
        except Exception:
            pass

        self.position = None
        self.entry_price = None
        self.entry_ts = None
        self.entry_side = None
        self.state["stop_dist"] = 0.0
        self.state["tp_dist"] = 0.0

    # -------------------------
    # Main loop tick
    # -------------------------
    def tick(self):
        # expose state
        self.state["trading_enabled"] = self.trading_enabled
        self.state["mode"] = self.mode
        self.state["bybit_base"] = BYBIT_BASE_URL
        self.state["proxy"] = "ON" if PROXIES else "OFF"

        # daily counters
        self._update_day_counter()
        self._maybe_daily_report()

        if not self.trading_enabled:
            self.state["last_event"] = "ê±°ë OFF"
            return

        if self.consec_losses >= MAX_CONSEC_LOSSES:
            self.tg_send_throttled("ð ì°ì ìì¤ ì í ëë¬ (ê±°ë ì¤ì§)")
            self.trading_enabled = False
            return

        # sync with exchange
        self.sync_position()

        # set leverage (first time or after mode change)
        if not self.lev_set:
            try:
                self.set_leverage()
                self.lev_set = True
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"â ë ë²ë¦¬ì§ ì¤ì  ì¤í¨: {e}")
                return

        # price
        try:
            price = self.get_last_price()
        except Exception as e:
            self.tg_send_bybit_err_throttled(f"â ê°ê²© ì¡°í ì¤í¨: {e}")
            return
        self.state["last_price"] = price

        # balance
        try:
            bal = self.get_usdt_balance()
        except Exception as e:
            self.tg_send_bybit_err_throttled(f"â ìê³  ì¡°í ì¤í¨: {e}")
            return
        self.state["usdt_balance"] = bal

        # market history
        try:
            kl = self.get_klines(interval=ENTRY_INTERVAL, limit=KLINE_LIMIT)
            if not kl:
                self.state["last_event"] = "kline empty"
                return
            kl = list(reversed(kl))
            closes = [float(x[4]) for x in kl]
            highs = [float(x[2]) for x in kl]
            lows = [float(x[3]) for x in kl]
            vols = [float(x[5]) for x in kl]
        except Exception as e:
            self.tg_send_bybit_err_throttled(f"â kline ì¤í¨: {e}")
            return

        sig = self.score_signals(closes, highs, lows, vols)
        self.state["last_decision"] = f"pLong={sig['p_long']*100:.1f} pShort={sig['p_short']*100:.1f} conf={sig['confidence']}"
        self.state["last_event"] = self.state["last_decision"]

        # manage existing position
        if self.position and self.entry_price and self.entry_side:
            stop_dist = float(self.state.get("stop_dist") or 0.0)
            tp_dist = float(self.state.get("tp_dist") or 0.0)
            if stop_dist <= 0 or tp_dist <= 0:
                atr = sig["atr"] or (price * 0.003)
                stop_dist = atr * self._stop_atr_mult()
                tp_dist = stop_dist * self._tp_r_mult()
                self.state["stop_dist"] = stop_dist
                self.state["tp_dist"] = tp_dist

            should, why = self.decide_exit(sig, self.entry_price, self.entry_side, stop_dist, tp_dist)
            if should:
                try:
                    p = self.get_position_info()
                    qty = float(p.get("size") or self.state.get("last_qty") or 0.0)
                    if qty <= 0:
                        qty = float(self.state.get("last_qty") or 0.0)
                    if qty <= 0:
                        self.tg_send_bybit_err_throttled("â ì²­ì°ìëì ëª» êµ¬í¨")
                        return
                    close_side = "Sell" if self.entry_side == "Buy" else "Buy"
                    self.order_market(close_side, qty, reduce_only=True)
                    self.state["exit_reason"] = why
                    self.tg_send(f"ð¤ EXIT {('LONG' if self.entry_side=='Buy' else 'SHORT')}: {why}\nAI: {sig['reason']}")
                    self._on_close(price_hint=price, qty=qty, side_closed=self.entry_side)
                    self._cooldown_until = time.time() + self._cooldown()
                except Exception as e:
                    self.tg_send_bybit_err_throttled(f"â ì²­ì° ì¤í¨: {e}")
                return

            # no exit
            return

        # entry cooldown
        if time.time() < self._cooldown_until:
            self.state["last_event"] = "ëê¸°: cooldown"
            return

        # daily entry limit
        if self._day_entries >= self._max_entries():
            self.state["last_event"] = "ëê¸°: ì¼ì¼ ì§ì ì í"
            return

        # entry decision
        side, reason, stop_dist, tp_dist = self.decide_entry(sig)
        self.state["entry_reason"] = reason

        if side is None:
            # no entry; optionally send only occasionally
            return

        # place entry
        try:
            qty = self.calc_qty(bal, price, stop_dist)
            if qty * price < MIN_NOTIONAL_USDT_FALLBACK:
                self.state["last_event"] = f"ëê¸°: notional too small qty={qty}"
                return

            if not self.lev_set:
                self.set_leverage()
                self.lev_set = True

            self.state["last_qty"] = qty
            self.state["stop_dist"] = stop_dist
            self.state["tp_dist"] = tp_dist

            self.order_market(side, qty)
            self.position = "LONG" if side == "Buy" else "SHORT"
            self.entry_side = side
            self.entry_price = price
            self.entry_ts = time.time()

            self._day_entries += 1
            self._cooldown_until = time.time() + self._cooldown()

            self.tg_send(
                f"ð¥ ENTER {self.position}: price={price:.2f} qty={qty}\n"
                f"ð¯ stopDistâ{stop_dist:.2f} tpDistâ{tp_dist:.2f}\n"
                f"ð§  {reason}"
            )

        except Exception as e:
            self.tg_send_bybit_err_throttled(f"â ì§ì ì£¼ë¬¸ ì¤í¨: {e}")
            self.position = None
            self.entry_price = None
            self.entry_ts = None
            self.entry_side = None

    # -------------------------
    # public state
    # -------------------------
    def public_state(self):
        trades = self.day_wins + self.day_losses
        winrate = (self.day_wins / trades * 100) if trades > 0 else 0.0
        return {
            "dry_run": DRY_RUN,
            "trading_enabled": self.trading_enabled,
            "mode": self.mode,
            "bybit_base": BYBIT_BASE_URL,
            "proxy": "ON" if PROXIES else "OFF",
            "price": self.state.get("last_price"),
            "position": self.position,
            "entry_side": self.entry_side,
            "entry_price": self.entry_price,
            "usdt_balance": self.state.get("usdt_balance"),
            "last_qty": self.state.get("last_qty"),
            "stop_dist": self.state.get("stop_dist"),
            "tp_dist": self.state.get("tp_dist"),
            "consec_losses": self.consec_losses,
            "day_entries": self._day_entries,
            "day_pnl_usdt": self.day_pnl_usdt,
            "day_wins": self.day_wins,
            "day_losses": self.day_losses,
            "day_winrate": winrate,
            "entry_reason": self.state.get("entry_reason"),
            "exit_reason": self.state.get("exit_reason"),
            "last_decision": self.state.get("last_decision"),
            "last_event": self.state.get("last_event"),
        }
