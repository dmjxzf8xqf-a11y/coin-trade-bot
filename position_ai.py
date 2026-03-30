import os
from typing import Any, Dict

try:
    from ai_learn import get_ai_stats, get_bucket_stats
except Exception:
    def get_ai_stats():
        return {"trades": 0, "winrate": 0.0, "pnl_sum": 0.0}
    def get_bucket_stats(symbol: str, side: str, strategy: str, regime: str):
        return {"trades": 0, "winrate": 0.0, "weighted_winrate": 0.0, "weighted_score": 0.0}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def ai_position_profile(symbol: str, side: str, strategy: str, regime: str, consec_losses: int = 0, day_profit: float = 0.0) -> Dict[str, Any]:
    g = get_ai_stats() or {}
    b = get_bucket_stats(symbol, side, strategy, regime) or {}

    global_trades = int(g.get("detail_trades", g.get("trades", 0)) or 0)
    global_wr = float(g.get("detail_winrate", g.get("winrate", 0.0)) or 0.0) / 100.0
    bucket_trades = int(b.get("trades", 0) or 0)
    bucket_wr = float(b.get("weighted_winrate", b.get("winrate", 0.0)) or 0.0)
    bucket_score = float(b.get("weighted_score", 0.0) or 0.0)

    size_mult = 1.0
    lev_mult = 1.0
    reason = []

    if global_trades >= 20:
        if global_wr >= 0.62:
            size_mult *= 1.10
            lev_mult *= 1.05
            reason.append("global_hot")
        elif global_wr <= 0.42:
            size_mult *= 0.85
            lev_mult *= 0.90
            reason.append("global_cold")

    if bucket_trades >= 8:
        if bucket_wr >= 0.60:
            size_mult *= 1.15
            lev_mult *= 1.05
            reason.append("bucket_hot")
        elif bucket_wr <= 0.40:
            size_mult *= 0.75
            lev_mult *= 0.90
            reason.append("bucket_cold")

    if bucket_score >= 8:
        size_mult *= 1.10
        reason.append("score_plus")
    elif bucket_score <= -8:
        size_mult *= 0.80
        reason.append("score_minus")

    if int(consec_losses or 0) >= 2:
        size_mult *= 0.80
        lev_mult *= 0.90
        reason.append("consec_loss_cut")
    if int(consec_losses or 0) >= 3:
        size_mult *= 0.65
        lev_mult *= 0.85
        reason.append("deep_loss_cut")

    if float(day_profit or 0.0) <= -25.0:
        size_mult *= 0.75
        lev_mult *= 0.90
        reason.append("day_dd_cut")

    size_mult = _clamp(size_mult, float(os.getenv("AI_SIZE_MIN", "0.45")), float(os.getenv("AI_SIZE_MAX", "1.60")))
    lev_mult = _clamp(lev_mult, float(os.getenv("AI_LEV_MIN", "0.80")), float(os.getenv("AI_LEV_MAX", "1.20")))

    confidence = (size_mult * 0.7) + (lev_mult * 0.3)
    return {
        "size_mult": round(size_mult, 4),
        "lev_mult": round(lev_mult, 4),
        "confidence": round(confidence, 4),
        "bucket_trades": bucket_trades,
        "bucket_wr": round(bucket_wr, 4),
        "global_wr": round(global_wr, 4),
        "bucket_score": round(bucket_score, 4),
        "reason": ",".join(reason) if reason else "neutral",
    }
