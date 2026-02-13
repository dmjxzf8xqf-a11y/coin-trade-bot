import time
import json
import hmac
import hashlib
import requests
from config import *

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# 가격 소스 (Render 차단 대비 다중)
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

    # -------- 알림 --------
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

    # -------- 가격 --------
    def _price_binance(self):
        r = requests.get(BINANCE, params={"symbol": SYMBOL}, headers=HEADERS, timeout=10)
        return float(r.json()["price"])

    def _price_gecko(self):
        r = requests.get(COINGECKO, params={"ids":"bitcoin","vs_currencies":"usd"}, timeout=10)
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

    # -------- Bybit 서명 --------
    def _headers(self, body):
        ts = str(int(time.time()*1000))
        recv = "5000"
        body_str = json.dumps(body, separators=(",",":"))
        pre = ts + BYBIT_API_KEY + recv + body_str
        sign = hmac.new(BYBIT_API_SECRET.encode(), pre.encode(), hashlib.sha256).hexdigest()
        return {
            "Content-Type":"application/json",
            "X-BAPI-API-KEY":BYBIT_API_KEY,
            "X-BAPI-SIGN":sign,
            "X-BAPI-SIGN-TYPE":"2",
            "X-BAPI-TIMESTAMP":ts,
            "X-BAPI-RECV-WINDOW":recv,
        }, body_str

    def _post(self, path, body):
        if DRY_RUN:
            return {"retCode":0,"retMsg":"DRY_RUN"}
        h, b = self._headers(body)
        r = requests.post(BYBIT_BASE_URL+path, headers=h, data=b, timeout=15)
        return r.json()

    # -------- 레버리지 --------
    def set_leverage(self):
        body = {
            "category":"linear",
            "symbol":SYMBOL,
            "buyLeverage":str(LEVERAGE),
            "sellLeverage":str(LEVERAGE),
        }
        res = self._post("/v5/position/set-leverage", body)
        self.notify(f"⚙️ 레버리지 {LEVERAGE}x 설정")

    # -------- 주문 --------
    def order(self, side):
        body = {
            "category":"linear",
            "symbol":SYMBOL,
            "side":side,
            "orderType":"Market",
            "qty":str(TRADE_QTY),
            "timeInForce":"IOC",
        }
        if side=="Sell":
            body["reduceOnly"]=True
        res = self._post("/v5/order/create", body)
        self.notify(f"주문: {res.get('retMsg')}")
        return res

    # -------- 메인 --------
    def tick(self):
        if not self.state.get("lev_set"):
            self.set_leverage()
            self.state["lev_set"]=True

        if self.consec_losses >= MAX_CONSEC_LOSSES:
            self.notify_throttled("🛑 연속 손실 제한 도달")
            return

        price = self.get_price()
        if not price:
            return

        self.state["last_price"]=price

        if not self.position:
            self.position="LONG"
            self.entry_price=price
            self.order("Buy")
            self.notify(f"📈 LONG {price}")
            return

        change = (price-self.entry_price)/self.entry_price*100

        if change <= -CRASH_PROTECT_PERCENT:
            self.notify("🚨 급락 보호")
            self.order("Sell")
            self.position=None
            self.consec_losses+=1
            return

        if change <= -MAX_LOSS_PERCENT:
            self.notify(f"🛑 손절 {change:.2f}%")
            self.order("Sell")
            self.position=None
            self.consec_losses+=1
            return

        if change >= TAKE_PROFIT_PERCENT:
            self.notify(f"💰 익절 {change:.2f}%")
            self.order("Sell")
            self.position=None
            self.consec_losses=0
            return

    def public_state(self):
        return {
            "price": self.state.get("last_price"),
            "position": self.position,
            "entry_price": self.entry_price,
            "consec_losses": self.consec_losses,
            "dry_run": DRY_RUN
        }
