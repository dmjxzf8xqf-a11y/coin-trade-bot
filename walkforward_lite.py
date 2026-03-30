import time
from typing import Any, Dict, Iterable

try:
    from ai_learn import get_bucket_stats
except Exception:
    def get_bucket_stats(symbol: str, side: str, strategy: str, regime: str):
        return {"trades": 0, "weighted_winrate": 0.0, "weighted_score": 0.0}


def evaluate_symbol(symbol: str) -> Dict[str, Any]:
    rows = []
    for side in ("LONG", "SHORT"):
        for strategy in ("trend", "mean_reversion", "breakout", "unknown"):
            for regime in ("bull_strong", "bear_strong", "sideways", "high_volatility", "unknown"):
                b = get_bucket_stats(symbol, side, strategy, regime) or {}
                trades = int(b.get("trades", 0) or 0)
                if trades <= 0:
                    continue
                rows.append({
                    "side": side,
                    "strategy": strategy,
                    "regime": regime,
                    "trades": trades,
                    "weighted_winrate": float(b.get("weighted_winrate", b.get("winrate", 0.0)) or 0.0),
                    "weighted_score": float(b.get("weighted_score", 0.0) or 0.0),
                })
    rows.sort(key=lambda x: (x["weighted_score"], x["weighted_winrate"], x["trades"]), reverse=True)
    best = rows[0] if rows else None
    recommended = {"enter_score_delta": 0, "order_usdt_mult": 1.0, "lev_mult": 1.0}
    if best:
        wr = float(best["weighted_winrate"] or 0.0)
        sc = float(best["weighted_score"] or 0.0)
        if wr >= 0.60 or sc >= 8:
            recommended = {"enter_score_delta": -1, "order_usdt_mult": 1.08, "lev_mult": 1.03}
        elif wr <= 0.42 or sc <= -8:
            recommended = {"enter_score_delta": 2, "order_usdt_mult": 0.88, "lev_mult": 0.95}
    return {
        "symbol": symbol,
        "ts": int(time.time()),
        "best": best,
        "samples": len(rows),
        "recommended": recommended,
    }


def evaluate_portfolio(symbols: Iterable[str]) -> Dict[str, Any]:
    reports = [evaluate_symbol(s) for s in symbols]
    active = [r for r in reports if r.get("best")]
    if not active:
        return {"ts": int(time.time()), "reports": reports, "portfolio": {"enter_score_delta": 0, "order_usdt_mult": 1.0, "lev_mult": 1.0}}
    enter = sum(float(r["recommended"]["enter_score_delta"]) for r in active) / len(active)
    usdt = sum(float(r["recommended"]["order_usdt_mult"]) for r in active) / len(active)
    lev = sum(float(r["recommended"]["lev_mult"]) for r in active) / len(active)
    return {
        "ts": int(time.time()),
        "reports": reports,
        "portfolio": {
            "enter_score_delta": round(enter),
            "order_usdt_mult": round(usdt, 4),
            "lev_mult": round(lev, 4),
        },
    }
