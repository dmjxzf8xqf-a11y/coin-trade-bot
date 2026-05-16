"""freqstyle_research_patch_v1.py

Runtime patch that adds Freqtrade-style DB/research/tuning commands to the bot.

Commands:
- /research       high-level winrate + PnL + side/regime report
- /dbreport       full grouped DB report
- /weakness       worst side/symbol/regime/exit buckets
- /exitstats      exit reason performance
- /tune           adaptive tuner suggestions

This patch is intentionally observational by default. It records data and gives
recommendations. Score adjustment from DB can be enabled with ADAPTIVE_SCORE_ON.
"""

from __future__ import annotations

import os
import time
from typing import Any

try:
    import trader as _t
    from trader import Trader
except Exception as e:  # pragma: no cover
    print(f"[FREQSTYLE_PATCH] boot failed: {e}", flush=True)
    _t = None
    Trader = None  # type: ignore

try:
    import research_db_v1 as rdb
    import freqstyle_report_v1 as frep
    import adaptive_tuner_v1 as tuner
    import exit_quality_patch_v1  # noqa: F401 - installs score-drop guard
except Exception as e:  # pragma: no cover
    print(f"[FREQSTYLE_PATCH] deps failed: {e}", flush=True)
    rdb = None  # type: ignore
    frep = None  # type: ignore
    tuner = None  # type: ignore


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(str(os.getenv(name, str(default))).strip()))
    except Exception:
        return int(default)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _notify(self: Any, msg: str) -> None:
    try:
        if hasattr(self, "notify"):
            self.notify(msg)
        elif hasattr(self, "notify_throttled"):
            self.notify_throttled(msg, 60)
        else:
            print(msg, flush=True)
    except Exception:
        try:
            print(msg, flush=True)
        except Exception:
            pass


def _hours_arg(parts: list[str], default: int = 168) -> int:
    if len(parts) >= 2:
        try:
            h = int(float(parts[1]))
            return max(1, min(24 * 90, h))
        except Exception:
            return default
    return default


FREQSTYLE_RESEARCH_ON = _env_bool("FREQSTYLE_RESEARCH_ON", True)
ADAPTIVE_SCORE_ON = _env_bool("ADAPTIVE_SCORE_ON", False)

