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

# ✅ Bybit 차단 회피 도메인 강제 치환 (LTE 403 대응)
try:
    BYBIT_BASE_URL = (BYBIT_BASE_URL or "").strip()
except:
    BYBIT_BASE_URL = "https://api.bybit.com"

if "api.bybit.com" in BYBIT_BASE_URL:
    BYBIT_BASE_URL = BYBIT_BASE_URL.replace("https://api.bybit.com", "https://api.bytick.com").replace(
        "http://api.bybit.com", "https://api.bytick.com"
    )

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

# 전략 파라미터(안정형 기본)
ENTRY_INTERVAL = str(_cfg("ENTRY_INTERVAL", "15"))          # 캔들 주기(분): "1","3","5","15","30","60"...
KLINE_LIMIT = int(_cfg("KLINE_LIMIT", 240))                # 캔들 개수(충분히 크게)
EMA_FAST = int(_cfg("EMA_FAST", 20))
EMA_SLOW = int(_cfg("EMA_SLOW", 50))

RSI_PERIOD = int(_cfg("RSI_PERIOD", 14))
RSI_MAX = float(_cfg("RSI_MAX", 65.0))                     # 과열이면 진입 금지

PULLBACK_BPS = float(_cfg("PULLBACK_BPS", 20.0))           # EMA20 근처 눌림 허용폭( basis points ) 20bps=0.2%
CONFIRM_UP = bool(_cfg("CONFIRM_UP", True))                # 직전 캔들보다 종가 상승 확인

ATR_PERIOD = int(_cfg("ATR_PERIOD", 14))
STOP_ATR_MULT = float(_cfg("STOP_ATR_MULT", 1.6))          # 손절폭 = ATR * 배수
TP_R_MULT = float(_cfg("TP_R_MULT", 1.5))                  # 익절폭 = 손절폭 * R배수 (RR)

COOLDOWN_SEC = int(_cfg("COOLDOWN_SEC", 60 * 20))          # 진입 후 최소 대기(기본 20분)
MAX_ENTRIES_PER_DAY = int(_cfg("MAX_ENTRIES_PER_DAY", 6))  # 하루 최대 진입 횟수

# 최소 주문 수량(거래소/심볼별 상이)
MIN_QTY = float(_cfg("MIN_QTY", 0.0001))

