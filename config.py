import os

# ===== MODE =====
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# ===== TELEGRAM =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")  # 보안상 이 채팅만 명령 허용

# ===== LOOP =====
LOOP_SECONDS = int(os.getenv("LOOP_SECONDS", "60"))
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC", "120"))

# ===== MASTER SWITCH =====
TRADING_ENABLED_DEFAULT = os.getenv("TRADING_ENABLED", "false").lower() == "true"

# ===== BYBIT =====
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BYBIT_BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com").rstrip("/")

SYMBOL = os.getenv("SYMBOL", "ETHUSDT")
CATEGORY = os.getenv("CATEGORY", "linear")          # USDT 선물 = linear
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "UNIFIED") # UNIFIED or CONTRACT

# ===== COMPOUNDING =====
LEVERAGE_DEFAULT = int(os.getenv("LEVERAGE", "5"))
RISK_PCT_DEFAULT = float(os.getenv("RISK_PCT", "0.2"))  # 잔고의 몇 %를 증거금으로 쓸지

# ===== RISK / STRATEGY =====
MAX_LOSS_PERCENT = float(os.getenv("MAX_LOSS_PERCENT", "0.8"))
TAKE_PROFIT_PERCENT = float(os.getenv("TAKE_PROFIT_PERCENT", "1.2"))
CRASH_PROTECT_PERCENT = float(os.getenv("CRASH_PROTECT_PERCENT", "1.5"))
MAX_CONSEC_LOSSES = int(os.getenv("MAX_CONSEC_LOSSES", "3"))
# ===== AI GROWTH (faster / logging / scaling) =====
AI_GROWTH = os.getenv("AI_GROWTH", "true").lower() == "true"
AI_GROWTH_FAST = os.getenv("AI_GROWTH_FAST", "true").lower() == "true"

# 최근 N번 결과로 더 빠르게 튜닝
GROWTH_WINDOW = int(os.getenv("GROWTH_WINDOW", "4"))      # 4~6 추천
GROWTH_EVERY_N = int(os.getenv("GROWTH_EVERY_N", "1"))    # 1이면 매번 exit마다 튜닝

# 민감도(평균 손익이 이 값 아래/위면 튜닝)
GROWTH_LOSS_THRESHOLD = float(os.getenv("GROWTH_LOSS_THRESHOLD", "-0.3"))
GROWTH_PROFIT_THRESHOLD = float(os.getenv("GROWTH_PROFIT_THRESHOLD", "0.6"))

# 성장 로그 파일(레일웨이/렌더에서 /tmp는 보통 OK)
GROWTH_LOG_PATH = os.getenv("GROWTH_LOG_PATH", "/tmp/growth_log.jsonl")

# ===== AUTO SCALE (balance-based) =====
AUTO_SCALE = os.getenv("AUTO_SCALE", "true").lower() == "true"
SCALE_REF_USDT = float(os.getenv("SCALE_REF_USDT", "50"))   # 잔고 50USDT를 기준 1.0배
SCALE_MIN = float(os.getenv("SCALE_MIN", "0.5"))
SCALE_MAX = float(os.getenv("SCALE_MAX", "2.0"))
SCALE_SMOOTH = float(os.getenv("SCALE_SMOOTH", "0.5"))      # 0~1 (높을수록 부드럽게 변화)
# ===== AUTO OPTIMIZATION =====
AUTO_OPTIMIZE = True
OPTIMIZE_INTERVAL_HOURS = 24

# ===== SAFETY =====
MAX_CONSECUTIVE_LOSSES = 4
MAX_DRAWDOWN_PERCENT = 12

# ===== MARKET REGIME =====
ENABLE_REGIME_FILTER = True

# ===== POSITION RISK =====
RISK_PER_TRADE = 0.02   # 2%
VOLATILITY_ADJUST = True



# ===== FINAL QUANT CORE (optional) =====
USE_EXECUTION_ENGINE = os.getenv("USE_EXECUTION_ENGINE", "1") == "1"
USE_LIQUIDITY_FILTER = os.getenv("USE_LIQUIDITY_FILTER", "1") == "1"
USE_STRATEGY_PERF = os.getenv("USE_STRATEGY_PERF", "1") == "1"
USE_PORTFOLIO_OPT = os.getenv("USE_PORTFOLIO_OPT", "1") == "1"
USE_WALKFORWARD = os.getenv("USE_WALKFORWARD", "0") == "1"  # 기본 OFF (무거움)

# liquidity filter (Bybit ticker based)
MIN_TURNOVER24H_USDT = float(os.getenv("MIN_TURNOVER24H_USDT", "5000000"))  # 5M
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", "12"))  # 12bps

# strategy performance auto-disable
PERF_WINDOW = int(os.getenv("PERF_WINDOW", "30"))
PERF_MIN_TRADES = int(os.getenv("PERF_MIN_TRADES", "10"))
PERF_DISABLE_BELOW_WINRATE = float(os.getenv("PERF_DISABLE_BELOW_WINRATE", "0.43"))
PERF_DISABLE_FOR_MIN = int(os.getenv("PERF_DISABLE_FOR_MIN", "60"))

# portfolio optimizer
PORT_BASE_MULT = float(os.getenv("PORT_BASE_MULT", "1.0"))
PORT_MAX_MULT = float(os.getenv("PORT_MAX_MULT", "1.8"))
PORT_MIN_MULT = float(os.getenv("PORT_MIN_MULT", "0.6"))
PORT_SMOOTH = float(os.getenv("PORT_SMOOTH", "0.35"))
