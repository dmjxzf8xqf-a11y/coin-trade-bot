import requests
import time
from config import *

BASE_URL = "https://api.bybit.com"


class Trader:
    def __init__(self, state):
        self.state = state
        self.position = None
        self.entry_price = None

    # ✅ 텔레그램 알림
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

    # ✅ 가격 조회 (안정 버전)
    def get_price(self):
        try:
            url = f"{BASE_URL}/v5/market/tickers?category=linear&symbol={SYMBOL}"
            r = requests.get(url, timeout=10)

            if r.status_code != 200 or not r.text:
                self.notify(f"⚠️ API 응답 이상: {r.status_code}")
                return None

            data = r.json()

            if "result" not in data or "list" not in data["result"]:
                self.notify(f"⚠️ API 구조 오류")
                return None

            price = float(data["result"]["list"][0]["lastPrice"])
            return price

        except Exception as e:
            self.notify(f"⚠️ 가격 조회 실패: {e}")
            return None

    # ✅ 주문 실행
    def place_order(self, side):
        if DRY_RUN:
            self.notify(f"🧪 테스트 주문: {side}")
            return

        # 실제 주문 로직 (원하면 나중에 추가)
        self.notify(f"🚨 실제 주문 실행: {side}")

    # ✅ 메인 루프 로직
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
            self.notify(f"🛑 손절 실행: {change:.2f}%")
            self.place_order("SELL")
            self.position = None

        elif change >= TAKE_PROFIT_PERCENT:
            self.notify(f"💰 익절 실행: {change:.2f}%")
            self.place_order("SELL")
            self.position = None

    # ✅ 상태 표시용
    def public_state(self):
        return {
            "price": self.state.get("last_price"),
            "position": self.position,
            "entry_price": self.entry_price
        }
