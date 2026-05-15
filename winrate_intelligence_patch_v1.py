"""winrate_intelligence_patch_v1.py

Runtime patch that wires the winrate-improvement modules into the current bot.

Adds:
- fee/slippage profitability guard before entry
- per-symbol adaptive block/boost
- loss reason classification on exit
- 24h Telegram report and commands

This file is intentionally conservative. It does not rewrite trader.py and every
piece is controlled by .env.
"""

from __future__ import annotations

import os
import time
from typing import Any

try:
    import trader as _t
    from trader import Trader
except Exception as _e:  # pragma: no cover
    print(f"[WINRATE_PATCH] boot failed: {_e}", flush=True)
    _t = None
    Trader = None  # type: ignore

try:
    from fee_profit_guard_v1 import evaluate_profit_guard
    import symbol_adaptive_filter_v1 as symfilter
    import loss_reason_analyzer_v1 as lossana
    import daily_reporter_v1 as dailyrep
except Exception as _e:  # pragma: no cover
    print(f"[WINRATE_PATCH] dependency import failed: {_e}", flush=True)
    evaluate_profit_guard = None  # type: ignore
    symfilter = None  # type: ignore
    lossana = None  # type: ignore
    dailyrep = None  # type: ignore


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


WINRATE_PATCH_ON = _env_bool("WINRATE_PATCH_ON", True)
LOSS_REASON_ON = _env_bool("LOSS_REASON_ON", True)
SYMBOL_ADAPTIVE_FILTER_ON = _env_bool("SYMBOL_ADAPTIVE_FILTER_ON", True)
FEE_PROFIT_GUARD_ON = _env_bool("FEE_PROFIT_GUARD_ON", True)
DAILY_REPORT_ON = _env_bool("DAILY_REPORT_ON", True)
SYMBOL_SCORE_ADJUST_ON = _env_bool("SYMBOL_SCORE_ADJUST_ON", True)


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