if _t is not None and Trader is not None and rdb is not None and frep is not None and FREQSTYLE_RESEARCH_ON:
    # 1) Log _score_symbol decisions and optionally adjust score from DB weakness.
    _orig_score_symbol = getattr(Trader, "_score_symbol", None)
    if callable(_orig_score_symbol):
        def _patched_score_symbol(self, symbol: str, price: float):
            info = _orig_score_symbol(self, symbol, price)
            try:
                if isinstance(info, dict):
                    side = str(info.get("side") or "").upper()
                    reason = str(info.get("reason") or "")
                    regime = rdb.extract_regime(reason)
                    score = _safe_float(info.get("score"), 0.0)
                    if ADAPTIVE_SCORE_ON and tuner is not None and side:
                        adj, why = tuner.score_adjustment(str(symbol), side, regime)
                        if adj:
                            info["score"] = max(0, min(100, int(score + adj)))
                            reason = reason + f"- ADAPTIVE_SCORE adj={adj} {why}\n"
                            info["reason"] = reason
                            score = _safe_float(info.get("score"), score)
                    mp = self._mp() if hasattr(self, "_mp") else {}
                    threshold = _safe_float(mp.get("enter_score"), rdb.extract_threshold(reason, 0.0)) if isinstance(mp, dict) else rdb.extract_threshold(reason, 0.0)
                    rdb.record_decision(
                        str(symbol),
                        side,
                        bool(info.get("ok")) and score >= threshold,
                        score=score,
                        threshold=threshold,
                        reason=reason,
                        strategy=str(info.get("strategy") or ""),
                        price=price,
                        raw={"info": info, "regime": regime},
                    )
            except Exception as e:
                try:
                    self.state["research_decision_error"] = str(e)
                except Exception:
                    pass
            return info
        Trader._score_symbol = _patched_score_symbol

    # 2) Record entries after actual append to self.positions.
    _orig_enter = getattr(Trader, "_enter", None)
    if callable(_orig_enter):
        def _patched_enter(self, symbol: str, side: str, price: float, reason: str, sl: float, tp: float, *args, **kwargs):
            before_n = len(getattr(self, "positions", []) or [])
            ret = _orig_enter(self, symbol, side, price, reason, sl, tp, *args, **kwargs)
            try:
                positions = getattr(self, "positions", []) or []
                if len(positions) > before_n:
                    pos = positions[-1]
                    if isinstance(pos, dict):
                        pos.setdefault("reason", reason)
                        pos.setdefault("entry_reason", reason)
                        pos.setdefault("regime", rdb.extract_regime(reason))
                        trade_id = rdb.record_entry(pos)
                        pos["research_trade_id"] = trade_id
                        self.state["research_last_entry_id"] = trade_id
            except Exception as e:
                try:
                    self.state["research_entry_error"] = str(e)
                except Exception:
                    pass
            return ret
        Trader._enter = _patched_enter

    # 3) Record exits with PnL delta and exit quality.
    _orig_exit_position = getattr(Trader, "_exit_position", None)
    if callable(_orig_exit_position):
        def _patched_exit_position(self, idx: int, why: str, force: bool = False):
            pos = None
            before_day = _safe_float(getattr(self, "day_profit", 0.0), 0.0)
            exit_price = None
            try:
                if 0 <= int(idx) < len(getattr(self, "positions", []) or []):
                    pos = dict(self.positions[idx])
                    try:
                        exit_price = float(_t.get_price(pos.get("symbol")))
                    except Exception:
                        exit_price = None
            except Exception:
                pos = None
            ret = _orig_exit_position(self, idx, why, force=force)
            try:
                if pos:
                    after_day = _safe_float(getattr(self, "day_profit", before_day), before_day)
                    pnl_delta = after_day - before_day
                    bucket = str(getattr(self, "state", {}).get("last_loss_bucket") or "")
                    trade_id = rdb.record_exit(pos, str(why), pnl_delta, exit_price=exit_price, loss_bucket=bucket)
                    self.state["research_last_exit_id"] = trade_id
            except Exception as e:
                try:
                    self.state["research_exit_error"] = str(e)
                except Exception:
                    pass
            return ret
        Trader._exit_position = _patched_exit_position

    # 4) Commands.
    _orig_handle_command = getattr(Trader, "handle_command", None)
    if callable(_orig_handle_command):
        def _patched_handle_command(self, text: str):
            parts = (text or "").strip().split()
            c0 = parts[0].lower() if parts else ""
            if c0 in ("/research", "/리서치"):
                _notify(self, frep.research(_hours_arg(parts, 168)))
                return
            if c0 in ("/dbreport", "/db"):
                _notify(self, frep.dbreport(_hours_arg(parts, 168)))
                return
            if c0 in ("/weakness", "/weak", "/약점"):
                _notify(self, frep.weakness(_hours_arg(parts, 168)))
                return
            if c0 in ("/exitstats", "/exits"):
                _notify(self, frep.exitstats(_hours_arg(parts, 168)))
                return
            if c0 in ("/tune", "/튜닝"):
                _notify(self, frep.tune(_hours_arg(parts, 168)))
                return
            return _orig_handle_command(self, text)
        Trader.handle_command = _patched_handle_command

    # 5) Public state small marker.
    _orig_public_state = getattr(Trader, "public_state", None)
    if callable(_orig_public_state):
        def _patched_public_state(self):
            ps = _orig_public_state(self)
            try:
                if isinstance(ps, dict):
                    ps["freqstyle_research"] = {
                        "on": True,
                        "db": str(getattr(rdb, "DB_PATH", "")),
                        "adaptive_score": ADAPTIVE_SCORE_ON,
                        "exit_quality": _env_bool("EXIT_QUALITY_ON", True),
                    }
            except Exception:
                pass
            return ps
        Trader.public_state = _patched_public_state

    print("[FREQSTYLE_PATCH] loaded: research DB + weakness/tune/exitstats commands", flush=True)
else:
    print("[FREQSTYLE_PATCH] disabled", flush=True)
