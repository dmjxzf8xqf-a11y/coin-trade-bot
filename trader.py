import time
import hmac
import hashlib
import requests
from config import *

BASE_URL = "https://api.bybit.com"

class Trader:
    def __init__(self, state):
        self.state = state
        self.position = None
        self.entry_price = None

    def notify(self, msg):
        print(msg)
        if BOT_TOKEN and CHAT_ID:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": msg}
            )

    def get_price(self):
        url = f"{BASE_URL}/v5/market/tickers?category=linear&symbol={SYMBOL}"
        data = requests.get(url).json()
        return float(data["result"]["list"][0]["lastPrice"])

    def place_order(self, side):
        if DRY_RUN:
            self.notify(f"🧪 TEST ORDER: {side}")
            return

        self.notify(f"🚨 REAL ORDER: {side}")
        # 실제 주문 로직 (원하면 추가 구현 가능)

    def tick(self):
        price = self.get_price()
        self.state["last_price"] = price

        # 📈 진입 조건 (예시: 단순 상승 추세)
        if not self.position:
            self.position = "LONG"
            self.entry_price = price
            self.place_order("BUY")
            self.notify(f"📈 LONG 진입: {price}")

        # 📉 손절 / 익절
        if self.position == "LONG":
            change = ((price - self.entry_price) / self.entry_price) * 100

            if change <= -MAX_LOSS_PERCENT:
                self.notify(f"🛑 손절 실행: {change:.2f}%")
                self.place_order("SELL")
                self.position = None

            elif change >= TAKE_PROFIT_PERCENT:
                self.notify(f"💰 익절 실행: {change:.2f}%")
                self.place_order("SELL")
                self.position = None

        self.state["last_event"] = f"Price: {price}"
        time.sleep(1)

    def public_state(self):
        return {
            "price": self.state.get("last_price"),
            "position": self.position,
            "entry_price": self.entry_price
        }
