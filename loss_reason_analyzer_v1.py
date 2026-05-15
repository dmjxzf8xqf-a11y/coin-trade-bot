"""loss_reason_analyzer_v1.py

Classify exit/loss reasons into buckets that can be counted and acted on.
The goal is not perfect ML. It is enough structure to stop guessing.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

try:
    from storage_utils import data_path, atomic_write_json, safe_read_json
except Exception:  # pragma: no cover
    def data_path(name: str) -> str:
        Path("data").mkdir(exist_ok=True)
        return str(Path("data") / name)

    def atomic_write_json(path: str, obj: Any, backup: bool = True) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def safe_read_json(path: str, default: Any) -> Any:
        try:
            p = Path(path)
            if not p.exists():
                return default
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _append_jsonl(path: str, obj: dict[str, Any]) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


LOSS_EVENTS_PATH = data_path("loss_events.jsonl")
LOSS_STATS_PATH = data_path("loss_reason_stats.json")


def classify_trade(pos: dict[str, Any], exit_reason: str, pnl: float, exit_price: float | None = None) -> str:
    if pnl >= 0:
        if "TRAIL" in str(exit_reason).upper():
            return "WIN_TRAILING_EXIT"
        if "TP" in str(exit_reason).upper():
            return "WIN_TAKE_PROFIT"
        return "WIN_OTHER"

    why = str(exit_reason or "").upper()
    side = str(pos.get("side") or "LONG").upper()
    entry = _safe_float(pos.get("entry_price"), 0.0)
    sl = _safe_float(pos.get("sl"), 0.0)
    tp = _safe_float(pos.get("tp"), 0.0)
    atr = _safe_float(pos.get("atr"), 0.0)
    score = _safe_float(pos.get("score"), 0.0)

    if "FEE" in why or "SLIPPAGE" in why:
        return "LOSS_FEE_SLIPPAGE"
    if "TIME" in why:
        return "LOSS_NO_FOLLOW_THROUGH_TIME"
    if "PANIC" in why or "FORCE" in why:
        return "LOSS_MANUAL_OR_PANIC"
    if "DCA" in why:
        return "LOSS_DCA_FAILED"
    if "TRAIL" in why:
        return "LOSS_TRAILING_REVERSAL"
    if "SL" in why or "STOP" in why or "손절" in why:
        if atr > 0 and entry > 0 and sl > 0:
            stop_dist = abs(entry - sl)
            if stop_dist / max(atr, 1e-9) < 1.1:
                return "LOSS_STOP_TOO_TIGHT"
        if score >= 85:
            return "LOSS_HIGH_SCORE_FAILED"
        return "LOSS_STOP_HIT"

    if exit_price and entry > 0:
        moved_against = (exit_price < entry) if side == "LONG" else (exit_price > entry)
        if moved_against:
            return "LOSS_TREND_REVERSAL"

    if score < 75:
        return "LOSS_LOW_QUALITY_ENTRY"
    return "LOSS_UNCLASSIFIED"


def record_trade_reason(pos: dict[str, Any], exit_reason: str, pnl: float, exit_price: float | None = None) -> str:
    reason = classify_trade(pos, exit_reason, pnl, exit_price=exit_price)
    event = {
        "ts": time.time(),
        "symbol": str(pos.get("symbol") or "").upper(),
        "side": str(pos.get("side") or "").upper(),
        "pnl": float(pnl),
        "exit_reason": str(exit_reason or ""),
        "bucket": reason,
        "entry_price": _safe_float(pos.get("entry_price"), 0.0),
        "exit_price": _safe_float(exit_price, 0.0),
        "score": _safe_float(pos.get("score"), 0.0),
        "strategy": str(pos.get("strategy") or ""),
    }
    _append_jsonl(LOSS_EVENTS_PATH, event)

    stats = safe_read_json(LOSS_STATS_PATH, {"total": 0, "buckets": {}, "symbols": {}})
    if not isinstance(stats, dict):
        stats = {"total": 0, "buckets": {}, "symbols": {}}
    stats["total"] = int(stats.get("total", 0) or 0) + 1
    stats.setdefault("buckets", {})[reason] = int(stats.setdefault("buckets", {}).get(reason, 0) or 0) + 1
    sym = event["symbol"] or "UNKNOWN"
    s = stats.setdefault("symbols", {}).setdefault(sym, {"total": 0, "buckets": {}})
    s["total"] = int(s.get("total", 0) or 0) + 1
    s.setdefault("buckets", {})[reason] = int(s.setdefault("buckets", {}).get(reason, 0) or 0) + 1
    stats["updated_ts"] = time.time()
    try:
        atomic_write_json(LOSS_STATS_PATH, stats)
    except Exception:
        pass
    return reason


def top_loss_buckets(limit: int = 5) -> list[tuple[str, int]]:
    stats = safe_read_json(LOSS_STATS_PATH, {"buckets": {}})
    buckets = stats.get("buckets", {}) if isinstance(stats, dict) else {}
    return sorted(((str(k), int(v)) for k, v in buckets.items()), key=lambda x: x[1], reverse=True)[:limit]
