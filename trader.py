# trader.py (FULL COPY-PASTE)
import os
import math
import time
import json
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from datetime import datetime, timezone
from config import *

# ✅ CloudFront/WAF 회피용 기본 헤더
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ✅ Proxy (Render/Railway 환경변수에 HTTPS_PROXY/HTTP_PROXY 넣으면 적용)
PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

# ✅ Bybit base URL
# - 이전 코드처럼 강제로 bytick으로 바꾸면 403(국가차단) 날 수 있음
# - 필요하면 Railway 환경변수 BYBIT_BASE_URL 로 직접 지정
try:
    BYBIT_BASE_URL = (os.getenv("BYBIT_BASE_URL") or BYBIT_BASE_URL or "").strip()
except:
    BYBIT_BASE_URL = "https://api.bybit.com"

if not BYBIT_BASE_URL:
    BYBIT_BASE_URL = "https://api.bybit.com"
BYBIT_BASE_URL = BYBIT_BASE_URL.rstrip("/")

# -------------------------
# 안전하게 config 기본값 제공
# -------------------------
def _cfg(name, default):
    try:
        return globals()[name]
    except KeyError:
        return default
    except Exception:
        return default

# =========================
# 전략/거래 파라미터
# =========================
ENTRY_INTERVAL = str(_cfg("ENTRY_INTERVAL", "15"))
KLINE_LIMIT = int(_cfg("KLINE_LIMIT", 240))
EMA_FAST = int(_cfg("EMA_FAST", 20))
EMA_SLOW = int(_cfg("EMA_SLOW", 50))

RSI_PERIOD = int(_cfg("RSI_PERIOD", 14))
RSI_MAX = float(_cfg("RSI_MAX", 65.0))

PULLBACK_BPS = float(_cfg("PULLBACK_BPS", 20.0))
CONFIRM_UP = bool(_cfg("CONFIRM_UP", True))

ATR_PERIOD = int(_cfg("ATR_PERIOD", 14))
STOP_ATR_MULT = float(_cfg("STOP_ATR_MULT", 1.6))
TP_R_MULT = float(_cfg("TP_R_MULT", 1.5))

COOLDOWN_SEC = int(_cfg("COOLDOWN_SEC", 60 * 20))
MAX_ENTRIES_PER_DAY = int(_cfg("MAX_ENTRIES_PER_DAY", 6))

# ✅ 주문 크기 (USDT 기준) : 30USDT 계좌면 5~10 정도가 현실적
ORDER_USDT = float(os.getenv("ORDER_USDT", str(_cfg("ORDER_USDT", 6.0))))

# ✅ 최소 주문(안전 fallback)
MIN_QTY_FALLBACK = float(_cfg("MIN_QTY", 0.0001))
MIN_NOTIONAL_USDT_FALLBACK = float(_cfg("MIN_NOTIONAL_USDT", 5.0))  # 보수적 기본

