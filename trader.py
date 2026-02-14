# ===== FULL QUANT ENGINE 1/5 : CONFIG & UTILS =====
import os, time, math, json, hmac, hashlib, requests
from urllib.parse import urlencode
from datetime import datetime, timezone
from config import *

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

def _cfg(name, default):
    try:
        return globals()[name]
    except:
        return default

BYBIT_BASE_URL = (os.getenv("BYBIT_BASE_URL") or _cfg("BYBIT_BASE_URL","https://api.bybit.com")).rstrip("/")

SYMBOL = os.getenv("SYMBOL", _cfg("SYMBOL", "BTCUSDT"))
CATEGORY = os.getenv("CATEGORY", _cfg("CATEGORY", "linear"))
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", _cfg("ACCOUNT_TYPE", "UNIFIED"))

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", _cfg("BYBIT_API_KEY",""))
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", _cfg("BYBIT_API_SECRET",""))

BOT_TOKEN = os.getenv("BOT_TOKEN", _cfg("BOT_TOKEN",""))
CHAT_ID = os.getenv("CHAT_ID", _cfg("CHAT_ID",""))

DRY_RUN = str(os.getenv("DRY_RUN", str(_cfg("DRY_RUN","true")))).lower() in ("1","true","yes","y")

MODE = os.getenv("MODE", "SAFE").upper()  # SAFE / AGGRO
ALLOW_LONG = str(os.getenv("ALLOW_LONG","true")).lower() in ("1","true","yes","y")
ALLOW_SHORT = str(os.getenv("ALLOW_SHORT","true")).lower() in ("1","true","yes","y")

LOOP_SECONDS = int(os.getenv("LOOP_SECONDS", str(_cfg("LOOP_SECONDS", 20))))
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC", str(_cfg("ALERT_COOLDOWN_SEC", 60))))
MAX_CONSEC_LOSSES = int(os.getenv("MAX_CONSEC_LOSSES", str(_cfg("MAX_CONSEC_LOSSES", 3))))

ENTRY_INTERVAL = str(os.getenv("ENTRY_INTERVAL", str(_cfg("ENTRY_INTERVAL","15"))))
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", str(_cfg("KLINE_LIMIT", 240))))
EMA_FAST = int(os.getenv("EMA_FAST", str(_cfg("EMA_FAST", 20))))
EMA_SLOW = int(os.getenv("EMA_SLOW", str(_cfg("EMA_SLOW", 50))))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", str(_cfg("RSI_PERIOD", 14))))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", str(_cfg("ATR_PERIOD", 14))))

LEVERAGE_SAFE = int(os.getenv("LEVERAGE_SAFE", "3"))
LEVERAGE_AGGRO = int(os.getenv("LEVERAGE_AGGRO", "8"))

ORDER_USDT_SAFE = float(os.getenv("ORDER_USDT_SAFE", "5"))
ORDER_USDT_AGGRO = float(os.getenv("ORDER_USDT_AGGRO", "12"))

STOP_ATR_MULT_SAFE = float(os.getenv("STOP_ATR_MULT_SAFE","1.8"))
STOP_ATR_MULT_AGGRO = float(os.getenv("STOP_ATR_MULT_AGGRO","1.3"))

TP_R_MULT_SAFE = float(os.getenv("TP_R_MULT_SAFE","1.5"))
TP_R_MULT_AGGRO = float(os.getenv("TP_R_MULT_AGGRO","2.0"))

TRAIL_ON = str(os.getenv("TRAIL_ON","true")).lower() in ("1","true","yes","y")
TRAIL_ATR_MULT = float(os.getenv("TRAIL_ATR_MULT","1.0"))

