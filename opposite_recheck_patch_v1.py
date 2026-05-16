"""opposite_recheck_patch_v1.py

Directional re-check patch.

Problem it solves:
- The old scorer can return the highest-score side even when that side is BLOCKED.
- Example: LONG score is high but blocked by RSI/fee/trend, while SHORT is lower but actually PASS.
- This patch re-evaluates both LONG/SHORT and prefers an OK side over a blocked higher-score side.

Safety:
- Default is DRY_RUN-only (`RECHECK_DRY_RUN_ONLY=true`).
- It does not bypass liquidity/spread/symbol blocks.
- Live use requires setting `RECHECK_DRY_RUN_ONLY=false` explicitly.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import trader as _t
    from trader import Trader
except Exception as e:  # pragma: no cover
    print(f"[OPPOSITE_RECHECK] boot failed: {e}", flush=True)
    _t = None
    Trader = None  # type: ignore

try:
    import research_db_v1 as rdb
except Exception:
    rdb = None  # type: ignore


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


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _reason_tag(reason: str) -> str:
    s = str(reason or "")
    if not s:
        return ""
    first = s.splitlines()[0].strip()
    for token in ("LIQ_BLOCK", "SPREAD", "SYMBOL_BLOCK", "ADV_BLOCK", "GLOBAL_LOCK", "KILL SWITCH"):
        if token in s:
            return token
    return first[:120]


def _non_bypass_safety_block(reason: str) -> bool:
    s = str(reason or "").upper()
    # Do not use reverse recheck to bypass market/order safety gates.
    hard_tokens = (
        "LIQ_BLOCK", "SPREAD", "SYMBOL_BLOCK", "ADV_BLOCK", "GLOBAL_LOCK",
        "KILL SWITCH", "TIME_FILTER", "COOLDOWN", "MAX_ENTRIES", "ORDER_LOCK",
        "DUP_ENTRY", "POSITION", "DISABLED UNTIL",
    )
    return any(t in s for t in hard_tokens)


RECHECK_ON = _env_bool("OPPOSITE_RECHECK_ON", True)
RECHECK_DRY_RUN_ONLY = _env_bool("RECHECK_DRY_RUN_ONLY", True)
RECHECK_MIN_SCORE = _env_float("RECHECK_MIN_SCORE", 0.0)
RECHECK_REQUIRE_OK = _env_bool("RECHECK_REQUIRE_OK", True)

if _t is not None and Trader is not None and RECHECK_ON:
    _orig_score_symbol = getattr(Trader, "_score_symbol", None)
    if callable(_orig_score_symbol):
        def _patched_score_symbol(self, symbol: str, price: float):
            info = _orig_score_symbol(self, symbol, price)
            try:
                dry_run = bool(getattr(_t, "DRY_RUN", False)) or _env_bool("DRY_RUN", False)
                if RECHECK_DRY_RUN_ONLY and not dry_run:
                    return info
                if not isinstance(info, dict):
                    return info
                if info.get("ok"):
                    return info

                prev_reason = str(info.get("reason") or "")
                if _non_bypass_safety_block(prev_reason):
                    return info

                if not callable(getattr(_t, "compute_signal_and_exits", None)):
                    return info

                mp = self._mp() if hasattr(self, "_mp") else {}
                if not isinstance(mp, dict):
                    mp = {}
                threshold = int(_safe_float(mp.get("enter_score"), _env_float("ENTER_SCORE_AGGRO", 65)))
                min_score = max(float(threshold), float(RECHECK_MIN_SCORE))
                avoid = bool(getattr(self, "state", {}).get("avoid_low_rsi", False))

                candidates: list[dict[str, Any]] = []
                for side in ("LONG", "SHORT"):
                    if side == "LONG" and not bool(getattr(self, "allow_long", True)):
                        continue
                    if side == "SHORT" and not bool(getattr(self, "allow_short", True)):
                        continue
                    try:
                        ok, reason, score, sl, tp, atr_v = _t.compute_signal_and_exits(
                            str(symbol), side, float(price or 0.0), mp, avoid_low_rsi=avoid
                        )
                        score_f = _safe_float(score, 0.0)
                        allowed = bool(ok) and score_f >= min_score
                        candidates.append({
                            "ok": bool(ok), "allowed": allowed, "side": side, "score": score_f,
                            "reason": str(reason or ""), "sl": sl, "tp": tp, "atr": atr_v,
                        })
                        if rdb is not None:
                            try:
                                rdb.record_decision(
                                    str(symbol), side, allowed, score=score_f, threshold=min_score,
                                    reason=str(reason or "") + f"\n- opposite_recheck_probe prev={_reason_tag(prev_reason)}\n",
                                    strategy=str(info.get("strategy") or "recheck"), price=float(price or 0.0),
                                    raw={"source": "opposite_recheck", "prev": prev_reason[:500]},
                                )
                            except Exception:
                                pass
                    except Exception as e:
                        try:
                            self.state["opposite_recheck_error"] = str(e)
                        except Exception:
                            pass

                good = [c for c in candidates if c.get("allowed")]
                if not good:
                    try:
                        self.state["opposite_recheck"] = {
                            "symbol": symbol,
                            "picked": None,
                            "prev": _reason_tag(prev_reason),
                            "candidates": [{"side": c["side"], "ok": c["ok"], "score": c["score"]} for c in candidates],
                        }
                    except Exception:
                        pass
                    return info

                # Prefer OK side. If both pass, highest score wins.
                best = sorted(good, key=lambda c: float(c.get("score") or 0.0), reverse=True)[0]
                new_reason = (
                    str(best.get("reason") or "")
                    + f"\n- OPPOSITE_RECHECK_PICK side={best['side']} score={best['score']:.1f} prev={_reason_tag(prev_reason)}\n"
                )
                out = dict(info)
                out.update({
                    "ok": True,
                    "side": best["side"],
                    "score": best["score"],
                    "reason": new_reason,
                    "sl": best.get("sl"),
                    "tp": best.get("tp"),
                    "atr": best.get("atr"),
                    "strategy": str(info.get("strategy") or "recheck"),
                    "opposite_recheck": True,
                })
                try:
                    self.state["opposite_recheck"] = {
                        "symbol": symbol,
                        "picked": best["side"],
                        "score": best["score"],
                        "prev": _reason_tag(prev_reason),
                    }
                except Exception:
                    pass
                return out
            except Exception as e:
                try:
                    self.state["opposite_recheck_error"] = str(e)
                except Exception:
                    pass
                return info

        Trader._score_symbol = _patched_score_symbol
        print("[OPPOSITE_RECHECK] loaded: prefer OK opposite side over blocked higher-score side", flush=True)
    else:
        print("[OPPOSITE_RECHECK] disabled: _score_symbol not found", flush=True)
else:
    print("[OPPOSITE_RECHECK] disabled", flush=True)
