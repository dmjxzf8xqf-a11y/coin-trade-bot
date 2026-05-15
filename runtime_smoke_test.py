#!/usr/bin/env python3
"""runtime_smoke_test.py

Fast offline runtime smoke test for the trading bot repo.

What this verifies:
- Core Python files compile.
- Runtime patches import in safe DRY_RUN mode.
- The unified signal function keeps its expected 6-value API.
- Trader command/status hooks exist after patch stacking.

It does NOT place orders and does NOT call Bybit/Telegram. Market functions are
monkey-patched with fake data before signal evaluation.
"""

from __future__ import annotations

import compileall
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _set_safe_env() -> None:
    safe_defaults = {
        # Never trade during smoke tests.
        "DRY_RUN": "true",
        "BOT_TOKEN": "",
        "CHAT_ID": "123456789",
        "TG_STRICT_CHAT": "true",
        "HEALTH_VERBOSE": "false",
        "BYBIT_BASE_URL": "https://api.bybit.com",
        "POSITION_MODE": "ONEWAY",
        # Keep patches importable but disable live/order side effects.
        "STAB_PATCH_ON": "true",
        "SIGNAL_ENGINE_ON": "true",
        "ALLIN_PATCH_ON": "true",
        "WINRATE_PATCH_ON": "true",
        "OPS_PATCH_ON": "true",
        "OPS_SAFETY_ON": "false",
        "EXCHANGE_SLTP_ON": "false",
        "POSITION_RECONCILE_ON": "false",
        "DECISION_LOG_ON": "false",
        "FEE_PROFIT_GUARD_ON": "false",
        "SYMBOL_ADAPTIVE_FILTER_ON": "false",
        "LOSS_REASON_ON": "false",
        "DAILY_REPORT_ON": "false",
        # Keep experimental features off unless explicitly tested elsewhere.
        "AI_AUTO_LEVERAGE": "false",
        "DL_LITE_ON": "false",
        "DCA_ON": "false",
        "EXPERIMENTAL_MULTI_POS_ON": "false",
        "EXPERIMENTAL_SCALP_MODE_ON": "false",
        "EXPERIMENTAL_DCA_ON": "false",
        "ALLOW_SHORT": "false",
        "DIVERSIFY": "false",
        "MAX_POSITIONS": "1",
        # Indicator thresholds lenient enough for fake data.
        "SIGNAL_MIN_ADX": "0",
        "SIGNAL_MIN_ATR_PCT": "0",
        "SIGNAL_MAX_ATR_PCT": "1",
        "SIGNAL_MIN_EMA_GAP_PCT": "0",
        "SIGNAL_ANTI_CHASE_ATR": "99",
    }
    for k, v in safe_defaults.items():
        os.environ.setdefault(k, v)


def _fake_klines(up: bool = True, n: int = 260):
    """Return Bybit-like klines newest-first."""
    rows = []
    price = 100.0
    for i in range(n):
        drift = 0.05 if up else -0.05
        wave = ((i % 7) - 3) * 0.01
        open_p = price
        close = max(1.0, price + drift + wave)
        high = max(open_p, close) + 0.35
        low = min(open_p, close) - 0.35
        vol = 1000 + i
        ts = int((time.time() - (n - i) * 60) * 1000)
        rows.append([str(ts), f"{open_p:.6f}", f"{high:.6f}", f"{low:.6f}", f"{close:.6f}", f"{vol:.6f}", f"{vol * close:.6f}"])
        price = close
    return list(reversed(rows))


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    _set_safe_env()

    print("[SMOKE] compileall...")
    ok = compileall.compile_dir(str(ROOT), quiet=1, maxlevels=10)
    _assert(ok, "compileall failed")

    print("[SMOKE] importing main/runtime patches...")
    import main as bot_main  # noqa: F401
    import trader as t

    print("[SMOKE] monkeypatching market data...")
    t.get_klines = lambda symbol, interval, limit=240: _fake_klines(up=True, n=max(260, int(limit or 240)))
    t.get_price = lambda symbol: 112.5
    t.get_ticker = lambda symbol: {"bid1Price": "112.49", "ask1Price": "112.51", "turnover24h": "100000000"}

    _assert(hasattr(t, "compute_signal_and_exits"), "trader.compute_signal_and_exits missing")
    mp = {"enter_score": 1, "lev": 3, "order_usdt": 10, "stop_mult": 1.8, "tp_r": 1.5}

    print("[SMOKE] evaluating LONG signal shape...")
    res_long = t.compute_signal_and_exits("BTCUSDT", "LONG", 112.5, mp, avoid_low_rsi=False)
    _assert(isinstance(res_long, tuple) and len(res_long) == 6, f"bad LONG signal result: {res_long!r}")

    print("[SMOKE] evaluating SHORT signal shape...")
    res_short = t.compute_signal_and_exits("BTCUSDT", "SHORT", 112.5, mp, avoid_low_rsi=False)
    _assert(isinstance(res_short, tuple) and len(res_short) == 6, f"bad SHORT signal result: {res_short!r}")

    tr = getattr(bot_main, "trader", None)
    _assert(tr is not None, "main.trader missing")
    _assert(callable(getattr(tr, "handle_command", None)), "Trader.handle_command missing")
    _assert(callable(getattr(tr, "tick", None)), "Trader.tick missing")
    _assert(callable(getattr(tr, "_mp", None)), "Trader._mp missing")
    _assert(isinstance(tr._mp(), dict), "Trader._mp() did not return dict")

    ps = tr.public_state() if callable(getattr(tr, "public_state", None)) else {}
    _assert(isinstance(ps, dict), "Trader.public_state() did not return dict")

    print("[SMOKE] OK")
    print(f"[SMOKE] LONG={res_long}")
    print(f"[SMOKE] SHORT={res_short}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"[SMOKE] FAIL: {e}", file=sys.stderr)
        raise