# =========================
# Trader
# =========================
class Trader:
    def __init__(self, state):
        self.state = state

        self.trading_enabled = _cfg("TRADING_ENABLED_DEFAULT", True)
        self.leverage = int(_cfg("LEVERAGE_DEFAULT", 5))
        self.risk_pct = float(_cfg("RISK_PCT_DEFAULT", 0.20))

        self.position = None
        self.entry_price = None
        self.entry_ts = None
        self.consec_losses = 0
        self.lev_set = False

        self._cooldown_until = 0
        self._day_key = None
        self._day_entries = 0

        self._last_alert_ts = 0
        self._last_bybit_err_ts = 0

        # instrument rules cache
        self._rules_cache = None
        self._rules_cache_ts = 0

    # ---------- Telegram ----------
    def tg_send(self, msg):
        print(msg)
        if _cfg("BOT_TOKEN", "") and _cfg("CHAT_ID", ""):
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data={"chat_id": CHAT_ID, "text": msg},
                    timeout=10,
                )
            except:
                pass

    def tg_send_throttled(self, msg):
        cooldown = int(_cfg("ALERT_COOLDOWN_SEC", 60))
        if time.time() - self._last_alert_ts >= cooldown:
            self._last_alert_ts = time.time()
            self.tg_send(msg)

    def tg_send_bybit_err_throttled(self, msg):
        cooldown = max(int(_cfg("ALERT_COOLDOWN_SEC", 60)), 120)
        if time.time() - self._last_bybit_err_ts >= cooldown:
            self._last_bybit_err_ts = time.time()
            self.tg_send(msg)

    # ---------- Bybit utils ----------
    def _safe_json(self, r: requests.Response):
        text = r.text or ""
        if not text.strip():
            return {"_non_json": True, "raw": "", "status": r.status_code}
        try:
            return r.json()
        except Exception:
            return {"_non_json": True, "raw": text[:500], "status": r.status_code}

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

    def _bybit_post(self, path: str, body: dict):
        if _cfg("DRY_RUN", True):
            return {"retCode": 0, "retMsg": "DRY_RUN", "result": {}}

        h, b = self._sign_post(body)
        url = BYBIT_BASE_URL + path
        r = requests.post(url, headers=h, data=b, timeout=15, proxies=PROXIES)
        data = self._safe_json(r)

        if r.status_code == 403:
            raise Exception(
                f"Bybit 403 blocked. base={BYBIT_BASE_URL} proxy={'ON' if PROXIES else 'OFF'} raw={data.get('raw')}"
            )
        if r.status_code == 407:
            raise Exception("Proxy auth failed (407). 프록시 아이디/비번 확인")
        if data.get("_non_json"):
            raise Exception(
                f"Bybit non-JSON status={data.get('status')} proxy={'ON' if PROXIES else 'OFF'} raw={data.get('raw')}"
            )
        return data

    def _bybit_get(self, path: str, params: dict):
        if _cfg("DRY_RUN", True):
            return {"retCode": 0, "retMsg": "DRY_RUN", "result": {}}

        h, query = self._sign_get(params)
        url = BYBIT_BASE_URL + path + ("?" + query if query else "")
        r = requests.get(url, headers=h, timeout=15, proxies=PROXIES)
        data = self._safe_json(r)

        if r.status_code == 403:
            raise Exception(
                f"Bybit 403 blocked. base={BYBIT_BASE_URL} proxy={'ON' if PROXIES else 'OFF'} raw={data.get('raw')}"
            )
        if r.status_code == 407:
            raise Exception("Proxy auth failed (407). 프록시 아이디/비번 확인")
        if data.get("_non_json"):
            raise Exception(
                f"Bybit non-JSON status={data.get('status')} proxy={'ON' if PROXIES else 'OFF'} raw={data.get('raw')}"
            )
        return data

    # ---------- market data ----------
    def get_last_price_bybit(self):
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
        res = self._bybit_get(
            "/v5/market/kline",
            {"category": CATEGORY, "symbol": SYMBOL, "interval": interval, "limit": limit},
        )
        if res.get("retCode") != 0:
            raise Exception(f"kline retCode={res.get('retCode')} retMsg={res.get('retMsg')}")
        return (res.get("result") or {}).get("list") or []

    # ---------- instruments info (min qty/step) ----------
    def get_instrument_rules(self, force=False):
        # 캐시 10분
        if (not force) and self._rules_cache and (time.time() - self._rules_cache_ts < 600):
            return self._rules_cache

        res = self._bybit_get("/v5/market/instruments-info", {"category": CATEGORY, "symbol": SYMBOL})
        if res.get("retCode") != 0:
            raise Exception(f"instruments-info retCode={res.get('retCode')} retMsg={res.get('retMsg')}")

        items = (((res.get("result") or {}).get("list")) or [])
        if not items:
            raise Exception("instruments-info empty")

        it = items[0]
        lot = it.get("lotSizeFilter") or {}
        min_qty = float(lot.get("minOrderQty") or MIN_QTY_FALLBACK)
        qty_step = float(lot.get("qtyStep") or MIN_QTY_FALLBACK)

        self._rules_cache = {"min_qty": min_qty, "qty_step": qty_step}
        self._rules_cache_ts = time.time()
        return self._rules_cache

    def _floor_to_step(self, x: float, step: float):
        if step <= 0:
            return x
        return math.floor(x / step) * step

    # ---------- indicators ----------
    def _ema(self, values, period):
        k = 2 / (period + 1)
        e = values[0]
        for v in values[1:]:
            e = v * k + e * (1 - k)
        return e

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

    # ---------- balance ----------
    def get_usdt_balance(self):
        if _cfg("DRY_RUN", True):
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

    # ---------- position ----------
    def get_position_info(self):
        if _cfg("DRY_RUN", True):
            if self.position == "LONG" and self.entry_price:
                return {"has_pos": True, "side": "Buy", "size": 1.0, "avgPrice": float(self.entry_price)}
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
        return {"has_pos": size > 0, "side": side, "size": size, "avgPrice": float(avg)}

    def sync_position(self):
        try:
            info = self.get_position_info()
            if info.get("has_pos") and info.get("side") == "Buy":
                self.position = "LONG"
                self.entry_price = float(info.get("avgPrice") or self.entry_price or 0)
            else:
                self.position = None
                self.entry_price = None
                self.entry_ts = None
        except Exception as e:
            self.tg_send_bybit_err_throttled(f"❌ 포지션 조회 실패: {e}")

    # ---------- leverage ----------
    def set_leverage(self):
        body = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "buyLeverage": str(self.leverage),
            "sellLeverage": str(self.leverage),
        }
        res = self._bybit_post("/v5/position/set-leverage", body)
        self.tg_send(f"⚙️ 레버리지 {self.leverage}x 설정: {res.get('retMsg')} ({res.get('retCode')})")

    # ---------- sizing (USDT 기준) ----------
    def calc_qty_by_usdt(self, usdt_balance: float, price: float):
        """
        ORDER_USDT(예: 6) 만큼을 증거금으로 쓰고 레버리지 적용한 notional로 qty 계산.
        최소수량/스텝 자동 맞춤.
        """
        rules = self.get_instrument_rules()
        min_qty = float(rules.get("min_qty") or MIN_QTY_FALLBACK)
        step = float(rules.get("qty_step") or MIN_QTY_FALLBACK)

        # 사용할 증거금: ORDER_USDT (잔고보다 크면 잔고 일부만)
        margin = min(max(ORDER_USDT, 0.0), max(usdt_balance, 0.0))

        # 너무 작은 주문금액이면 패스 (거래소 최소 notional은 심볼/계정마다 달라서 보수적으로)
        if margin < MIN_NOTIONAL_USDT_FALLBACK:
            return 0.0, f"잔고/주문금액 너무 작음: margin={margin:.2f} < {MIN_NOTIONAL_USDT_FALLBACK}"

        notional = margin * max(self.leverage, 1)
        qty = notional / max(price, 1e-12)

        # step 내림
        qty = self._floor_to_step(qty, step)

        # 최소수량 보정
        if qty < min_qty:
            # min_qty로 올려보고, 그때 필요한 증거금이 잔고 내인지 체크
            need_notional = min_qty * price
            need_margin = need_notional / max(self.leverage, 1)
            if need_margin > usdt_balance:
                return 0.0, f"최소수량 충족 불가: need_margin≈{need_margin:.2f} > balance={usdt_balance:.2f}"
            qty = min_qty
            qty = self._floor_to_step(qty, step)

        return float(f"{qty:.6f}"), f"qty OK (min={min_qty}, step={step})"

    # ---------- anti-overtrade ----------
    def _update_day_counter(self):
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._day_key != day_key:
            self._day_key = day_key
            self._day_entries = 0

    # ---------- entry signal ----------
    def should_enter_long(self):
        kl = self.get_klines(interval=ENTRY_INTERVAL, limit=KLINE_LIMIT)
        if len(kl) < max(EMA_SLOW * 3, 120):
            return (False, "kline 부족")

        kl = list(reversed(kl))
        closes = [float(x[4]) for x in kl]
        highs = [float(x[2]) for x in kl]
        lows = [float(x[3]) for x in kl]

        price = closes[-1]
        ema20 = self._ema(closes[-(EMA_FAST * 6):], EMA_FAST)
        ema50 = self._ema(closes[-(EMA_SLOW * 6):], EMA_SLOW)
        rsi = self._rsi(closes, RSI_PERIOD)
        atr = self._atr(highs, lows, closes, ATR_PERIOD)

        if rsi is None or atr is None:
            return (False, "지표 계산 불가")

        if price <= ema50:
            return (False, f"NO: 추세필터(가격<=EMA{EMA_SLOW}) price={price:.2f} ema50={ema50:.2f}")

        pullback_tol = ema20 * (PULLBACK_BPS / 10000.0)
        if abs(price - ema20) > pullback_tol:
            return (False, f"NO: 풀백아님(EMA{EMA_FAST} 근처만) price={price:.2f} ema20={ema20:.2f} tol≈{pullback_tol:.2f}")

        if rsi >= RSI_MAX:
            return (False, f"NO: RSI 과열회피 rsi={rsi:.1f} >= {RSI_MAX}")

        if CONFIRM_UP and closes[-1] <= closes[-2]:
            return (False, f"NO: 확인실패(종가상승 아님) c1={closes[-2]:.2f} c2={closes[-1]:.2f}")

        stop_dist = atr * STOP_ATR_MULT
        tp_dist = stop_dist * TP_R_MULT

        reason = (
            f"ENTER: EMA{EMA_SLOW} 상단 + EMA{EMA_FAST} 풀백 + RSI과열X | "
            f"price={price:.2f} ema20={ema20:.2f} ema50={ema50:.2f} rsi={rsi:.1f} atr={atr:.2f} | "
            f"stop≈{stop_dist:.2f} tp≈{tp_dist:.2f}"
        )
        return (True, reason, stop_dist, tp_dist)

    # ---------- status/help ----------
    def help_text(self):
        return (
            "📌 명령어\n"
            "/start  거래 ON\n"
            "/stop   거래 OFF\n"
            "/status 상태\n"
            "/buy    수동 LONG\n"
            "/sell   수동 청산\n"
            "/panic  강제청산 + OFF\n"
            "/risk 0.2  (리스크%)\n"
            "/lev 5     (레버리지)\n"
        )

    def status_text(self):
        lines = []
        lines.append(f"🧠 DRY_RUN={_cfg('DRY_RUN', True)} | ON={self.trading_enabled}")
        lines.append(f"⚙️ lev={self.leverage} | risk={self.risk_pct} | ORDER_USDT={ORDER_USDT}")
        lines.append(f"🌐 bybit_base={BYBIT_BASE_URL} | proxy={'ON' if PROXIES else 'OFF'}")
        if self.state.get("last_price") is not None:
            lines.append(f"💵 price={self.state.get('last_price'):.2f}")
        if self.state.get("usdt_balance") is not None:
            lines.append(f"💰 USDT={self.state.get('usdt_balance'):.2f}")
        lines.append(f"📍 POS={self.position or 'None'} entry={self.entry_price}")
        if self.state.get("entry_reason"):
            lines.append(f"🧠 근거: {self.state.get('entry_reason')}")
        if self.state.get("last_event"):
            lines.append(f"📝 last={self.state.get('last_event')}")
        return "\n".join(lines)

    # ---------- telegram command handler ----------
    def handle_command(self, text: str):
        cmd = (text or "").strip()

        if cmd == "/start":
            self.trading_enabled = True
            self.tg_send("✅ 거래 ON")
            return

        if cmd == "/stop":
            self.trading_enabled = False
            self.tg_send("🛑 거래 OFF")
            return

        if cmd == "/status":
            self.tg_send(self.status_text())
            return

        if cmd in ("/help", "help"):
            self.tg_send(self.help_text())
            return

        if cmd.startswith("/risk "):
            try:
                v = float(cmd.split()[1])
                if not (0.01 <= v <= 1.0):
                    self.tg_send("❌ risk 범위: 0.01 ~ 1.0")
                    return
                self.risk_pct = v
                self.tg_send(f"✅ RISK_PCT 변경: {self.risk_pct}")
            except:
                self.tg_send("❌ 사용법: /risk 0.2")
            return

        if cmd.startswith("/lev "):
            try:
                v = int(cmd.split()[1])
                if not (1 <= v <= 20):
                    self.tg_send("❌ lev 범위: 1 ~ 20")
                    return
                self.leverage = v
                self.lev_set = False
                self.tg_send(f"✅ LEVERAGE 변경: {self.leverage} (다음 루프 적용)")
            except:
                self.tg_send("❌ 사용법: /lev 5")
            return

        if cmd == "/buy":
            self.sync_position()
            if self.position is not None:
                self.tg_send("⚠️ 이미 포지션 있음. /status 확인")
                return
            try:
                price = self.get_last_price_bybit()
                bal = self.get_usdt_balance()

                qty, why = self.calc_qty_by_usdt(bal, price)
                if qty <= 0:
                    self.tg_send(f"❌ 주문불가: {why}")
                    return

                if not self.lev_set:
                    self.set_leverage()
                    self.lev_set = True

                self.order_market("Buy", qty)
                self.position = "LONG"
                self.entry_price = price
                self.entry_ts = time.time()
                self._cooldown_until = time.time() + COOLDOWN_SEC
                self.tg_send(f"📈 수동 LONG: {price:.2f} / qty={qty}\n✅ {why}")
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ /buy 실패: {e}")
            return

        if cmd == "/sell":
            self.sync_position()
            if self.position is None:
                self.tg_send("⚠️ 포지션 없음")
                return
            try:
                p = self.get_position_info()
                qty = float(p.get("size") or 0.0)
                if qty <= 0:
                    self.tg_send("⚠️ 실제 포지션 size=0")
                    return
                self.order_market("Sell", qty, reduce_only=True)
                self.position = None
                self.entry_price = None
                self.entry_ts = None
                self.tg_send("✅ 수동 청산 완료")
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ /sell 실패: {e}")
            return

        if cmd == "/panic":
            try:
                p = self.get_position_info()
                qty = float(p.get("size") or 0.0)
                if qty > 0:
                    self.order_market("Sell", qty, reduce_only=True)
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ /panic 실패: {e}")
                return
            self.position = None
            self.entry_price = None
            self.entry_ts = None
            self.trading_enabled = False
            self.tg_send("🚨 PANIC: 강제청산 시도 + 거래 OFF")
            return

        if cmd.startswith("/"):
            self.tg_send("❓ 명령을 모르겠음. /help")
            return

    # ---------- order ----------
    def order_market(self, side: str, qty: float, reduce_only=False):
        if _cfg("DRY_RUN", True):
            self.tg_send(f"🧪 테스트 주문: {side} qty={qty} reduceOnly={reduce_only}")
            return {"retCode": 0, "retMsg": "DRY_RUN"}

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

        res = self._bybit_post("/v5/order/create", body)
        self.tg_send(f"✅ 주문: {res.get('retMsg')} ({res.get('retCode')}) / qty={qty}")
        return res

    # ---------- strategy loop ----------
    def tick(self):
        self.state["trading_enabled"] = self.trading_enabled
        self.state["leverage"] = self.leverage
        self.state["risk_pct"] = self.risk_pct
        self.state["bybit_base"] = BYBIT_BASE_URL
        self.state["proxy"] = "ON" if PROXIES else "OFF"
        self.state["order_usdt"] = ORDER_USDT

        if not self.trading_enabled:
            self.state["last_event"] = "거래 OFF"
            return

        if self.consec_losses >= int(_cfg("MAX_CONSEC_LOSSES", 3)):
            self.tg_send_throttled("🛑 연속 손실 제한 도달 (거래 중지)")
            self.trading_enabled = False
            return

        self._update_day_counter()
        self.sync_position()

        if not self.lev_set:
            try:
                self.set_leverage()
                self.lev_set = True
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ 레버리지 설정 실패: {e}")
                return

        try:
            price = self.get_last_price_bybit()
        except Exception as e:
            self.tg_send_bybit_err_throttled(f"❌ 가격(Bybit) 조회 실패: {e}")
            return

        self.state["last_price"] = price
        self.state["last_event"] = f"Price: {price:.2f}"

        try:
            usdt_balance = self.get_usdt_balance()
        except Exception as e:
            self.tg_send_bybit_err_throttled(f"❌ 잔고 조회 실패: {e}")
            return

        self.state["usdt_balance"] = usdt_balance

        # 진입 로직
        if self.position is None and self.entry_price is None:
            if time.time() < self._cooldown_until:
                self.state["last_event"] = "대기: cooldown"
                return

            if self._day_entries >= MAX_ENTRIES_PER_DAY:
                self.state["last_event"] = "대기: 일일 진입 제한"
                return

            try:
                out = self.should_enter_long()
                if not out[0]:
                    self.state["entry_reason"] = out[1]
                    self.state["last_event"] = "대기: " + out[1]
                    return

                _, reason, stop_dist, tp_dist = out
                self.state["entry_reason"] = reason

                qty, why = self.calc_qty_by_usdt(usdt_balance, price)
                if qty <= 0:
                    self.state["last_event"] = "대기: " + why
                    return

                self.state["last_qty"] = qty
                self.state["stop_dist"] = stop_dist
                self.state["tp_dist"] = tp_dist

                try:
                    self.order_market("Buy", qty)
                    self.position = "LONG"
                    self.entry_price = price
                    self.entry_ts = time.time()

                    self._day_entries += 1
                    self._cooldown_until = time.time() + COOLDOWN_SEC

                    self.tg_send(f"📈 LONG 진입: {price:.2f} / qty={qty}\n🧠 근거: {reason}\n✅ {why}")
                except Exception as e:
                    self.position = None
                    self.entry_price = None
                    self.entry_ts = None
                    self.tg_send_bybit_err_throttled(f"❌ 주문 실패: {e}")
                return

            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ 진입 판단 실패: {e}")
                return

        # 관리 로직 (ATR 기반)
        if self.position == "LONG" and self.entry_price:
            stop_dist = float(self.state.get("stop_dist") or 0.0)
            tp_dist = float(self.state.get("tp_dist") or 0.0)

            p = None
            try:
                p = self.get_position_info()
            except:
                p = None

            qty = float((p or {}).get("size") or self.state.get("last_qty") or MIN_QTY_FALLBACK)

            if stop_dist <= 0:
                stop_dist = max(self.entry_price * 0.005, 1.0)
            if tp_dist <= 0:
                tp_dist = stop_dist * TP_R_MULT

            stop_price = self.entry_price - stop_dist
            tp_price = self.entry_price + tp_dist

            if price <= stop_price:
                self.tg_send(f"🛑 손절: price={price:.2f} <= stop={stop_price:.2f}")
                try:
                    self.order_market("Sell", qty, reduce_only=True)
                except Exception as e:
                    self.tg_send_bybit_err_throttled(f"❌ 손절 실패: {e}")
                    return
                self.position = None
                self.entry_price = None
                self.entry_ts = None
                self.consec_losses += 1
                self._cooldown_until = time.time() + COOLDOWN_SEC
                return

            if price >= tp_price:
                self.tg_send(f"💰 익절: price={price:.2f} >= tp={tp_price:.2f}")
                try:
                    self.order_market("Sell", qty, reduce_only=True)
                except Exception as e:
                    self.tg_send_bybit_err_throttled(f"❌ 익절 실패: {e}")
                    return
                self.position = None
                self.entry_price = None
                self.entry_ts = None
                self.consec_losses = 0
                self._cooldown_until = time.time() + COOLDOWN_SEC
                return

    def public_state(self):
        return {
            "dry_run": _cfg("DRY_RUN", True),
            "trading_enabled": self.trading_enabled,
            "leverage": self.leverage,
            "risk_pct": self.risk_pct,
            "bybit_base": BYBIT_BASE_URL,
            "proxy": "ON" if PROXIES else "OFF",
            "order_usdt": ORDER_USDT,
            "price": self.state.get("last_price"),
            "position": self.position,
            "entry_price": self.entry_price,
            "usdt_balance": self.state.get("usdt_balance"),
            "last_qty": self.state.get("last_qty"),
            "stop_dist": self.state.get("stop_dist"),
            "tp_dist": self.state.get("tp_dist"),
            "consec_losses": self.consec_losses,
            "day_entries": self._day_entries,
            "cooldown_until": self._cooldown_until,
            "entry_reason": self.state.get("entry_reason"),
            "last_event": self.state.get("last_event"),
        }
