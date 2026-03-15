# ai_learn.py
import json
import time
from typing import Dict, Any, List
from storage_utils import data_path, safe_read_json, atomic_write_json

LEARN_FILE = data_path("learn_state.json")
DEFAULT_STATE = {"global": {"trades": 0, "wins": 0, "losses": 0, "pnl_sum": 0.0, "recent": []}, "buckets": {}}

def _deep_default() -> Dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_STATE))

def _load() -> Dict[str, Any]:
    data = safe_read_json(LEARN_FILE, _deep_default())
    if not isinstance(data, dict):
        return _deep_default()
    data.setdefault("global", _deep_default()["global"])
    data.setdefault("buckets", {})
    data["global"].setdefault("trades", 0)
    data["global"].setdefault("wins", 0)
    data["global"].setdefault("losses", 0)
    data["global"].setdefault("pnl_sum", 0.0)
    data["global"].setdefault("recent", [])
    return data

def _save(data: Dict[str, Any]) -> None:
    atomic_write_json(LEARN_FILE, data)

def _bucket_key(symbol: str, side: str, strategy: str, regime: str) -> str:
    return f"{symbol}|{side}|{strategy}|{regime}"

def _append_recent(arr: List[dict], item: dict, maxlen: int = 120) -> None:
    arr.append(item)
    if len(arr) > maxlen:
        del arr[:-maxlen]

def _weighted_score(recent: List[dict]) -> float:
    if not recent:
        return 0.0
    total_w = 0.0
    win_score = 0.0
    pnl_score = 0.0
    n = len(recent)
    for i, r in enumerate(recent):
        age = (n - 1 - i)
        w = 0.93 ** age
        total_w += w
        pnl = float(r.get("pnl", 0.0))
        won = 1.0 if pnl > 0 else 0.0
        win_score += won * w
        pnl_score += pnl * w
    if total_w <= 0:
        return 0.0
    winrate = win_score / total_w
    avg_pnl = pnl_score / total_w
    return round((winrate * 100.0 * 0.7) + (avg_pnl * 0.3), 4)

def record_trade_result(pnl: float) -> None:
    data = _load()
    g = data["global"]
    g["trades"] += 1
    if pnl > 0:
        g["wins"] += 1
    elif pnl < 0:
        g["losses"] += 1
    g["pnl_sum"] += float(pnl)
    _append_recent(g["recent"], {"ts": int(time.time()), "pnl": float(pnl)})
    _save(data)

def record_trade_result_ex(pnl: float, symbol: str, side: str, strategy: str, regime: str, enter_score: float = 0.0, reason: str = "") -> None:
    data = _load()
    g = data["global"]
    g["trades"] += 1
    if pnl > 0:
        g["wins"] += 1
    elif pnl < 0:
        g["losses"] += 1
    g["pnl_sum"] += float(pnl)
    _append_recent(g["recent"], {"ts": int(time.time()), "pnl": float(pnl), "symbol": symbol, "side": side, "strategy": strategy, "regime": regime, "enter_score": float(enter_score), "reason": reason})
    key = _bucket_key(symbol, side, strategy, regime)
    b = data["buckets"].setdefault(key, {"symbol": symbol, "side": side, "strategy": strategy, "regime": regime, "trades": 0, "wins": 0, "losses": 0, "pnl_sum": 0.0, "recent": []})
    b["trades"] += 1
    if pnl > 0:
        b["wins"] += 1
    elif pnl < 0:
        b["losses"] += 1
    b["pnl_sum"] += float(pnl)
    _append_recent(b["recent"], {"ts": int(time.time()), "pnl": float(pnl), "enter_score": float(enter_score), "reason": reason})
    _save(data)

def get_bucket_score(symbol: str, side: str, strategy: str, regime: str) -> float:
    data = _load()
    b = data["buckets"].get(_bucket_key(symbol, side, strategy, regime))
    return _weighted_score(b.get("recent", [])) if b else 0.0

def get_symbol_side_score(symbol: str, side: str) -> float:
    data = _load()
    scores = []
    for b in data["buckets"].values():
        if b.get("symbol") == symbol and b.get("side") == side:
            scores.append(_weighted_score(b.get("recent", [])))
    return round(sum(scores) / len(scores), 4) if scores else 0.0

def get_global_score() -> float:
    data = _load()
    return _weighted_score(data["global"].get("recent", []))

def check_winrate_milestone():
    stats = get_ai_stats()
    wr = float(stats.get("winrate", 0.0))
    if wr >= 70 and int(stats.get("trades", 0)) >= 20:
        return f"ð AI ì¹ë¥  ì¢ì: {wr:.1f}%"
    if wr <= 35 and int(stats.get("trades", 0)) >= 20:
        return f"â ï¸ AI ì¹ë¥  ë®ì: {wr:.1f}%"
    return None

def get_ai_stats():
    data = _load()
    g = data["global"]
    trades = int(g.get("trades", 0))
    wins = int(g.get("wins", 0))
    losses = int(g.get("losses", 0))
    winrate = (wins / trades * 100.0) if trades > 0 else 0.0
    return {"trades": trades, "wins": wins, "losses": losses, "winrate": round(winrate, 2), "pnl_sum": round(float(g.get("pnl_sum", 0.0)), 4), "global_score": round(get_global_score(), 4)}
