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


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _coerce_recent(value: Any, maxlen: int = 120) -> List[Dict[str, Any]]:
    """Keep only dict rows so old/broken JSON cannot crash AI scoring."""
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
    return out[-maxlen:]


def _normalize_counter_bucket(value: Any, default: Dict[str, Any], recent_max: int = 120) -> Dict[str, Any]:
    """Normalize a stats object that should contain trades/wins/losses/pnl_sum/recent."""
    base = json.loads(json.dumps(default))
    if not isinstance(value, dict):
        return base

    base["trades"] = _to_int(value.get("trades", base.get("trades", 0)), 0)
    base["wins"] = _to_int(value.get("wins", base.get("wins", 0)), 0)
    base["losses"] = _to_int(value.get("losses", base.get("losses", 0)), 0)
    base["pnl_sum"] = _to_float(value.get("pnl_sum", base.get("pnl_sum", 0.0)), 0.0)
    base["recent"] = _coerce_recent(value.get("recent", []), recent_max)

    # Preserve harmless extra fields.
    for k, v in value.items():
        if k not in base and k != "recent":
            base[k] = v
    return base


def _bucket_key(symbol: str, side: str, strategy: str, regime: str) -> str:
    return f"{(symbol or '').upper()}|{(side or '').upper()}|{strategy or 'unknown'}|{regime or 'unknown'}"


def _normalize_state(data: Any) -> Dict[str, Any]:
    """Repair learn_state.json in memory and write it back safely.

    Fixes the common crash:
    AI_PATCH_ERR 'list' object has no attribute 'keys'

    Cause:
    - global/global_detail/buckets/recent can become a list or another invalid type.
    - The runtime AI patch expects dict-like objects.
    """
    default = _deepcopy_default()
    if not isinstance(data, dict):
        return default

    data["global"] = _normalize_counter_bucket(data.get("global"), default["global"], 120)
    data["global_detail"] = _normalize_counter_bucket(data.get("global_detail"), default["global_detail"], 120)

    raw_buckets = data.get("buckets", {})
    fixed_buckets: Dict[str, Any] = {}

    if isinstance(raw_buckets, dict):
        iterable = raw_buckets.items()
        for key, bucket in iterable:
            if not isinstance(bucket, dict):
                continue
            nb = _normalize_counter_bucket(
                bucket,
                {"trades": 0, "wins": 0, "losses": 0, "pnl_sum": 0.0, "recent": []},
                80,
            )
            nb["symbol"] = str(bucket.get("symbol") or nb.get("symbol") or "")
            nb["side"] = str(bucket.get("side") or nb.get("side") or "")
            nb["strategy"] = str(bucket.get("strategy") or nb.get("strategy") or "unknown")
            nb["regime"] = str(bucket.get("regime") or nb.get("regime") or "unknown")
            fixed_buckets[str(key)] = nb

    elif isinstance(raw_buckets, list):
        # Convert an accidental list of bucket objects into the expected dict shape.
        for bucket in raw_buckets:
            if not isinstance(bucket, dict):
                continue
            symbol = str(bucket.get("symbol") or "").upper()
            side = str(bucket.get("side") or "").upper()
            strategy = str(bucket.get("strategy") or "unknown")
            regime = str(bucket.get("regime") or "unknown")
            if not symbol or not side:
                continue
            key = _bucket_key(symbol, side, strategy, regime)
            nb = _normalize_counter_bucket(
                bucket,
                {"trades": 0, "wins": 0, "losses": 0, "pnl_sum": 0.0, "recent": []},
                80,
            )
            nb["symbol"] = symbol
            nb["side"] = side
            nb["strategy"] = strategy
            nb["regime"] = regime
            fixed_buckets[key] = nb

    data["buckets"] = fixed_buckets

    if not isinstance(data.get("milestones"), dict):
        data["milestones"] = json.loads(json.dumps(default["milestones"]))
    else:
        data["milestones"].setdefault("last_winrate_notice", 0)

    return data


def _load() -> Dict[str, Any]:
    data = safe_read_json(LEARN_FILE, _deepcopy_default())
    data = _normalize_state(data)
    # Persist the repaired shape so the same crash does not repeat every tick.
    try:
        atomic_write_json(LEARN_FILE, data)
    except Exception:
        pass
    return data


def _save(data: Dict[str, Any]) -> None:
    atomic_write_json(LEARN_FILE, _normalize_state(data))


def _append_recent(arr: List[Dict[str, Any]], item: Dict[str, Any], maxlen: int = 80) -> None:
    if not isinstance(arr, list):
        return
    if isinstance(item, dict):
        arr.append(item)
    if len(arr) > maxlen:
        del arr[:-maxlen]


