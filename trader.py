import time
import json
import hmac
import hashlib
import requests
from config import *

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# 가격 소스(다중)
BINANCE = "https://api.binance.com/api/v3/ticker/price"
COINGECKO = "https://api.coingecko.com/api/v3/simple/price"
COINBASE = "https://api.coinbase.com/v2/prices/BTC-USD/spot"


class Trader:
    def __init__(self, state):
        self.state = state
        self.position = None
        self.entry_price = None
        self.consec_losses = 0
        self._last_alert_ts = 0

    # ---------- notify ----------
    def notify(self, msg):
        print(msg)
        if BOT_TOKEN and CHAT_ID:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data={"chat_id": CHAT_ID, "text": msg},
                    timeout=10,
                )
            except:
                pass

    def notify_throttled(self, msg):
        if time.time() - self._last_alert_ts >= ALERT_COOLDOWN_SEC:
            self._last_alert_ts = time.time()
            self.notify(msg)

    # ---------- price ----------
    def _price_binance(self):
        r = requests.get(BINANCE, params={"symbol": SYMBOL}, headers=HEADERS, timeout=10)
        return float(r.json()["price"])

    def _price_gecko(self):
        r = requests.get(COINGECKO, params={"ids": "bitcoin", "vs_currencies": "usd"}, timeout=10)
        return float(r.json()["bitcoin"]["usd"])

    def _price_coinbase(self):
        r = requests.get(COINBASE, timeout=10)
        return float(r.json()["data"]["amount"])

    def get_price(self):
        for f in (self._price_binance, self._price_gecko, self._price_coinbase):
            try:
                return f()
            except:
                pass
        self.notify_throttled("⚠️ 가격 조회 실패")
        return None

    # ---------- bybit signing ----------
    def _signed_headers(self, body: dict):
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body_str = json.dumps(body, separators=(",", ":"))
        pre = ts + BYBIT_API_KEY + recv + body_str
        sign = hmac.new(BYBIT_API_SECRET.encode(), pre.encode(), hashlib.sha256).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": BYBIT_API_KEY,
            "X-BAPI-SIGN": sign,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
        }, body_str

    def _bybit_post(self, path: str, body: dict):
        if DRY_RUN:
            return {"retCode": 0, "retMsg": "DRY_RUN", "result": {}}
        h, b = self._signed_headers(body)
        r = requests.post(BYBIT_BASE_URL + path, headers=h, data=b, timeout=15)
        if not r.text:
            raise Exception(f"Bybit empty response status={r.status_code}")
        return r.json()

    # ---------- leverage ----------
    def set_leverage(self):
        body = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "buyLeverage": str(LEVERAGE),
            "sellLeverage": str(LEVERAGE),
        }
        res = self._bybit_post("/v5/position/set-leverage", body)
        self.notify(f"⚙️ 레버리지 {LEVERAGE}x 설정: {res.get('retMsg')} ({res.get('retCode')})")

    # ---------- balance (USDT) ----------
    def get_usdt_balance(self):
        """
        V5 wallet-balance는 GET이지만,
        Render에서 간단히 쓰려고 POST 엔드포인트만 구현한 상태라
        'wallet-balance'는 Bybit가 실제로는 GET임.
        그래서 여기선 우회로: coin-balance(POST 불가) 문제 때문에
        실전에서는 GET 서명 구현이 필요.
        
        ✅ 지금은 "복붙 즉시 동작"을 위해:
        - DRY_RUN이면 가짜 잔고 30으로 가정
        - 실전 ON 전에는 내가 GET 서명 버전으로 업그레이드해주는 게 정석
        """
        if DRY_RUN:
            return float(self.state.get("paper_usdt", 30.0))

        # ⚠️ 실전에서는 아래처럼 '잔고 조회용 Signed GET'을 붙여야 정확함a
        # 지금은 안전을 위해 강제 예외 → 실거래 전에 업그레이드 유도
        raise Exception("실전 복리(잔고 기반) 사용하려면 Bybit 잔고 Signed GET 구현이 필요함")

    # ---------- position sizing (compound) ----------
    def calc_qty_from_balance(self, usdt_balance: float, price: float):
        """
        증거금 = usdt_balance * RISK_PCT
        명목 포지션(USDT) = 증거금 * LEVERAGE
        qty(BTC) = 명목/price
        """
        margin = usdt_balance * RISK_PCT
        notional = margin * LEVERAGE
        qty = notional / price

        # 너무 작은 qty는 거래소 최소단위에 걸릴 수 있어서 바닥값
        qty = max(qty, 0.0001)

        # 자주 쓰는 소수점으로 깔끔하게
        return float(f"{qty:.6f}")

    # ---------- order ----------
    def order_market(self, side: str, qty: float):
        if DRY_RUN:
            self.notify(f"🧪 테스트 주문: {side} qty={qty}")
            return {"retCode": 0, "retMsg": "DRY_RUN"}

        body = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "side": side,              # "Buy" / "Sell"
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "IOC",
        }
        if side == "Sell":
            body["reduceOnly"] = True

        res = self._bybit_post("/v5/order/create", body)
        self.notify(f"✅ 주문: {res.get('retMsg')} ({res.get('retCode')}) / qty={qty}")
        return res

    # ---------- main ----------
    def tick(self):
        if not TRADING_ENABLED:
            self.state["last_event"] = "TRADING_ENABLED=false (거래 OFF)"
            return

        if self.consec_losses >= MAX_CONSEC_LOSSES:
            self.notify_throttled("🛑 연속 손실 제한 도달 (거래 중지)")
            return

        if not self.state.get("lev_set"):
            self.set_leverage()
            self.state["lev_set"] = True

        price = self.get_price()
        if not price:
            return

        self.state["last_price"] = price
        self.state["last_event"] = f"Price: {price}"

        # ✅ 복리: 잔고 기반으로 qty 자동 계산
        usdt_balance = self.get_usdt_balance()
        qty = self.calc_qty_from_balance(usdt_balance, price)
        self.state["usdt_balance"] = usdt_balance
        self.state["calc_qty"] = qty

        # 중복 진입 방지
        if (self.position is None) and (self.entry_price is None):
            self.position = "LONG"
            self.entry_price = price
            self.order_market("Buy", qty)
            self.notify(f"📈 LONG 진입: {price} / USDT={usdt_balance:.2f} / qty={qty} (복리)")
            return

        # 손절/익절
        change = (price - self.entry_price) / self.entry_price * 100

        if change <= -CRASH_PROTECT_PERCENT:
            self.notify("🚨 급락 보호")
            self.order_market("Sell", qty)
            self.position = None
            self.entry_price = None
            self.consec_losses += 1
            return

        if change <= -MAX_LOSS_PERCENT:
            self.notify(f"🛑 손절 {change:.2f}%")
            self.order_market("Sell", qty)
            self.position = None
            self.entry_price = None
            self.consec_losses += 1
            return

        if change >= TAKE_PROFIT_PERCENT:
            self.notify(f"💰 익절 {change:.2f}%")
            self.order_market("Sell", qty)
            self.position = None
            self.entry_price = None
            self.consec_losses = 0
            return

    def public_state(self):
        return {
            "price": self.state.get("last_price"),
            "position": self.position,
            "entry_price": self.entry_price,
            "consec_losses": self.consec_losses,
            "dry_run": DRY_RUN,
            "trading_enabled": TRADING_ENABLED,
            "usdt_balance": self.state.get("usdt_balance"),
            "calc_qty": self.state.get("calc_qty"),
        }
