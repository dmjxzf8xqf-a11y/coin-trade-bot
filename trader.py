import requests
from config import *

BINANCE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"


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

    # ✅ Binance 가격 조회 (안정)
    def get_price(self):
        try:
            r = requests.get(BINANCE_URL, timeout=10)
            data = r.json()
            return float(data["price"])
        except:
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

        # ===== 진입 =====
        if not self.position:
            self.position = "LONG"
            self.entry_price = price
            self.place_order("BUY")
            self.notify(f"📈 LONG 진입: {price}")
            return

        # ===== 손절 / 익절 =====
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
