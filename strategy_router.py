# strategy_router.py
from ai_learn import get_bucket_score, get_symbol_side_score, get_global_score


def _norm_regime(regime: str) -> str:
    return (regime or "").strip().lower()


def select_strategy(regime: str) -> str:
    """Map every regime name used in trader.py/market_regime.py to one strategy key.

    trader.py currently emits: bull / bear / range / volatile.
    Older helpers sometimes emit: bull_strong / bear_strong / sideways / high_volatility.
    """
    r = _norm_regime(regime)

    if r in ("volatile", "high_volatility", "panic", "crash"):
        return "defense"
    if r in ("range", "sideways", "sideway", "chop", "unknown", ""):
        return "no_trade"
    if r in ("bull", "bull_strong", "up", "uptrend"):
        return "trend_long"
    if r in ("bear", "bear_strong", "down", "downtrend"):
        return "trend_short"

    return "no_trade"


def compute_ai_enter_score(symbol: str, side: str, strategy: str, regime: str, base_enter_score: float) -> float:
    score = float(base_enter_score)
    score += get_bucket_score(symbol, side, strategy, regime) * 0.15
    score += get_symbol_side_score(symbol, side) * 0.08
    score += get_global_score() * 0.03

    # hardening: trend strategies need higher baseline quality
    if strategy in ("trend_long", "trend_short"):
        score += 4.0
    if _norm_regime(regime) in ("high_volatility", "volatile"):
        score += 6.0
    return round(score, 4)


def should_block_trade(symbol: str, side: str, strategy: str, regime: str, enter_score: float):
    r = _norm_regime(regime)
    if strategy == "no_trade":
        return True, "횡보/불명확 구간 진입 금지"
    if r in ("high_volatility", "volatile") and strategy != "defense":
        return True, "고변동성 구간에서는 defense만 허용"

    min_map = {
        "defense": 72,
        "trend_long": 70,
        "trend_short": 70,
    }
    need = min_map.get(strategy, 72)
    if enter_score < need:
        return True, f"AI 점수 부족 ({enter_score:.1f} < {need})"
    return False, "통과"
