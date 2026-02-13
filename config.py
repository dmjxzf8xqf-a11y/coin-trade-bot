import os

# ===== MODE =====
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# ===== TELEGRAM =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

# ===== LOOP =====
LOOP_SECONDS = int(os.getenv("LOOP_SECONDS", "60"))

# ===== BYBIT =====
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BYBIT_BASE_URL = "https://api.bybit.com"
BYBIT_CATEGORY = "linear"   # USDT 선물

# ===== TRADING =====
SYMBOL = "BTCUSDT"
TRADE_QTY = float(os.getenv("TRADE_QTY", "0.001"))

# 5배 레버리지 안전값
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
MAX_LOSS_PERCENT = float(os.getenv("MAX_LOSS_PERCENT", "0.8"))
TAKE_PROFIT_PERCENT = float(os.getenv("TAKE_PROFIT_PERCENT", "1.2"))

# 급락 보호
CRASH_PROTECT_PERCENT = float(os.getenv("CRASH_PROTECT_PERCENT", "1.5"))

# 연속 손실 시 중지
MAX_CONSEC_LOSSES = int(os.getenv("MAX_CONSEC_LOSSES", "3"))

# 알림 쿨다운
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC", "120"))
