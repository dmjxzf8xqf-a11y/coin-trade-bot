import os

# ===== MODE =====
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# ===== TELEGRAM =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

# ===== LOOP =====
LOOP_SECONDS = int(os.getenv("LOOP_SECONDS", "60"))

# ===== MASTER SWITCH =====
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "true").lower() == "true"

# ===== BYBIT =====
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BYBIT_BASE_URL = "https://api.bybit.com"
SYMBOL = "BTCUSDT"
CATEGORY = "linear"  # USDT 선물

# ===== COMPOUNDING =====
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
RISK_PCT = float(os.getenv("RISK_PCT", "0.2"))  # ✅ 복리 핵심: 잔고의 몇 %를 증거금으로 쓸지

# ===== RISK / STRATEGY =====
MAX_LOSS_PERCENT = float(os.getenv("MAX_LOSS_PERCENT", "0.8"))
TAKE_PROFIT_PERCENT = float(os.getenv("TAKE_PROFIT_PERCENT", "1.2"))
CRASH_PROTECT_PERCENT = float(os.getenv("CRASH_PROTECT_PERCENT", "1.5"))
MAX_CONSEC_LOSSES = int(os.getenv("MAX_CONSEC_LOSSES", "3"))

ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC", "120"))