if _t is not None and Trader is not None and WINRATE_PATCH_ON:
    # 1) Wrap compute_signal_and_exits with fee/slippage profitability guard.
    _orig_compute = getattr(_t, "compute_signal_and_exits", None)
    if callable(_orig_compute) and callable(evaluate_profit_guard):
        def _patched_compute_signal_and_exits(symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False):
            ok, reason, score, sl, tp, atr_v = _orig_compute(symbol, side, price, mp, avoid_low_rsi=avoid_low_rsi)
            if FEE_PROFIT_GUARD_ON and ok:
                pass_fee, fee_msg, fee_metrics = evaluate_profit_guard(side, float(price or 0.0), sl, tp, trader_module=_t)
                if not pass_fee:
                    reason = f"{reason}- {fee_msg}\n"
                    return False, reason, score, sl, tp, atr_v
                reason = f"{reason}- {fee_msg}\n"
            return ok, reason, score, sl, tp, atr_v
        _t.compute_signal_and_exits = _patched_compute_signal_and_exits

        def _method_compute_signal_and_exits(self, symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False):
            return _t.compute_signal_and_exits(symbol, side, price, mp, avoid_low_rsi=avoid_low_rsi)
        Trader.compute_signal_and_exits = _method_compute_signal_and_exits

    # 2) Block bad symbols and slightly boost proven symbols.
    _orig_score_symbol = getattr(Trader, "_score_symbol", None)
    if callable(_orig_score_symbol) and symfilter is not None:
        def _patched_score_symbol(self, symbol: str, price: float):
            if SYMBOL_ADAPTIVE_FILTER_ON:
                blocked, msg = symfilter.is_symbol_blocked(symbol)
                if blocked:
                    self.state["symbol_filter"] = {"symbol": symbol, "blocked": True, "reason": msg}
                    return {"ok": False, "reason": msg, "strategy": "symbol_adaptive"}
            info = _orig_score_symbol(self, symbol, price)
            if isinstance(info, dict) and SYMBOL_ADAPTIVE_FILTER_ON and SYMBOL_SCORE_ADJUST_ON:
                try:
                    adj, why = symfilter.score_adjustment(symbol)
                    if info.get("ok") and adj:
                        info["score"] = int(max(0, min(100, int(info.get("score", 0) or 0) + int(adj))))
                    if info.get("reason"):
                        info["reason"] = str(info.get("reason")) + f"- {why} adj={adj}\n"
                    self.state["symbol_filter"] = {"symbol": symbol, "adjust": adj, "reason": why}
                except Exception as e:
                    self.state["symbol_filter_error"] = str(e)
            return info
        Trader._score_symbol = _patched_score_symbol

    # 3) Record final trade outcome, classify loss, update symbol stats.
    _orig_exit_position = getattr(Trader, "_exit_position", None)
    if callable(_orig_exit_position):
        def _patched_exit_position(self, idx: int, why: str, force: bool = False):
            pos = None
            before_day = _safe_float(getattr(self, "day_profit", 0.0), 0.0)
            exit_price = None
            try:
                if 0 <= idx < len(getattr(self, "positions", []) or []):
                    pos = dict(self.positions[idx])
                    try:
                        exit_price = float(_t.get_price(pos.get("symbol")))
                    except Exception:
                        exit_price = None
            except Exception:
                pos = None

            ret = _orig_exit_position(self, idx, why, force=force)

            if pos:
                after_day = _safe_float(getattr(self, "day_profit", before_day), before_day)
                pnl_delta = after_day - before_day
                bucket = ""
                try:
                    if LOSS_REASON_ON and lossana is not None:
                        bucket = lossana.record_trade_reason(pos, why, pnl_delta, exit_price=exit_price)
                        self.state["last_loss_bucket"] = bucket
                except Exception as e:
                    self.state["loss_reason_error"] = str(e)
                try:
                    if SYMBOL_ADAPTIVE_FILTER_ON and symfilter is not None:
                        s = symfilter.record_trade(pos.get("symbol", ""), pos.get("side", ""), pnl_delta, bucket)
                        self.state["last_symbol_stats"] = {
                            "symbol": pos.get("symbol"),
                            "recent_winrate": s.get("recent_winrate"),
                            "consec_losses": s.get("consec_losses"),
                            "blocked_until": s.get("blocked_until"),
                        }
                except Exception as e:
                    self.state["symbol_record_error"] = str(e)
            return ret
        Trader._exit_position = _patched_exit_position

    # 4) Add Telegram commands for data-driven tuning.
    _orig_handle_command = getattr(Trader, "handle_command", None)
    if callable(_orig_handle_command):
        def _patched_handle_command(self, text: str):
            cmd = (text or "").strip().split()
            c0 = cmd[0].lower() if cmd else ""
            if c0 in ("/daily", "/report", "/리포트"):
                if dailyrep is None:
                    _notify(self, "❌ daily_reporter_v1 로드 실패")
                else:
                    _notify(self, dailyrep.build_daily_report())
                return
            if c0 in ("/symbolscores", "/symbolscore", "/blocked", "/심볼통계"):
                if symfilter is None:
                    _notify(self, "❌ symbol_adaptive_filter_v1 로드 실패")
                else:
                    _notify(self, symfilter.summary(limit=15))
                return
            if c0 in ("/losses", "/loss", "/손실원인"):
                if lossana is None:
                    _notify(self, "❌ loss_reason_analyzer_v1 로드 실패")
                else:
                    tops = lossana.top_loss_buckets(limit=8)
                    if not tops:
                        _notify(self, "손실 원인 기록 없음")
                    else:
                        _notify("📉 손실/종료 원인 TOP\n" + "\n".join(f"- {k}: {v}" for k, v in tops))
                return
            return _orig_handle_command(self, text)
        Trader.handle_command = _patched_handle_command

    # 5) Auto daily report in tick loop.
    _orig_tick = getattr(Trader, "tick", None)
    if callable(_orig_tick):
        def _patched_tick(self):
            ret = _orig_tick(self)
            if DAILY_REPORT_ON and dailyrep is not None:
                try:
                    dailyrep.maybe_send_daily(self)
                except Exception as e:
                    self.state["daily_report_error"] = str(e)
            return ret
        Trader.tick = _patched_tick

    # 6) Expose summary in public_state without breaking existing output.
    _orig_public_state = getattr(Trader, "public_state", None)
    if callable(_orig_public_state):
        def _patched_public_state(self):
            ps = _orig_public_state(self)
            try:
                if isinstance(ps, dict):
                    ps["winrate_patch"] = {
                        "signal_engine": _env_bool("SIGNAL_ENGINE_ON", True),
                        "fee_guard": FEE_PROFIT_GUARD_ON,
                        "symbol_filter": SYMBOL_ADAPTIVE_FILTER_ON,
                        "loss_reason": LOSS_REASON_ON,
                        "daily_report": DAILY_REPORT_ON,
                        "last_loss_bucket": self.state.get("last_loss_bucket"),
                        "symbol_filter_state": self.state.get("symbol_filter"),
                    }
            except Exception:
                pass
            return ps
        Trader.public_state = _patched_public_state

    print("[WINRATE_PATCH] loaded: fee guard + symbol adaptive + loss reason + daily report", flush=True)
else:
    print("[WINRATE_PATCH] disabled", flush=True)
