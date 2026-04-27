"""AI score runtime patch.

What it does:
1) Uses ai_learn bucket memory to adjust live entry scores.
2) Persists entry context (regime / strategy / raw score / ai adjust).
3) Records detailed trade outcomes back into ai_learn on exit.
4) Exposes lightweight debug info in trader.state.

Import this once from main.py after importing trader.
"""

import os
import time

import trader as trader_module
from trader import Trader

try:
    from ai_learn import (
        get_recommended_score_adjustment,
        record_trade_result_ex,
        get_ai_stats,
    )
except Exception:  # pragma: no cover
    def get_recommended_score_adjustment(*args, **kwargs):
        return {"adjustment": 0, "bucket_trades": 0, "bucket_score": 0.0, "symbol_side_score": 0.0, "global_score": 0.0, "raw": 0.0}

    def record_trade_result_ex(*args, **kwargs):
        return None

    def get_ai_stats():
        return {"trades": 0, "wins": 0, "losses": 0, "winrate": 0.0, "detail_trades": 0, "detail_winrate": 0.0, "global_score": 0.0}


AI_SCORE_PATCH_ON = str(os.getenv("AI_SCORE_PATCH_ON", "true")).lower() in ("1", "true", "yes", "y", "on")
AI_SCORE_ADJ_MIN = int(os.getenv("AI_SCORE_ADJ_MIN", "-6"))
AI_SCORE_ADJ_MAX = int(os.getenv("AI_SCORE_ADJ_MAX", "6"))
AI_SIZE_BONUS_MAX = float(os.getenv("AI_SIZE_BONUS_MAX", "0.30"))
AI_SIZE_PENALTY_MAX = float(os.getenv("AI_SIZE_PENALTY_MAX", "0.35"))


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _safe_regime(symbol: str) -> str:
    try:
        return str(trader_module.detect_market_regime(symbol) or "range")
    except Exception:
        return "range"


