import requests

class Trader:
    def __init__(self, state):
        self.state = state
        self.last_price = None

    def notify(self, msg):
        print(msg)

    def get_price(self):
        url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT"
        r = requests.get(url).json()
        return float(r["result"]["list"][0]["lastPrice"])

    def tick(self):
        price = self.get_price()
        self.last_price = price
        self.state["last_event"] = f"BTC Price: {price}"
        print("Price:", price)

    def public_state(self):
        return {"price": self.last_price}