TIME_EXIT_MIN = int(os.getenv("TIME_EXIT_MIN","360"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC","1200"))
MAX_ENTRIES_PER_DAY = int(os.getenv("MAX_ENTRIES_PER_DAY","6"))

ENTER_SCORE_SAFE = int(os.getenv("ENTER_SCORE_SAFE","65"))
ENTER_SCORE_AGGRO = int(os.getenv("ENTER_SCORE_AGGRO","55"))
EXIT_SCORE_DROP = int(os.getenv("EXIT_SCORE_DROP","35"))

def _now_utc():
    return datetime.now(timezone.utc)

def _day_key_utc():
    return _now_utc().strftime("%Y-%m-%d")
    # ===== FULL QUANT ENGINE 2/5 : BYBIT API =====
def _safe_json(r: requests.Response):
    text = r.text or ""
    if not text.strip():
        return {"_non_json": True, "raw": "", "status": r.status_code}
    try:
        return r.json()
    except Exception:
        return {"_non_json": True, "raw": text[:800], "status": r.status_code}

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

def bybit_get(path: str, params: dict):
    if DRY_RUN:
        return {"retCode": 0, "retMsg": "DRY_RUN", "result": {}}
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

def get_last_price():
    res = bybit_get("/v5/market/tickers", {"category": CATEGORY, "symbol": SYMBOL})
    lst = (((res.get("result") or {}).get("list")) or [])
    if not lst:
        raise Exception("tickers empty")
    t = lst[0]
    p = t.get("markPrice") or t.get("lastPrice")
    return float(p)

def get_klines(interval: str, limit: int):
    res = bybit_get("/v5/market/kline", {"category": CATEGORY, "symbol": SYMBOL, "interval": str(interval), "limit": int(limit)})
    return (res.get("result") or {}).get("list") or []

def get_wallet_usdt():
    res = bybit_get("/v5/account/wallet-balance", {"accountType": ACCOUNT_TYPE})
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

def get_position():
    res = bybit_get("/v5/position/list", {"category": CATEGORY, "symbol": SYMBOL})
    items = (((res.get("result") or {}).get("list")) or [])
    if not items:
        return {"has_pos": False}
    p = items[0]
    size = float(p.get("size") or 0)
    side = p.get("side")
    avg = float(p.get("avgPrice") or p.get("entryPrice") or 0)
    upnl = float(p.get("unrealisedPnl") or 0)
    return {"has_pos": size > 0, "side": side, "size": size, "avgPrice": avg, "uPnL": upnl}

def set_leverage(x: int):
    body = {"category": CATEGORY, "symbol": SYMBOL, "buyLeverage": str(x), "sellLeverage": str(x)}
    return bybit_post("/v5/position/set-leverage", body)

def order_market(side: str, qty: float, reduce_only=False):
    body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty), "timeInForce": "IOC"}
    if reduce_only:
        body["reduceOnly"] = True
    return bybit_post("/v5/order/create", body)
    # ===== FULL QUANT ENGINE 3/5 : INDICATORS + AI SCORE =====
def ema(data, period):
    k = 2/(period+1)
    e = data[0]
    for v in data[1:]:
        e = v*k + e*(1-k)
    return e

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains=loss=0.0
    for i in range(-period,0):
        d = closes[i] - closes[i-1]
        if d > 0: gains += d
        else: loss -= d
    rs = gains/(loss+1e-9)
    return 100 - (100/(1+rs))

def atr(highs,lows,closes,period=14):
    if len(closes) < period + 1:
        return None
    trs=[]
    for i in range(-period,0):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    return sum(trs)/period

def ai_score(price, ema_fast, ema_slow, rsi_val, atr_val):
    score = 0
    if price > ema_slow: score += 25
    if price > ema_fast: score += 20
    if rsi_val is not None and 45 < rsi_val < 65: score += 20
    if atr_val is not None and (atr_val/price) < 0.02: score += 15
    if ema_fast > ema_slow: score += 20
    return int(score)

def confidence_label(score):
    if score >= 85: return "🔥 매우높음"
    if score >= 70: return "✅ 높음"
    if score >= 55: return "⚠️ 보통"
    return "❌ 낮음"

def build_reason(side, price, ema_fast, ema_slow, rsi_val, atr_val, score):
    return (
        f"[{side}] 근거\n"
        f"- price={price:.2f}\n"
        f"- EMA{EMA_FAST}={ema_fast:.2f}, EMA{EMA_SLOW}={ema_slow:.2f}\n"
        f"- RSI{RSI_PERIOD}={rsi_val:.1f}\n"
        f"- ATR{ATR_PERIOD}={atr_val:.2f}\n"
        f"- AI score={score} ({confidence_label(score)})\n"
    )# ===== FULL QUANT ENGINE 4/5 : POSITION MGMT (SL/TP/TRAIL/TIME) =====
def mode_params():
    if MODE == "AGGRO":
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

def qty_from_order_usdt(order_usdt, lev, price):
    # 증거금 order_usdt로 notional=order_usdt*lev, qty=notional/price
    if order_usdt <= 0 or price <= 0:
        return 0.0
    return float(f"{(order_usdt*lev/max(price,1e-9)):.6f}")# ===== FULL QUANT ENGINE 5/5 : TRADER + TELEGRAM COMMANDS + LOOP =====
