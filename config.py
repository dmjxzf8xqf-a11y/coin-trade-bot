import os

# ====== BYBIT API ======
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# ====== TELEGRAM ======
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

# ====== BOT SETTINGS ======
SYMBOL = "BTCUSDT"
TRADE_QTY = 0.001        # 주문 수량
MAX_LOSS_PERCENT = 2     # 손실 제한 %
TAKE_PROFIT_PERCENT = 3  # 익절 %
DRY_RUN = True           # True = 테스트 모드 (실제 주문X)
