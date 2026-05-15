"""signal_engine_runtime_patch_v1.py

Runtime wiring: make trader.py use signal_engine.evaluate_signal() for live entries.
Import this after the old stability patch and before allin_guard_experimental_patch_v1
so later patches can still wrap the unified signal function.
"""

from __future__ import annotations

import os

try:
    import trader as _t
    from trader import Trader
    from signal_engine import evaluate_signal
except Exception as _e:  # pragma: no cover
    print(f"[SIGNAL_ENGINE_PATCH] boot failed: {_e}", flush=True)
    _t = None
    Trader = None  # type: ignore
    evaluate_signal = None  # type: ignore


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


SIGNAL_ENGINE_ON = _env_bool("SIGNAL_ENGINE_ON", True)

if _t is not None and Trader is not None and evaluate_signal is not None and SIGNAL_ENGINE_ON:
    def _compute_signal_and_exits(symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False):
        return evaluate_signal(symbol, side, price, mp, avoid_low_rsi=avoid_low_rsi, trader_module=_t)

    _t.compute_signal_and_exits = _compute_signal_and_exits

    def _method_compute_signal_and_exits(self, symbol: str, side: str, price: float, mp: dict, avoid_low_rsi: bool = False):
        return _t.compute_signal_and_exits(symbol, side, price, mp, avoid_low_rsi=avoid_low_rsi)

    Trader.compute_signal_and_exits = _method_compute_signal_and_exits
    print("[SIGNAL_ENGINE_PATCH] loaded: live/backtest-style signal unified", flush=True)
else:
    print("[SIGNAL_ENGINE_PATCH] disabled", flush=True)
