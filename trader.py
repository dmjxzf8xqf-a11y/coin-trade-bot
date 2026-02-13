import requests
from config import *

BASE_URL = "https://api.bybit.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
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

    def get_price(self):
        try:
            url = f"{BASE_URL}/v5/market/tickers?category=linear&symbol={SYMBOL}"
            r = requests.get(url, headers=HEADERS, timeout=10)

            # 응답 내용 확인
            if not r.text:
                self.notify("⚠️ 빈 응답 수신")
                return None

            data = r.json()

            if "result" not in data:
                self.notify(f"⚠️ API 오류: {data}")
                return None

            return float(data["result"]["list"][0]["lastPrice"])

        except Exception as e:
            self.notify(f"⚠️ 가격 조회 실패")
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
