import json
import time
from typing import Any, Dict, List, Optional

from storage_utils import data_path, safe_read_json, atomic_write_json

LEARN_FILE = data_path("learn_state.json")

DEFAULT_STATE: Dict[str, Any] = {
    "global": {"trades": 0, "wins": 0, "losses": 0, "pnl_sum": 0.0, "recent": []},
    "global_detail": {"trades": 0, "wins": 0, "losses": 0, "pnl_sum": 0.0, "recent": []},
    "buckets": {},
    "milestones": {"last_winrate_notice": 0},
}


def _deepcopy_default() -> Dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_STATE))


def _load() -> Dict[str, Any]:
    data = safe_read_json(LEARN_FILE, _deepcopy_default())
    if not isinstance(data, dict):
        return _deepcopy_default()
    data.setdefault("global", json.loads(json.dumps(DEFAULT_STATE["global"])))
    data.setdefault("global_detail", json.loads(json.dumps(DEFAULT_STATE["global_detail"])))
    data.setdefault("buckets", {})
    data.setdefault("milestones", {"last_winrate_notice": 0})
    return data


def _save(data: Dict[str, Any]) -> None:
    atomic_write_json(LEARN_FILE, data)


def _bucket_key(symbol: str, side: str, strategy: str, regime: str) -> str:
    return f"{(symbol or '').upper()}|{(side or '').upper()}|{strategy or 'unknown'}|{regime or 'unknown'}"


def _append_recent(arr: List[Dict[str, Any]], item: Dict[str, Any], maxlen: int = 80) -> None:
    arr.append(item)
    if len(arr) > maxlen:
        del arr[:-maxlen]


def _weighted_components(recent: List[Dict[str, Any]]) -> Dict[str, float]:
    if not recent:
        return {"winrate": 0.0, "avg_pnl": 0.0, "score": 0.0}

    total_w = 0.0
    win_score = 0.0
    pnl_score = 0.0
    n = len(recent)
    for i, r in enumerate(recent):
        age = (n - 1 - i)
        w = 0.92 ** age
        total_w += w
        pnl = float(r.get("pnl", 0.0) or 0.0)
        if pnl > 0:
            win_score += 1.0 * w
        pnl_score += pnl * w

    if total_w <= 0:
        return {"winrate": 0.0, "avg_pnl": 0.0, "score": 0.0}

    winrate = win_score / total_w
    avg_pnl = pnl_score / total_w
    # score: positive = easier entries allowed, negative = tighten up.
    score = ((winrate - 0.5) * 100.0 * 0.8) + (avg_pnl * 0.2)
    return {"winrate": winrate, "avg_pnl": avg_pnl, "score": score}


def _bucket_stats(bucket: Dict[str, Any]) -> Dict[str, Any]:
    trades = int(bucket.get("trades", 0) or 0)
    wins = int(bucket.get("wins", 0) or 0)
    losses = int(bucket.get("losses", 0) or 0)
    pnl_sum = float(bucket.get("pnl_sum", 0.0) or 0.0)
    comp = _weighted_components(bucket.get("recent", []))
    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "winrate": (wins / trades) if trades > 0 else 0.0,
        "pnl_sum": pnl_sum,
        "weighted_winrate": comp["winrate"],
        "weighted_avg_pnl": comp["avg_pnl"],
        "weighted_score": comp["score"],
    }


def record_trade_result(pnl: float) -> None:
    data = _load()
    g = data["global"]
    pnl = float(pnl or 0.0)
    g["trades"] = int(g.get("trades", 0) or 0) + 1
    if pnl > 0:
        g["wins"] = int(g.get("wins", 0) or 0) + 1
    elif pnl < 0:
        g["losses"] = int(g.get("losses", 0) or 0) + 1
    g["pnl_sum"] = float(g.get("pnl_sum", 0.0) or 0.0) + pnl
    _append_recent(g["recent"], {"ts": int(time.time()), "pnl": pnl}, maxlen=120)
    _save(data)