def _weighted_components(recent: List[Dict[str, Any]]) -> Dict[str, float]:
    recent = _coerce_recent(recent, 500)
    if not recent:
        return {"winrate": 0.0, "avg_pnl": 0.0, "score": 0.0}

    total_w = 0.0
    win_score = 0.0
    pnl_score = 0.0
    n = len(recent)
    for i, r in enumerate(recent):
        age = n - 1 - i
        w = 0.92 ** age
        total_w += w
        pnl = _to_float(r.get("pnl", 0.0), 0.0)
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
    bucket = _normalize_counter_bucket(
        bucket,
        {"trades": 0, "wins": 0, "losses": 0, "pnl_sum": 0.0, "recent": []},
        80,
    )
    trades = _to_int(bucket.get("trades", 0), 0)
    wins = _to_int(bucket.get("wins", 0), 0)
    losses = _to_int(bucket.get("losses", 0), 0)
    pnl_sum = _to_float(bucket.get("pnl_sum", 0.0), 0.0)
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
    pnl = _to_float(pnl, 0.0)
    g["trades"] = _to_int(g.get("trades", 0), 0) + 1
    if pnl > 0:
        g["wins"] = _to_int(g.get("wins", 0), 0) + 1
    elif pnl < 0:
        g["losses"] = _to_int(g.get("losses", 0), 0) + 1
    g["pnl_sum"] = _to_float(g.get("pnl_sum", 0.0), 0.0) + pnl
    if not isinstance(g.get("recent"), list):
        g["recent"] = []
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
    pnl = _to_float(pnl, 0.0)
    symbol = (symbol or "").upper()
    side = (side or "").upper()
    strategy = strategy or "unknown"
    regime = regime or "unknown"
    extra = extra or {}

    gd = data["global_detail"]
    gd["trades"] = _to_int(gd.get("trades", 0), 0) + 1
    if pnl > 0:
        gd["wins"] = _to_int(gd.get("wins", 0), 0) + 1
    elif pnl < 0:
        gd["losses"] = _to_int(gd.get("losses", 0), 0) + 1
    gd["pnl_sum"] = _to_float(gd.get("pnl_sum", 0.0), 0.0) + pnl
    if not isinstance(gd.get("recent"), list):
        gd["recent"] = []
    _append_recent(gd["recent"], {
        "ts": ts,
        "pnl": pnl,
        "symbol": symbol,
        "side": side,
        "strategy": strategy,
        "regime": regime,
        "enter_score": _to_float(enter_score, 0.0),
        "reason": reason,
        **extra,
    }, maxlen=120)

    if not isinstance(data.get("buckets"), dict):
        data["buckets"] = {}

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
    if not isinstance(b, dict):
        b = {
            "symbol": symbol,
            "side": side,
            "strategy": strategy,
            "regime": regime,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "pnl_sum": 0.0,
            "recent": [],
        }
        data["buckets"][key] = b

    b["trades"] = _to_int(b.get("trades", 0), 0) + 1
    if pnl > 0:
        b["wins"] = _to_int(b.get("wins", 0), 0) + 1
    elif pnl < 0:
        b["losses"] = _to_int(b.get("losses", 0), 0) + 1
    b["pnl_sum"] = _to_float(b.get("pnl_sum", 0.0), 0.0) + pnl
    if not isinstance(b.get("recent"), list):
        b["recent"] = []
    _append_recent(b["recent"], {
        "ts": ts,
        "pnl": pnl,
        "enter_score": _to_float(enter_score, 0.0),
        "reason": reason,
        **extra,
    }, maxlen=80)
    _save(data)


def get_bucket_stats(symbol: str, side: str, strategy: str, regime: str) -> Dict[str, Any]:
    data = _load()
    b = data.get("buckets", {}).get(_bucket_key(symbol, side, strategy, regime))
    if not isinstance(b, dict):
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "winrate": 0.0,
            "pnl_sum": 0.0,
            "weighted_winrate": 0.0,
            "weighted_avg_pnl": 0.0,
            "weighted_score": 0.0,
        }
    return _bucket_stats(b)


def get_bucket_score(symbol: str, side: str, strategy: str, regime: str) -> float:
    return _to_float(get_bucket_stats(symbol, side, strategy, regime).get("weighted_score", 0.0), 0.0)


def get_symbol_side_score(symbol: str, side: str) -> float:
    data = _load()
    vals: List[float] = []
    buckets = data.get("buckets", {})
    if not isinstance(buckets, dict):
        return 0.0
    for b in buckets.values():
        if not isinstance(b, dict):
            continue
        if (b.get("symbol") or "").upper() == (symbol or "").upper() and (b.get("side") or "").upper() == (side or "").upper():
            vals.append(_to_float(_bucket_stats(b).get("weighted_score", 0.0), 0.0))
    return (sum(vals) / len(vals)) if vals else 0.0


def get_global_score() -> float:
    data = _load()
    gd = data.get("global_detail", {})
    if not isinstance(gd, dict):
        gd = {}
    return _to_float(_weighted_components(gd.get("recent", [])).get("score", 0.0), 0.0)


def get_recommended_score_adjustment(symbol: str, side: str, strategy: str, regime: str) -> Dict[str, Any]:
    bucket = get_bucket_stats(symbol, side, strategy, regime)
    bucket_trades = _to_int(bucket.get("trades", 0), 0)
    bucket_score = _to_float(bucket.get("weighted_score", 0.0), 0.0)
    symbol_side_score = _to_float(get_symbol_side_score(symbol, side), 0.0)
    global_score = _to_float(get_global_score(), 0.0)

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
    trades = _to_int(stats.get("trades", 0), 0)
    winrate = _to_float(stats.get("winrate", 0.0), 0.0)
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
    if not isinstance(g, dict):
        g = {}
    if not isinstance(gd, dict):
        gd = {}
    trades = _to_int(g.get("trades", 0), 0)
    wins = _to_int(g.get("wins", 0), 0)
    losses = _to_int(g.get("losses", 0), 0)
    winrate = (wins / trades * 100.0) if trades > 0 else 0.0
    detail_trades = _to_int(gd.get("trades", 0), 0)
    detail_wins = _to_int(gd.get("wins", 0), 0)
    detail_winrate = (detail_wins / detail_trades * 100.0) if detail_trades > 0 else 0.0
    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "winrate": round(winrate, 2),
        "pnl_sum": round(_to_float(g.get("pnl_sum", 0.0), 0.0), 4),
        "detail_trades": detail_trades,
        "detail_winrate": round(detail_winrate, 2),
        "global_score": round(get_global_score(), 4),
    }
