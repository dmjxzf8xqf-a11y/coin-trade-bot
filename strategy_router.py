# strategy_router.py
from ai_learn import get_bucket_score, get_symbol_side_score, get_global_score

def select_strategy(regime: str) -> str:
    r = (regime or "").lower()
    if r == "high_volatility":
        return "defense"
    if r == "sideways":
        return "mean_reversion"
    if r == "bull_strong":
        return "trend_long"
    if r == "bear_strong":
        return "trend_short"
    return "trend_long"

def compute_ai_enter_score(symbol: str, side: str, strategy: str, regime: str, base_enter_score: float) -> float:
    score = float(base_enter_score)
    score += get_bucket_score(symbol, side, strategy, regime) * 0.20
    score += get_symbol_side_score(symbol, side) * 0.10
    score += get_global_score() * 0.05
    return round(score, 4)

def should_block_trade(symbol: str, side: str, strategy: str, regime: str, enter_score: float):
    if regime == "high_volatility" and strategy != "defense":
        return True, "시장 변동성 과다"
    min_map = {"defense": 55, "mean_reversion": 62, "trend_long": 65, "trend_short": 65}
    need = min_map.get(strategy, 65)
    if enter_score < need:
        return True, f"AI 점수 부족 ({enter_score:.1f} < {need})"
    return False, "통과"
