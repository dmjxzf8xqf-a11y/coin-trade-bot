import requests
import time
from config import *

PRICE_URL = "https://api.binance.com/api/v3/ticker/price"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}

class Trader:
    def __init__(self, state):
        self.state = state
        self.position = None
        self.entry_price = None

    def notify(self, msg):
        print(msg)
        if BOT_TOKEN and CHAT_ID:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data={"chat_id": CHAT_ID, "text": msg}
                )
            except:
                pass

    # 🔥 안정 가격 조회 (재시도 포함)
    def get_price(self):
        for _ in range(3):  # 3번 재시도
            try:
                r = requests.get(
                    PRICE_URL,
                    params={"symbol": SYMBOL},
                    headers=HEADERS,
                    timeout=10,
                )

                if r.status_code != 200:
                    time.sleep(1)
                    continue

                data = r.json()

                if "price" not in data:
                    return None

                return float(data["price"])

            except:
                time.sleep(1)

        self.notify("⚠️ 가격 조회 실패")
        return None

    def place_order(self, side):
        if DRY_RUN:
            self.notify(f"🧪 테스트 주문: {side}")
            return

        self.notify(f"🚨 실제 주문 실행: {side}")

    def tick(self):
        price = self.get_price()

        if price is None:
            return

        self.state["last_price"] = price
        self.state["last_event"] = f"Price: {price}"

        if not self.position:
            self.position = "LONG"
            self.entry_price = price
            self.place_order("BUY")
            self.notify(f"📈 LONG 진입: {price}")
            return

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
            "entry_price": self.entry_price
        }
