import time
import requests
from config import *

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
COINBASE_URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"


class Trader:
    def __init__(self, state):
        self.state = state
        self.position = None
        self.entry_price = None

        # 알림 도배 방지(초 단위)
        self._last_alert_ts = 0
        self._alert_cooldown_sec = 120  # 2분에 1번만 알림

        # 가격 실패 연속 카운트
        self._price_fail_count = 0

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
        now = time.time()
        if now - self._last_alert_ts >= self._alert_cooldown_sec:
            self._last_alert_ts = now
            self.notify(msg)

    # ---- 가격 소스 1: Binance ----
    def _price_binance(self):
        r = requests.get(
            BINANCE_URL,
            params={"symbol": "BTCUSDT"},  # 고정 (심볼 헷갈림 방지)
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code != 200 or not r.text:
            raise Exception(f"binance status={r.status_code} body='{(r.text or '')[:120]}'")
        data = r.json()
        return float(data["price"])

    # ---- 가격 소스 2: CoinGecko ----
    def _price_coingecko(self):
        r = requests.get(
            COINGECKO_URL,
            params={"ids": "bitcoin", "vs_currencies": "usd"},
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code != 200 or not r.text:
            raise Exception(f"coingecko status={r.status_code} body='{(r.text or '')[:120]}'")
        data = r.json()
        return float(data["bitcoin"]["usd"])

    # ---- 가격 소스 3: Coinbase ----
    def _price_coinbase(self):
        r = requests.get(COINBASE_URL, headers=HEADERS, timeout=10)
        if r.status_code != 200 or not r.text:
            raise Exception(f"coinbase status={r.status_code} body='{(r.text or '')[:120]}'")
        data = r.json()
        return float(data["data"]["amount"])

    def get_price(self):
        # 순서대로 시도: Binance → CoinGecko → Coinbase
        errors = []
        for fn in (self._price_binance, self._price_coingecko, self._price_coinbase):
            try:
                price = fn()
                self._price_fail_count = 0
                return price
            except Exception as e:
                errors.append(str(e))

        self._price_fail_count += 1
        self.state["last_error_detail"] = " | ".join(errors)[:500]

        # 실패가 계속돼도 텔레그램은 2분에 1번만
        self.notify_throttled(f"⚠️ 가격 조회 실패 x{self._price_fail_count}\n{self.state['last_error_detail']}")
        return None

    def place_order(self, side):
        if DRY_RUN:
            self.notify(f"🧪 테스트 주문: {side}")
            return
        # 실제 주문 로직은 다음 단계에서 Bybit V5로 붙임
        self.notify(f"🚨 실제 주문 실행(미구현): {side}")

    def tick(self):
        price = self.get_price()
        if price is None:
            return

        self.state["last_price"] = price
        self.state["last_event"] = f"Price: {price}"

        # ===== 진입(예시) =====
        if not self.position:
            self.position = "LONG"
            self.entry_price = price
            self.place_order("BUY")
            self.notify(f"📈 LONG 진입: {price}")
            return

        # ===== 손절/익절 =====
        change = ((price - self.entry_price) / self.entry_price) * 100

        if change <= -MAX_LOSS_PERCENT:
            self.notify(f"🛑 손절: {change:.2f}%")
            self.place_order("SELL")
            self.position = None

        elif change >= TAKE_PROFIT_PERCENT:
            self.notify(f"💰 익절: {change:.2f}%")
            self.place_order("SELL")
            self.position = None

    def public_state(self):
        return {
            "price": self.state.get("last_price"),
            "position": self.position,
            "entry_price": self.entry_price,
            "price_fail_count": self._price_fail_count,
            "last_error_detail": self.state.get("last_error_detail"),
        }
