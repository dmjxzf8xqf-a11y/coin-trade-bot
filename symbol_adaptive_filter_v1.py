"""symbol_adaptive_filter_v1.py

Per-symbol adaptive filter.
- Records recent wins/losses per symbol and side.
- Blocks symbols that keep losing.
- Slightly boosts symbols that fit the current strategy well.
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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(str(os.getenv(name, str(default))).strip()))
    except Exception:
        return int(default)


SYMBOL_STATS_PATH = data_path("symbol_stats.json")
SYMBOL_ADAPTIVE_FILTER_ON = _env_bool("SYMBOL_ADAPTIVE_FILTER_ON", True)
SYMBOL_MIN_TRADES = _env_int("SYMBOL_MIN_TRADES", 6)
SYMBOL_BLOCK_WR_BELOW = _env_float("SYMBOL_BLOCK_WR_BELOW", 35.0)
SYMBOL_BLOCK_CONSEC_LOSSES = _env_int("SYMBOL_BLOCK_CONSEC_LOSSES", 3)
SYMBOL_BLOCK_HOURS = _env_float("SYMBOL_BLOCK_HOURS", 24.0)
SYMBOL_RECENT_KEEP = _env_int("SYMBOL_RECENT_KEEP", 30)
SYMBOL_GOOD_WR_ABOVE = _env_float("SYMBOL_GOOD_WR_ABOVE", 60.0)
SYMBOL_GOOD_SCORE_BOOST = _env_int("SYMBOL_GOOD_SCORE_BOOST", 3)
SYMBOL_BAD_SCORE_PENALTY = _env_int("SYMBOL_BAD_SCORE_PENALTY", 3)


def _load() -> dict[str, Any]:
    obj = safe_read_json(SYMBOL_STATS_PATH, {"symbols": {}, "updated_ts": 0})
    if not isinstance(obj, dict):
        return {"symbols": {}, "updated_ts": 0}
    obj.setdefault("symbols", {})
    return obj


def _save(obj: dict[str, Any]) -> None:
    obj["updated_ts"] = time.time()
    try:
        atomic_write_json(SYMBOL_STATS_PATH, obj)
    except Exception:
        pass


def _sym_obj(data: dict[str, Any], symbol: str) -> dict[str, Any]:
    symbol = str(symbol or "UNKNOWN").upper()
    s = data.setdefault("symbols", {}).setdefault(symbol, {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "consec_losses": 0,
        "recent": [],
        "sides": {},
        "blocked_until": 0,
        "last_reason": "",
    })
    s.setdefault("recent", [])
    s.setdefault("sides", {})
    return s


def _recent_wr(recent: list[Any]) -> tuple[int, float]:
    vals = [int(x) for x in recent if int(x) in (0, 1)]
    n = len(vals)
    if n <= 0:
        return 0, 0.0
    return n, 100.0 * sum(vals) / n


def is_symbol_blocked(symbol: str) -> tuple[bool, str]:
    if not SYMBOL_ADAPTIVE_FILTER_ON:
        return False, "SYMBOL_FILTER_OFF"
    data = _load()
    s = _sym_obj(data, symbol)
    until = float(s.get("blocked_until", 0) or 0)
    if until > time.time():
        left = int(until - time.time())
        return True, f"SYMBOL_BLOCK {str(symbol).upper()} left={left}s reason={s.get('last_reason','')}"
    return False, ""


def record_trade(symbol: str, side: str, pnl: float, loss_bucket: str = "") -> dict[str, Any]:
    data = _load()
    symbol = str(symbol or "UNKNOWN").upper()
    side = str(side or "").upper()
    s = _sym_obj(data, symbol)
    win = 1 if float(pnl) >= 0 else 0
    s["total"] = int(s.get("total", 0) or 0) + 1
    if win:
        s["wins"] = int(s.get("wins", 0) or 0) + 1
        s["consec_losses"] = 0
    else:
        s["losses"] = int(s.get("losses", 0) or 0) + 1
        s["consec_losses"] = int(s.get("consec_losses", 0) or 0) + 1

    recent = list(s.get("recent", []) or [])
    recent.append(win)
    s["recent"] = recent[-max(5, SYMBOL_RECENT_KEEP):]

    if side:
        ss = s.setdefault("sides", {}).setdefault(side, {"total": 0, "wins": 0, "losses": 0, "recent": []})
        ss["total"] = int(ss.get("total", 0) or 0) + 1
        ss["wins"] = int(ss.get("wins", 0) or 0) + (1 if win else 0)
        ss["losses"] = int(ss.get("losses", 0) or 0) + (0 if win else 1)
        r2 = list(ss.get("recent", []) or [])
        r2.append(win)
        ss["recent"] = r2[-max(5, SYMBOL_RECENT_KEEP):]

    n, wr = _recent_wr(s["recent"])
    s["recent_winrate"] = wr
    s["last_pnl"] = float(pnl)
    s["last_loss_bucket"] = loss_bucket
    s["last_trade_ts"] = time.time()

    block_reason = ""
    if n >= SYMBOL_MIN_TRADES and wr < SYMBOL_BLOCK_WR_BELOW:
        block_reason = f"recent_wr={wr:.1f}%<{SYMBOL_BLOCK_WR_BELOW:.1f}% n={n}"
    if int(s.get("consec_losses", 0) or 0) >= SYMBOL_BLOCK_CONSEC_LOSSES:
        block_reason = f"consec_losses={s.get('consec_losses')}>={SYMBOL_BLOCK_CONSEC_LOSSES}"
    if block_reason:
        s["blocked_until"] = time.time() + SYMBOL_BLOCK_HOURS * 3600.0
        s["last_reason"] = block_reason

    _save(data)
    return s


def score_adjustment(symbol: str) -> tuple[int, str]:
    if not SYMBOL_ADAPTIVE_FILTER_ON:
        return 0, "SYMBOL_FILTER_OFF"
    data = _load()
    s = _sym_obj(data, symbol)
    n, wr = _recent_wr(list(s.get("recent", []) or []))
    if n < SYMBOL_MIN_TRADES:
        return 0, f"symbol_warmup {n}/{SYMBOL_MIN_TRADES}"
    if wr >= SYMBOL_GOOD_WR_ABOVE:
        return int(SYMBOL_GOOD_SCORE_BOOST), f"symbol_good wr={wr:.1f}% n={n}"
    if wr < SYMBOL_BLOCK_WR_BELOW + 10:
        return -int(SYMBOL_BAD_SCORE_PENALTY), f"symbol_weak wr={wr:.1f}% n={n}"
    return 0, f"symbol_neutral wr={wr:.1f}% n={n}"


def summary(limit: int = 10) -> str:
    data = _load()
    items = []
    for sym, s in (data.get("symbols", {}) or {}).items():
        n, wr = _recent_wr(list(s.get("recent", []) or []))
        until = float(s.get("blocked_until", 0) or 0)
        block = f" BLOCK {int((until-time.time())/3600)}h" if until > time.time() else ""
        items.append((n, wr, sym, block, int(s.get("consec_losses", 0) or 0)))
    items.sort(key=lambda x: (x[3] != "", -x[1], x[0]), reverse=True)
    if not items:
        return "심볼 통계 없음"
    lines = ["📊 심볼 적응 필터"]
    for n, wr, sym, block, cl in items[:limit]:
        lines.append(f"- {sym}: recent {wr:.1f}% ({n}) consecL={cl}{block}")
    return "\n".join(lines)