# -------------------------
# Trader
# -------------------------
class Trader:
    """
    ✅ 안정형 근거 기반 봇
    - EMA50 추세필터 + EMA20 되돌림 + RSI 과열 회피
    - ATR 기반 손절폭 + 리스크%로 수량 계산(포지션 사이징)
    - Bybit 캔들/가격을 Bybit에서 직접 사용(가격 기준 통일)
    - 텔레그램 명령: /start /stop /status /buy /sell /panic /risk /lev
    """

    def __init__(self, state):
        self.state = state

        self.trading_enabled = _cfg("TRADING_ENABLED_DEFAULT", True)
        self.leverage = int(_cfg("LEVERAGE_DEFAULT", 3))
        self.risk_pct = float(_cfg("RISK_PCT_DEFAULT", 0.10))

        # 내부 상태
        self.position = None         # "LONG" or None
        self.entry_price = None
        self.entry_ts = None
        self.consec_losses = 0
        self.lev_set = False

        # 과매매 방지
        self._cooldown_until = 0
        self._day_key = None
        self._day_entries = 0

        # 스팸 방지
        self._last_alert_ts = 0
        self._last_bybit_err_ts = 0

        # 마지막 전략 설명
        self.last_reason = ""

    # ---------- Telegram send ----------
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

    # ---------- Bybit signing (v5) ----------
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
        r = requests.post(url, headers=h, data=b, timeout=15)
        data = self._safe_json(r)

        if r.status_code == 403:
            raise Exception(f"Bybit 403 blocked. base={BYBIT_BASE_URL} raw={data.get('raw')}")
        if data.get("_non_json"):
            raise Exception(f"Bybit non-JSON status={data.get('status')} raw={data.get('raw')}")
        return data

    def _bybit_get(self, path: str, params: dict):
        if _cfg("DRY_RUN", True):
            return {"retCode": 0, "retMsg": "DRY_RUN", "result": {}}
        h, query = self._sign_get(params)
        url = BYBIT_BASE_URL + path + ("?" + query if query else "")
        r = requests.get(url, headers=h, timeout=15)
        data = self._safe_json(r)

        if r.status_code == 403:
            raise Exception(f"Bybit 403 blocked. base={BYBIT_BASE_URL} raw={data.get('raw')}")
        if data.get("_non_json"):
            raise Exception(f"Bybit non-JSON status={data.get('status')} raw={data.get('raw')}")
        return data

    # ---------- market data (Bybit) ----------
    def get_last_price_bybit(self):
        # ✅ 가격 기준을 Bybit로 통일
        res = self._bybit_get("/v5/market/tickers", {"category": CATEGORY, "symbol": SYMBOL})
        if res.get("retCode") != 0:
            raise Exception(f"tickers retCode={res.get('retCode')} retMsg={res.get('retMsg')}")
        lst = (((res.get("result") or {}).get("list")) or [])
        if not lst:
            raise Exception("tickers empty")
        t = lst[0]
        # lastPrice / markPrice 둘 다 오는데, 보수적으로 markPrice 우선
        p = t.get("markPrice") or t.get("lastPrice")
        return float(p)

    def get_klines(self, interval=None, limit=None):
        interval = str(interval or ENTRY_INTERVAL)
        limit = int(limit or KLINE_LIMIT)
        res = self._bybit_get("/v5/market/kline", {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "interval": interval,
            "limit": limit
        })
        if res.get("retCode") != 0:
            raise Exception(f"kline retCode={res.get('retCode')} retMsg={res.get('retMsg')}")
        # list item: [start, open, high, low, close, volume, turnover]
        return (res.get("result") or {}).get("list") or []

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

    # ---------- sizing (ATR 기반) ----------
    def calc_qty_by_risk(self, usdt_balance: float, price: float, stop_dist: float):
        """
        선물(Linear) 기준:
        - 손절 시 손실 = qty * stop_dist
        - 목표 리스크(USDT) = balance * risk_pct
        => qty_risk = risk_usdt / stop_dist
        - 단, 레버리지 한도에 따른 최대 notional도 고려
        """
        risk_usdt = max(usdt_balance * self.risk_pct, 0.0)
        if stop_dist <= 0:
            return MIN_QTY

        qty_risk = risk_usdt / stop_dist

        # 레버리지 한도 기준 최대 수량(대략): (balance * leverage) / price
        max_qty_by_lev = (usdt_balance * max(self.leverage, 1)) / max(price, 1e-12)

        qty = min(qty_risk, max_qty_by_lev)
        qty = max(qty, MIN_QTY)
        return float(f"{qty:.6f}")

    # ---------- order ----------
    def order_market(self, side: str, qty: float, reduce_only=False):
        if _cfg("DRY_RUN", True):
            self.tg_send(f"🧪 테스트 주문: {side} qty={qty} reduceOnly={reduce_only}")
            return {"retCode": 0, "retMsg": "DRY_RUN"}

        body = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "side": side,          # "Buy" / "Sell"
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "IOC",
        }
        if reduce_only:
            body["reduceOnly"] = True

        res = self._bybit_post("/v5/order/create", body)
        self.tg_send(f"✅ 주문: {res.get('retMsg')} ({res.get('retCode')}) / qty={qty}")
        return res

    # ---------- anti-overtrade ----------
    def _update_day_counter(self):
        # UTC 기준 하루 카운트(서버/배포 환경에 안전)
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._day_key != day_key:
            self._day_key = day_key
            self._day_entries = 0

    # ---------- entry signal ----------
    def should_enter_long(self):
        """
        ✅ 근거(정석)
        1) EMA50 위 = 상승 추세일 때만 롱
        2) EMA20 근처로 눌림(Pullback)일 때만
        3) RSI 과열이면 진입 금지
        4) (옵션) 직전 캔들 대비 종가 상승 확인
        """
        kl = self.get_klines(interval=ENTRY_INTERVAL, limit=KLINE_LIMIT)
        if len(kl) < max(EMA_SLOW * 3, 120):
            return (False, "kline 부족")

        # Bybit는 최신이 앞에 오기도 해서, 시간순으로 정렬되게 뒤집기
        kl = list(reversed(kl))

        closes = [float(x[4]) for x in kl]
        highs  = [float(x[2]) for x in kl]
        lows   = [float(x[3]) for x in kl]

        price = closes[-1]

        ema20 = self._ema(closes[-(EMA_FAST*6):], EMA_FAST)
        ema50 = self._ema(closes[-(EMA_SLOW*6):], EMA_SLOW)
        rsi = self._rsi(closes, RSI_PERIOD)
        atr = self._atr(highs, lows, closes, ATR_PERIOD)

        if rsi is None or atr is None:
            return (False, "지표 계산 불가")

        # 1) 추세 필터
        if price <= ema50:
            return (False, f"NO: 추세필터(가격<=EMA{EMA_SLOW}) price={price:.2f} ema50={ema50:.2f}")

        # 2) 풀백(EMA20 근처)
        pullback_tol = ema20 * (PULLBACK_BPS / 10000.0)
        if abs(price - ema20) > pullback_tol:
            return (False, f"NO: 풀백아님(EMA{EMA_FAST} 근처만) price={price:.2f} ema20={ema20:.2f} tol≈{pullback_tol:.2f}")

        # 3) RSI 과열 회피
        if rsi >= RSI_MAX:
            return (False, f"NO: RSI 과열회피 rsi={rsi:.1f} >= {RSI_MAX}")

        # 4) 확인(상승 캔들)
        if CONFIRM_UP and closes[-1] <= closes[-2]:
            return (False, f"NO: 확인실패(종가상승 아님) c1={closes[-2]:.2f} c2={closes[-1]:.2f}")

        # 손절/익절 거리(ATR 기반)
        stop_dist = atr * STOP_ATR_MULT
        tp_dist = stop_dist * TP_R_MULT

        reason = (
            f"ENTER: 추세(EMA{EMA_SLOW} 상단) + 풀백(EMA{EMA_FAST} 근처) + RSI과열X | "
            f"price={price:.2f} ema20={ema20:.2f} ema50={ema50:.2f} rsi={rsi:.1f} atr={atr:.2f} | "
            f"stop≈{stop_dist:.2f} tp≈{tp_dist:.2f}"
        )
        return (True, reason, stop_dist, tp_dist)

    # ---------- telegram commands ----------
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
            # 수동 진입은 전략 근거 없이도 가능하게(원하면 여기서도 시그널 체크하도록 바꿔줄 수 있음)
            self.sync_position()
            if self.position is not None:
                self.tg_send("⚠️ 이미 포지션 있음. /status 확인")
                return
            try:
                price = self.get_last_price_bybit()
                bal = self.get_usdt_balance()
                # 수동은 ATR 대신 고정 stop을 쓰면 위험해서, 캔들 기반 stop을 계산
                ok = self.should_enter_long()
                if len(ok) >= 4:
                    _, reason, stop_dist, _ = ok
                else:
                    stop_dist = max(price * 0.005, 1.0)  # fallback 0.5%
                    reason = "MANUAL: fallback stop"
                qty = self.calc_qty_by_risk(bal, price, stop_dist)

                if not self.lev_set:
                    self.set_leverage()
                    self.lev_set = True

                self.order_market("Buy", qty)
                self.position = "LONG"
                self.entry_price = price
                self.entry_ts = time.time()
                self._cooldown_until = time.time() + COOLDOWN_SEC
                self.tg_send(f"📈 수동 LONG 진입: {price:.2f} / qty={qty}\n🧠 참고: {reason}")
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ /buy 실패: {e}")
            return

        if cmd == "/sell":
            self.sync_position()
            if self.position is None:
                self.tg_send("⚠️ 포지션 없음")
                return
            try:
                price = self.get_last_price_bybit()
                bal = self.get_usdt_balance()
                # reduceOnly는 수량을 크게 잡아도 실제 포지션만 줄어듦. 그래도 보수적으로 계산
                qty = max(MIN_QTY, float(self.state.get("last_qty") or MIN_QTY))
                self.order_market("Sell", qty, reduce_only=True)
                self.position = None
                self.entry_price = None
                self.entry_ts = None
                self.tg_send(f"✅ 수동 청산 완료 (price={price:.2f})")
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ /sell 실패: {e}")
            return

        if cmd == "/panic":
            try:
                qty = max(MIN_QTY, float(self.state.get("last_qty") or MIN_QTY))
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

        if cmd in ("/help", "help"):
            self.tg_send(self.help_text())
            return

        if cmd.startswith("/"):
            self.tg_send("❓ 명령을 모르겠음. /help")
            return

    def help_text(self):
        return (
            "📌 명령어\n"
            "/start  거래 ON\n"
            "/stop   거래 OFF\n"
            "/status 상태/잔고/포지션/근거\n"
            "/buy    수동 LONG 진입\n"
            "/sell   수동 청산\n"
            "/panic  강제청산 + 거래OFF\n"
            "/risk 0.2  (잔고의 20% 손절리스크)\n"
            "/lev 5     (레버리지)\n"
        )

    def status_text(self):
        lines = []
        lines.append(f"🧠 DRY_RUN={_cfg('DRY_RUN', True)} | ON={self.trading_enabled}")
        lines.append(f"⚙️ lev={self.leverage} | risk={self.risk_pct}")
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

    # ---------- strategy loop ----------
    def tick(self):
        # 상태 노출
        self.state["trading_enabled"] = self.trading_enabled
        self.state["leverage"] = self.leverage
        self.state["risk_pct"] = self.risk_pct
        self.state["bybit_base"] = BYBIT_BASE_URL

        if not self.trading_enabled:
            self.state["last_event"] = "거래 OFF"
            return

        # 연속 손실 제한
        if self.consec_losses >= int(_cfg("MAX_CONSEC_LOSSES", 3)):
            self.tg_send_throttled("🛑 연속 손실 제한 도달 (거래 중지)")
            self.trading_enabled = False
            return

        # 하루 카운터 갱신
        self._update_day_counter()

        # 포지션 동기화
        self.sync_position()

        # 레버리지 설정(최초 1회 or /lev 변경 후)
        if not self.lev_set:
            try:
                self.set_leverage()
                self.lev_set = True
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ 레버리지 설정 실패: {e}")
                return

        # 가격 (Bybit로 통일)
        try:
            price = self.get_last_price_bybit()
        except Exception as e:
            self.tg_send_bybit_err_throttled(f"❌ 가격(Bybit) 조회 실패: {e}")
            return

        self.state["last_price"] = price
        self.state["last_event"] = f"Price: {price:.2f}"

        # 잔고
        try:
            usdt_balance = self.get_usdt_balance()
        except Exception as e:
            self.tg_send_bybit_err_throttled(f"❌ 잔고 조회 실패: {e}")
            return

        self.state["usdt_balance"] = usdt_balance

        # -------------------------
        # 진입 로직 (포지션 없을 때만)
        # -------------------------
        if self.position is None and self.entry_price is None:
            # 과매매 방지(쿨다운)
            if time.time() < self._cooldown_until:
                self.state["last_event"] = "대기: cooldown"
                return

            # 하루 최대 진입 제한
            if self._day_entries >= MAX_ENTRIES_PER_DAY:
                self.state["last_event"] = "대기: 일일 진입 제한"
                return

            # ✅ 근거 기반 진입 판단
            try:
                out = self.should_enter_long()
                if not out[0]:
                    self.state["entry_reason"] = out[1]
                    self.state["last_event"] = "대기: " + out[1]
                    return

                _, reason, stop_dist, tp_dist = out
                self.state["entry_reason"] = reason

                qty = self.calc_qty_by_risk(usdt_balance, price, stop_dist)
                self.state["last_qty"] = qty
                self.state["stop_dist"] = stop_dist
                self.state["tp_dist"] = tp_dist

                # 주문
                try:
                    self.order_market("Buy", qty)
                    self.position = "LONG"
                    self.entry_price = price
                    self.entry_ts = time.time()

                    self._day_entries += 1
                    self._cooldown_until = time.time() + COOLDOWN_SEC

                    self.tg_send(
                        f"📈 LONG 진입: {price:.2f} / qty={qty}\n"
                        f"🧠 근거: {reason}"
                    )
                except Exception as e:
                    self.position = None
                    self.entry_price = None
                    self.entry_ts = None
                    self.tg_send_bybit_err_throttled(f"❌ 주문 실패: {e}")
                return

            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ 진입 판단 실패: {e}")
                return

        # -------------------------
        # 관리 로직 (포지션 있을 때)
        # -------------------------
        if self.position == "LONG" and self.entry_price:
            stop_dist = float(self.state.get("stop_dist") or 0.0)
            tp_dist = float(self.state.get("tp_dist") or 0.0)
            qty = float(self.state.get("last_qty") or MIN_QTY)

            # fallback(혹시 state가 날아가면)
            if stop_dist <= 0:
                stop_dist = max(self.entry_price * 0.005, 1.0)
            if tp_dist <= 0:
                tp_dist = stop_dist * TP_R_MULT

            stop_price = self.entry_price - stop_dist
            tp_price = self.entry_price + tp_dist

            # 손절
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

            # 익절
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
