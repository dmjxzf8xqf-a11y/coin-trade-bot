from typing import Dict, Any

try:
    from ai_learn import get_bucket_stats
except Exception:
    def get_bucket_stats(symbol: str, side: str, strategy: str, regime: str):
        return {"trades": 0, "weighted_winrate": 0.0, "weighted_score": 0.0}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def get_symbol_weight(symbol: str, side: str = "LONG", strategy: str = "trend", regime: str = "sideways") -> float:
    b = get_bucket_stats(symbol, side, strategy, regime) or {}
    trades = int(b.get("trades", 0) or 0)
    wr = float(b.get("weighted_winrate", b.get("winrate", 0.0)) or 0.0)
    score = float(b.get("weighted_score", 0.0) or 0.0)

    base = {
        "BTCUSDT": 1.15,
        "ETHUSDT": 1.08,
        "SOLUSDT": 1.02,
        "XRPUSDT": 0.98,
    }.get((symbol or "").upper(), 1.0)

    if trades >= 6:
        if wr >= 0.58:
            base *= 1.10
        elif wr <= 0.42:
            base *= 0.88
    if score >= 8:
        base *= 1.08
    elif score <= -8:
        base *= 0.88
    return round(_clamp(base, 0.55, 1.35), 4)


def explain_symbol_weight(symbol: str, side: str = "LONG", strategy: str = "trend", regime: str = "sideways") -> Dict[str, Any]:
    w = get_symbol_weight(symbol, side, strategy, regime)
    return {"symbol": (symbol or "").upper(), "weight": w}