def record_trade_result_ex(
    pnl: float,
    symbol: str,
    side: str,
    strategy: str,
    regime: str,
    enter_score: float = 0.0,
    reason: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    data = _load()
    ts = int(time.time())
    pnl = float(pnl or 0.0)
    symbol = (symbol or "").upper()
    side = (side or "").upper()
    strategy = strategy or "unknown"
    regime = regime or "unknown"
    extra = extra or {}

    gd = data["global_detail"]
    gd["trades"] = int(gd.get("trades", 0) or 0) + 1
    if pnl > 0:
        gd["wins"] = int(gd.get("wins", 0) or 0) + 1
    elif pnl < 0:
        gd["losses"] = int(gd.get("losses", 0) or 0) + 1
    gd["pnl_sum"] = float(gd.get("pnl_sum", 0.0) or 0.0) + pnl
    _append_recent(gd["recent"], {
        "ts": ts,
        "pnl": pnl,
        "symbol": symbol,
        "side": side,
        "strategy": strategy,
        "regime": regime,
        "enter_score": float(enter_score or 0.0),
        "reason": reason,
        **extra,
    }, maxlen=120)

    key = _bucket_key(symbol, side, strategy, regime)
    b = data["buckets"].setdefault(key, {
        "symbol": symbol,
        "side": side,
        "strategy": strategy,
        "regime": regime,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "pnl_sum": 0.0,
        "recent": [],
    })
    b["trades"] = int(b.get("trades", 0) or 0) + 1
    if pnl > 0:
        b["wins"] = int(b.get("wins", 0) or 0) + 1
    elif pnl < 0:
        b["losses"] = int(b.get("losses", 0) or 0) + 1
    b["pnl_sum"] = float(b.get("pnl_sum", 0.0) or 0.0) + pnl
    _append_recent(b["recent"], {
        "ts": ts,
        "pnl": pnl,
        "enter_score": float(enter_score or 0.0),
        "reason": reason,
        **extra,
    }, maxlen=80)
    _save(data)


def get_bucket_stats(symbol: str, side: str, strategy: str, regime: str) -> Dict[str, Any]:
    data = _load()
    b = data["buckets"].get(_bucket_key(symbol, side, strategy, regime))
    if not b:
        return {"trades": 0, "wins": 0, "losses": 0, "winrate": 0.0, "pnl_sum": 0.0, "weighted_winrate": 0.0, "weighted_avg_pnl": 0.0, "weighted_score": 0.0}
    return _bucket_stats(b)


def get_bucket_score(symbol: str, side: str, strategy: str, regime: str) -> float:
    return float(get_bucket_stats(symbol, side, strategy, regime).get("weighted_score", 0.0) or 0.0)


def get_symbol_side_score(symbol: str, side: str) -> float:
    data = _load()
    vals = []
    for b in data.get("buckets", {}).values():
        if (b.get("symbol") or "").upper() == (symbol or "").upper() and (b.get("side") or "").upper() == (side or "").upper():
            vals.append(float(_bucket_stats(b).get("weighted_score", 0.0) or 0.0))
    return (sum(vals) / len(vals)) if vals else 0.0


def get_global_score() -> float:
    data = _load()
    gd = data.get("global_detail", {})
    return float(_weighted_components(gd.get("recent", [])).get("score", 0.0) or 0.0)


def get_recommended_score_adjustment(symbol: str, side: str, strategy: str, regime: str) -> Dict[str, Any]:
    bucket = get_bucket_stats(symbol, side, strategy, regime)
    bucket_trades = int(bucket.get("trades", 0) or 0)
    bucket_score = float(bucket.get("weighted_score", 0.0) or 0.0)
    symbol_side_score = float(get_symbol_side_score(symbol, side) or 0.0)
    global_score = float(get_global_score() or 0.0)

    # minimum sample guard
    min_bucket = 3
    if bucket_trades < min_bucket:
        bucket_score = 0.0

    raw = (bucket_score * 0.55) + (symbol_side_score * 0.30) + (global_score * 0.15)
    # convert learned score -> enter_score adjustment
    # positive adjustment = stricter, negative adjustment = easier entry
    if raw >= 12.0:
        adj = -5
    elif raw >= 7.0:
        adj = -3
    elif raw >= 3.5:
        adj = -1
    elif raw <= -12.0:
        adj = 6
    elif raw <= -7.0:
        adj = 4
    elif raw <= -3.5:
        adj = 2
    else:
        adj = 0

    return {
        "adjustment": int(adj),
        "bucket_trades": bucket_trades,
        "bucket_score": round(bucket_score, 4),
        "symbol_side_score": round(symbol_side_score, 4),
        "global_score": round(global_score, 4),
        "raw": round(raw, 4),
    }


def check_winrate_milestone() -> Optional[str]:
    stats = get_ai_stats()
    trades = int(stats.get("trades", 0) or 0)
    winrate = float(stats.get("winrate", 0.0) or 0.0)
    if trades < 20:
        return None
    if winrate >= 70.0:
        return f"AI winrate milestone: {winrate:.1f}% ({trades} trades)"
    if winrate <= 35.0:
        return f"AI warning: low winrate {winrate:.1f}% ({trades} trades)"
    return None


def get_ai_stats() -> Dict[str, Any]:
    data = _load()
    g = data.get("global", {})
    gd = data.get("global_detail", {})
    trades = int(g.get("trades", 0) or 0)
    wins = int(g.get("wins", 0) or 0)
    losses = int(g.get("losses", 0) or 0)
    winrate = (wins / trades * 100.0) if trades > 0 else 0.0
    detail_trades = int(gd.get("trades", 0) or 0)
    detail_wins = int(gd.get("wins", 0) or 0)
    detail_winrate = (detail_wins / detail_trades * 100.0) if detail_trades > 0 else 0.0
    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "winrate": round(winrate, 2),
        "pnl_sum": round(float(g.get("pnl_sum", 0.0) or 0.0), 4),
        "detail_trades": detail_trades,
        "detail_winrate": round(detail_winrate, 2),
        "global_score": round(get_global_score(), 4),
    }
# ===== ADVANCED AI WEIGHT PATCH =====
try:
    import math
    
    def _ai_symbol_weight(symbol, winrate, trades):
        try:
            if trades < 5:
                return 1.0
            
            if winrate > 0.65:
                return 1.3
            elif winrate > 0.55:
                return 1.15
            elif winrate < 0.40:
                return 0.8
            else:
                return 1.0
        except:
            return 1.0

    _orig_score_ai = globals().get("ai_score_adjust")

    if callable(_orig_score_ai):
        def ai_score_adjust(score, symbol=None, stats=None):
            base = _orig_score_ai(score, symbol, stats)
            
            try:
                if stats:
                    wr = stats.get("winrate", 0.5)
                    trades = stats.get("trades", 0)
                    
                    w = _ai_symbol_weight(symbol, wr, trades)
                    return base * w
            except:
                pass

            return base

except Exception:
    pass