if AI_SCORE_PATCH_ON:
    _orig_score_symbol = getattr(Trader, "_score_symbol", None)
    if callable(_orig_score_symbol):
        def _score_symbol_ai_patch(self, symbol: str, price: float):
            info = _orig_score_symbol(self, symbol, price)
            if not isinstance(info, dict):
                return info
            if not info.get("ok"):
                return info

            side = str(info.get("side") or "")
            strategy = str(info.get("strategy") or "unknown")
            regime = _safe_regime(symbol)
            rec = get_recommended_score_adjustment(symbol, side, strategy, regime)
            adj = int(_clamp(int(rec.get("adjustment", 0) or 0), AI_SCORE_ADJ_MIN, AI_SCORE_ADJ_MAX))
            raw_score = float(info.get("score", 0.0) or 0.0)
            patched_score = float(_clamp(raw_score - adj, 0, 100))
            # negative adj means easier entry => score gets boosted here against fixed threshold.
            info["raw_score"] = raw_score
            info["score"] = patched_score
            info["ai_adjustment"] = adj
            info["regime"] = regime
            info["ai_meta"] = rec
            try:
                info["reason"] = f"{info.get('reason','')}\n- ai_adj={adj} raw={raw_score:.1f} patched={patched_score:.1f} regime={regime}"
            except Exception:
                pass
            self.state["last_ai_score_patch"] = {
                "ts": int(time.time()),
                "symbol": symbol,
                "side": side,
                "strategy": strategy,
                "regime": regime,
                "raw_score": round(raw_score, 3),
                "patched_score": round(patched_score, 3),
                "adjustment": adj,
                "meta": rec,
            }
            return info

        Trader._score_symbol = _score_symbol_ai_patch

    _orig_enter = getattr(Trader, "_enter", None)
    if callable(_orig_enter):
        def _enter_ai_patch(self, symbol: str, side: str, price: float, reason: str, sl: float, tp: float, *args, **kwargs):
            before_n = len(getattr(self, "positions", []) or [])

            # _enter() is commonly called with positional args:
            # _enter(symbol, side, price, reason, sl, tp, strategy, score, atr)
            # Older patch only read kwargs, so strategy/score/atr were often saved as unknown/0.
            strategy = kwargs.get("strategy", None)
            score = kwargs.get("score", None)
            atr = kwargs.get("atr", None)
            ai_adjustment = kwargs.get("ai_adjustment", 0.0)

            if strategy is None and len(args) >= 1:
                strategy = args[0]
            if score is None and len(args) >= 2:
                score = args[1]
            if atr is None and len(args) >= 3:
                atr = args[2]

            strategy = str(strategy or "unknown")
            score = float(score or 0.0)
            atr = float(atr or 0.0)
            regime = _safe_regime(symbol)
            # size scaling by learned score quality.
            mp = None
            try:
                mp = self._mp()
            except Exception:
                mp = None
            if isinstance(mp, dict):
                rec = get_recommended_score_adjustment(symbol, side, strategy, regime)
                adj = int(_clamp(int(rec.get("adjustment", 0) or 0), AI_SCORE_ADJ_MIN, AI_SCORE_ADJ_MAX))
                current_order = float(mp.get("order_usdt", 0.0) or 0.0)
                if adj <= -3:
                    mult = 1.0 + min(AI_SIZE_BONUS_MAX, abs(adj) * 0.05)
                    mp["order_usdt"] = current_order * mult
                elif adj >= 3:
                    mult = 1.0 - min(AI_SIZE_PENALTY_MAX, adj * 0.05)
                    mp["order_usdt"] = max(1.0, current_order * mult)
                    self.state["last_ai_size_mult"] = {"symbol": symbol, "adj": adj, "mult": round(mult, 4)}
            out = _orig_enter(self, symbol, side, price, reason, sl, tp, *args, **kwargs)
            try:
                if len(self.positions) > before_n:
                    pos = self.positions[-1]
                    pos["regime"] = regime
                    pos["entry_reason_text"] = reason
                    pos["entry_score_raw"] = float(score or 0.0)
                    pos["entry_score_final"] = float(score or 0.0)
                    pos["ai_adjustment"] = float(ai_adjustment or 0.0)
                    pos["atr_at_entry"] = float(atr or 0.0)
            except Exception:
                pass
            return out

        Trader._enter = _enter_ai_patch

    _orig_exit_position = getattr(Trader, "_exit_position", None)
    if callable(_orig_exit_position):
        def _exit_position_ai_patch(self, idx: int, why: str, force=False):
            pos = None
            try:
                if 0 <= idx < len(self.positions):
                    pos = dict(self.positions[idx])
            except Exception:
                pos = None

            pnl_est = None
            if pos:
                try:
                    symbol = pos.get("symbol") or ""
                    side = pos.get("side") or ""
                    entry_price = float(pos.get("entry_price") or 0.0)
                    price = float(trader_module.get_price(symbol))
                    last_order_usdt = float(pos.get("last_order_usdt") or 0.0)
                    last_lev = float(pos.get("last_lev") or 0.0)
                    notional = last_order_usdt * last_lev
                    pnl_real = trader_module._get_realized_pnl_usdt(symbol, float(pos.get("entry_ts") or 0.0))
                    pnl_est = pnl_real if (pnl_real is not None) else trader_module.estimate_pnl_usdt(side, entry_price, price, notional)
                except Exception:
                    pnl_est = None

            out = _orig_exit_position(self, idx, why, force=force)

            if pos and (pnl_est is not None):
                try:
                    record_trade_result_ex(
                        pnl=float(pnl_est),
                        symbol=str(pos.get("symbol") or ""),
                        side=str(pos.get("side") or ""),
                        strategy=str(pos.get("strategy") or "unknown"),
                        regime=str(pos.get("regime") or _safe_regime(str(pos.get("symbol") or ""))),
                        enter_score=float(pos.get("entry_score_final") or pos.get("entry_score") or 0.0),
                        reason=str(why or ""),
                        extra={
                            "ai_adjustment": float(pos.get("ai_adjustment") or 0.0),
                            "entry_score_raw": float(pos.get("entry_score_raw") or pos.get("entry_score") or 0.0),
                            "atr_at_entry": float(pos.get("atr_at_entry") or 0.0),
                        },
                    )
                    self.state["last_ai_record"] = {
                        "symbol": str(pos.get("symbol") or ""),
                        "side": str(pos.get("side") or ""),
                        "pnl": round(float(pnl_est), 4),
                        "why": str(why or ""),
                    }
                except Exception as e:
                    self.state["last_ai_record_error"] = str(e)
            return out

        Trader._exit_position = _exit_position_ai_patch

    _orig_status_text = getattr(Trader, "status_text", None)
    if callable(_orig_status_text):
        def _status_text_ai_patch(self, *args, **kwargs):
            base = _orig_status_text(self, *args, **kwargs)
            try:
                s = get_ai_stats()
                extra = (
                    f"\nð§  AI detail_trades={int(s.get('detail_trades', 0) or 0)}"
                    f" | detail_wr={float(s.get('detail_winrate', 0.0) or 0.0):.1f}%"
                    f" | global_score={float(s.get('global_score', 0.0) or 0.0):.2f}"
                )
                return f"{base}{extra}"
            except Exception:
                return base

        Trader.status_text = _status_text_ai_patch