class Trader:
    def __init__(self, state=None):
        self.state = state if isinstance(state, dict) else {}
        self.trading_enabled = True
        self.position = None      # "LONG"/"SHORT"
        self.entry_price = None
        self.entry_ts = None
        self.stop_price = None
        self.tp_price = None
        self.trail_price = None
        self.consec_losses = 0
        self._cooldown_until = 0
        self._day_key = None
        self._day_entries = 0
        self.win = 0
        self.loss = 0
        self.day_profit = 0.0
        self._last_alert_ts = 0
        self._last_err_ts = 0
        self._lev_set = False

    def notify(self, msg):
        print(msg)
        if BOT_TOKEN and CHAT_ID:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data={"chat_id": CHAT_ID, "text": msg},
                    timeout=10
                )
            except:
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

    def _sync_real_position(self):
        if DRY_RUN:
            return
        p = get_position()
        if p.get("has_pos"):
            side = p.get("side")
            self.position = "LONG" if side == "Buy" else "SHORT"
            self.entry_price = float(p.get("avgPrice") or self.entry_price or 0)
            if not self.entry_ts:
                self.entry_ts = time.time()
        else:
            self.position = None
            self.entry_price = None
            self.entry_ts = None
            self.stop_price = None
            self.tp_price = None
            self.trail_price = None

    # ========= Telegram commands =========
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
            self.notify("🛡 SAFE 모드로 전환")
            return

        if cmd in ("/aggro", "/attack"):
            os.environ["MODE"] = "AGGRO"
            self.notify("⚔️ AGGRO(공격) 모드로 전환")
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
        winrate = (self.win/total*100) if total else 0.0
        mp = mode_params()
        lines = []
        lines.append(f"🧠 DRY_RUN={DRY_RUN} | ON={self.trading_enabled} | MODE={os.getenv('MODE','SAFE')}")
        lines.append(f"⚙️ lev={mp['lev']} | order_usdt={mp['order_usdt']} | enter_score>={mp['enter_score']}")
        lines.append(f"🌐 base={BYBIT_BASE_URL} | proxy={'ON' if PROXIES else 'OFF'}")
        if self.state.get("last_price") is not None:
            lines.append(f"💵 price={self.state.get('last_price'):.2f}")
        if self.state.get("usdt_balance") is not None:
            lines.append(f"💰 USDT={self.state.get('usdt_balance'):.2f}")
        lines.append(f"📍 POS={self.position or 'None'} entry={self.entry_price}")
        if self.stop_price and self.tp_price:
            lines.append(f"🎯 stop={self.stop_price:.2f} | tp={self.tp_price:.2f} | trail={self.trail_price}")
        lines.append(f"📈 day_profit={self.day_profit:.2f} | winrate={winrate:.1f}% (W{self.win}/L{self.loss})")
        if self.state.get("entry_reason"):
            lines.append(f"🧠 근거:\n{self.state.get('entry_reason')}")
        if self.state.get("last_event"):
            lines.append(f"📝 last={self.state.get('last_event')}")
        return "\n".join(lines)

    # ========= Manual =========
    def manual_enter(self, side: str):
        try:
            self._reset_day()
            self._sync_real_position()
            if self.position:
                self.notify("⚠️ 이미 포지션 있음. /status")
                return

            mp = mode_params()
            price = get_last_price()
            bal = get_wallet_usdt() if not DRY_RUN else float(self.state.get("paper_usdt", 30.0))

            lev = mp["lev"]
            if not self._lev_set and not DRY_RUN:
                set_leverage(lev)
                self._lev_set = True

            qty = qty_from_order_usdt(mp["order_usdt"], lev, price)
            if qty <= 0:
                self.notify("❌ qty 계산 실패")
                return

            # 근거도 같이 뽑아서 보여줌
            ok, reason, score, sl, tp = self._compute_signal_and_exits(side, price)

            if not DRY_RUN:
                order_market("Buy" if side=="LONG" else "Sell", qty)
            self.position = side
            self.entry_price = price
            self.entry_ts = time.time()
            self.stop_price = sl
            self.tp_price = tp
            self.trail_price = None
            self._cooldown_until = time.time() + COOLDOWN_SEC
            self._day_entries += 1

            self.state["entry_reason"] = reason
            self.notify(f"📌 수동진입 {side} qty={qty}\n{reason}\n⏳ 청산기준: stop={sl:.2f}, tp={tp:.2f}")
        except Exception as e:
            self.err_throttled(f"❌ manual enter 실패: {e}")

    def manual_exit(self, why: str, force=False):
        try:
            self._sync_real_position()
            if not self.position and not force:
                self.notify("⚠️ 포지션 없음")
                return

            price = get_last_price()
            qty = 0.001
            if not DRY_RUN:
                p = get_position()
                qty = float(p.get("size") or 0.0)
                if qty > 0:
                    order_market("Sell" if self.position=="LONG" else "Buy", qty, reduce_only=True)

            # PnL(대략, DRY_RUN은 추정)
            if self.entry_price:
                pnl = (price - self.entry_price) if self.position=="LONG" else (self.entry_price - price)
                self.day_profit += pnl
                if pnl >= 0: self.win += 1
                else: self.loss += 1
                if pnl < 0: self.consec_losses += 1
                else: self.consec_losses = 0

            self.notify(f"✅ 청산({why}) price={price:.2f} day_profit={self.day_profit:.2f}")
            self.position=None
            self.entry_price=None
            self.entry_ts=None
            self.stop_price=None
            self.tp_price=None
            self.trail_price=None
            self._cooldown_until = time.time() + COOLDOWN_SEC
        except Exception as e:
            self.err_throttled(f"❌ manual exit 실패: {e}")

    # ========= Signal / exits =========
    def _compute_signal_and_exits(self, side: str, price: float):
        kl = get_klines(ENTRY_INTERVAL, KLINE_LIMIT)
        if len(kl) < 120:
            return False, "kline 부족", 0, price*0.99, price*1.01

        kl = list(reversed(kl))
        closes = [float(x[4]) for x in kl]
        highs = [float(x[2]) for x in kl]
        lows  = [float(x[3]) for x in kl]

        efast = ema(closes[-(EMA_FAST*6):], EMA_FAST)
        eslow = ema(closes[-(EMA_SLOW*6):], EMA_SLOW)
        r = rsi(closes, RSI_PERIOD) or 50.0
        a = atr(highs, lows, closes, ATR_PERIOD) or (price*0.005)

        score = ai_score(price, efast, eslow, r, a)
        mp = mode_params()
        enter_ok = score >= mp["enter_score"]

        # 롱/숏 조건 분기
        if side == "LONG":
            trend_ok = price > eslow and efast > eslow
        else:
            trend_ok = price < eslow and efast < eslow

        ok = enter_ok and trend_ok

        reason = build_reason(side, price, efast, eslow, r, a, score)
        reason += f"- trend_ok={trend_ok} | enter_ok={enter_ok}\n"

        stop_dist = a * mp["stop_atr"]
        tp_dist = stop_dist * mp["tp_r"]
        if side == "LONG":
            sl = price - stop_dist
            tp = price + tp_dist
        else:
            sl = price + stop_dist
            tp = price - tp_dist

        return ok, reason, score, sl, tp

    # ========= Main tick =========
    def tick(self):
        self._reset_day()

        self.state["trading_enabled"] = self.trading_enabled
        self.state["mode"] = os.getenv("MODE","SAFE")
        self.state["bybit_base"] = BYBIT_BASE_URL
        self.state["proxy"] = "ON" if PROXIES else "OFF"

        if not self.trading_enabled:
            self.state["last_event"] = "거래 OFF"
            return

        if self.consec_losses >= MAX_CONSEC_LOSSES:
            self.notify_throttled("🛑 연속 손실 제한 도달. 거래 중지")
            self.trading_enabled = False
            return

        # price + balance
        try:
            price = get_last_price()
            self.state["last_price"] = price
        except Exception as e:
            self.err_throttled(f"❌ price 실패: {e}")
            return

        try:
            bal = get_wallet_usdt() if not DRY_RUN else float(self.state.get("paper_usdt", 30.0))
            self.state["usdt_balance"] = bal
        except Exception as e:
            self.err_throttled(f"❌ balance 실패: {e}")
            return

        # sync position
        try:
            self._sync_real_position()
        except Exception as e:
            self.err_throttled(f"❌ position sync 실패: {e}")

        # ENTRY
        if not self.position:
            if time.time() < self._cooldown_until:
                self.state["last_event"] = "대기: cooldown"
                return
            if self._day_entries >= MAX_ENTRIES_PER_DAY:
                self.state["last_event"] = "대기: 일일 진입 제한"
                return

            mp = mode_params()
            lev = mp["lev"]
            if not self._lev_set and not DRY_RUN:
                try:
                    set_leverage(lev)
                    self._lev_set = True
                except Exception as e:
                    self.err_throttled(f"❌ leverage 설정 실패: {e}")
                    return

            # LONG / SHORT 둘 다 스캔해서 더 점수 높은 쪽 선택
            best = None
            if ALLOW_LONG:
                ok, reason, score, sl, tp = self._compute_signal_and_exits("LONG", price)
                best = ("LONG", ok, reason, score, sl, tp)
            if ALLOW_SHORT:
                ok2, reason2, score2, sl2, tp2 = self._compute_signal_and_exits("SHORT", price)
                if (best is None) or (score2 > best[3]):
                    best = ("SHORT", ok2, reason2, score2, sl2, tp2)

            side, ok, reason, score, sl, tp = best
            self.state["entry_reason"] = reason

            if not ok:
                self.state["last_event"] = f"대기: score={score}"
                return

            qty = qty_from_order_usdt(mp["order_usdt"], lev, price)
            if qty <= 0:
                self.state["last_event"] = "대기: qty<=0"
                return

            try:
                if not DRY_RUN:
                    order_market("Buy" if side=="LONG" else "Sell", qty)
                self.position = side
                self.entry_price = price
                self.entry_ts = time.time()
                self.stop_price = sl
                self.tp_price = tp
                self.trail_price = None
                self._cooldown_until = time.time() + COOLDOWN_SEC
                self._day_entries += 1

                self.notify(
                    f"✅ 진입 {side} qty={qty}\n{reason}"
                    f"\n⏳ 청산기준: stop={sl:.2f}, tp={tp:.2f}"
                )
                self.state["last_event"] = f"ENTER {side}"
            except Exception as e:
                self.err_throttled(f"❌ entry 주문 실패: {e}")
            return

        # EXIT / MANAGE
        if self.position and self.entry_price:
            side = self.position
            ok, reason, score, sl_new, tp_new = self._compute_signal_and_exits(side, price)

            # 트레일링 스탑
            if TRAIL_ON:
                # 롱이면 가격이 오르면 stop 올림 / 숏이면 가격이 내리면 stop 내림
                try:
                    kl = get_klines(ENTRY_INTERVAL, KLINE_LIMIT)
                    kl = list(reversed(kl))
                    closes = [float(x[4]) for x in kl]
                    highs = [float(x[2]) for x in kl]
                    lows  = [float(x[3]) for x in kl]
                    a = atr(highs, lows, closes, ATR_PERIOD) or (price*0.005)
                    trail_dist = a * TRAIL_ATR_MULT

                    if side == "LONG":
                        candidate = price - trail_dist
                        if self.trail_price is None or candidate > self.trail_price:
                            self.trail_price = candidate
                    else:
                        candidate = price + trail_dist
                        if self.trail_price is None or candidate < self.trail_price:
                            self.trail_price = candidate
                except:
                    pass

            # 실제 적용 stop은: 기본 stop vs trail 중 더 유리한 것
            eff_stop = self.stop_price
            if self.trail_price is not None:
                if side == "LONG":
                    eff_stop = max(eff_stop, self.trail_price)
                else:
                    eff_stop = min(eff_stop, self.trail_price)

            # 시간청산
            if self.entry_ts and (time.time() - self.entry_ts) > (TIME_EXIT_MIN*60):
                self.manual_exit("TIME EXIT")
                return

            # 점수 급락 청산(조기 리스크 컷)
            if score <= EXIT_SCORE_DROP:
                self.manual_exit(f"SCORE DROP {score}")
                return

            # 손절/익절
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

            # 상태 기록
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
            "trail_price": self.trail_price,
            "day_profit": self.day_profit,
            "win": self.win,
            "loss": self.loss,
            "consec_losses": self.consec_losses,
            "day_entries": self._day_entries,
            "cooldown_until": self._cooldown_until,
            "entry_reason": self.state.get("entry_reason"),
            "last_event": self.state.get("last_event"),
            "last_price": self.state.get("last_price"),
            "usdt_balance": self.state.get("usdt_balance"),
        }
    
