# strategy_router.py
from ai_learn import get_bucket_score, get_symbol_side_score, get_global_score


def select_strategy(regime: str) -> str:
    r = (regime or "").lower()
    if r == "high_volatility":
        return "defense"
    if r == "sideways":
        return "no_trade"
    if r == "bull_strong":
        return "trend_long"
    if r == "bear_strong":
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
    if regime == "high_volatility":
        score += 6.0
    return round(score, 4)


def should_block_trade(symbol: str, side: str, strategy: str, regime: str, enter_score: float):
    if strategy == "no_trade":
        return True, "í¡ë³´ì¥/ë¶ëªí êµ¬ê° ì§ì ê¸ì§"
    if regime == "high_volatility" and strategy != "defense":
        return True, "ê³ ë³ëì± êµ¬ê°ììë defenseë§ íì©"

    min_map = {
        "defense": 72,
        "trend_long": 70,
        "trend_short": 70,
    }
    need = min_map.get(strategy, 72)
    if enter_score < need:
        return True, f"AI ì ì ë¶ì¡± ({enter_score:.1f} < {need})"
    return False, "íµê³¼"
