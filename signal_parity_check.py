#!/usr/bin/env python3
"""signal_parity_check.py

Offline parity check for the trading bot signal path.

Purpose
- Prove that live runtime calls are actually going through signal_engine.py.
- Compare these three paths for the same market window:
    1) signal_engine.evaluate_signal(...)
    2) trader.compute_signal_and_exits(...)
    3) main.trader.compute_signal_and_exits(...)
- Fail fast if any later patch silently replaces or mutates the signal result.

This script never places orders and never calls Bybit/Telegram. It monkey-patches
trader.get_klines/get_price/get_ticker with offline data before evaluation.

Usage
    python signal_parity_check.py
    python signal_parity_check.py --csv data/BTCUSDT_15m.csv --samples 25
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent


SAFE_ENV = {
    # Never trade or expose anything during the parity check.
    "DRY_RUN": "true",
    "BOT_TOKEN": "",
    "CHAT_ID": "123456789",
    "TG_STRICT_CHAT": "true",
    "HEALTH_VERBOSE": "false",
    "BYBIT_BASE_URL": "https://api.bybit.com",
    "POSITION_MODE": "ONEWAY",
    # Keep the live patch stack importable.
    "STAB_PATCH_ON": "true",
    "SIGNAL_ENGINE_ON": "true",
    "ALLIN_PATCH_ON": "true",
    "WINRATE_PATCH_ON": "true",
    "OPS_PATCH_ON": "true",
    # Disable wrappers that intentionally alter signal_engine output.
    "OPS_SAFETY_ON": "false",
    "EXCHANGE_SLTP_ON": "false",
    "POSITION_RECONCILE_ON": "false",
    "DECISION_LOG_ON": "false",
    "FEE_PROFIT_GUARD_ON": "false",
    "SYMBOL_ADAPTIVE_FILTER_ON": "false",
    "LOSS_REASON_ON": "false",
    "DAILY_REPORT_ON": "false",
    "AI_AUTO_LEVERAGE": "false",
    "DL_LITE_ON": "false",
    "DCA_ON": "false",
    "EXPERIMENTAL_MULTI_POS_ON": "false",
    "EXPERIMENTAL_SCALP_MODE_ON": "false",
    "EXPERIMENTAL_DCA_ON": "false",
    "DIVERSIFY": "false",
    "MAX_POSITIONS": "1",
    # Lenient thresholds so synthetic tests pass/block for strategy reasons, not
    # because fake data is too narrow.
    "SIGNAL_MIN_ADX": "0",
    "SIGNAL_MIN_ATR_PCT": "0",
    "SIGNAL_MAX_ATR_PCT": "1",
    "SIGNAL_MIN_EMA_GAP_PCT": "0",
    "SIGNAL_ANTI_CHASE_ATR": "99",
    "SIGNAL_HARD_VOLUME": "false",
    "SIGNAL_HTF_ON": "false",
    "SIGNAL_HTF_HARD": "false",
    "SIGNAL_EMA200_HARD": "false",
}


def set_safe_env() -> None:
    for k, v in SAFE_ENV.items():
        os.environ.setdefault(k, v)


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 1000.0


def _float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _ts_to_ms(v: Any, fallback_i: int) -> int:
    s = str(v or "").strip()
    if not s:
        return int((time.time() - (10_000 - fallback_i) * 60) * 1000)
    try:
        raw = float(s)
        if raw > 10_000_000_000:
            return int(raw)
        if raw > 1_000_000_000:
            return int(raw * 1000)
    except Exception:
        pass
    return int((time.time() - (10_000 - fallback_i) * 60) * 1000)


def synthetic_candles(kind: str, n: int = 280) -> list[Candle]:
    rows: list[Candle] = []
    price = 100.0
    now = int(time.time() * 1000)
    kind = kind.lower().strip()
    for i in range(n):
        if kind == "down":
            drift = -0.045
        elif kind == "range":
            drift = 0.0
        else:
            drift = 0.045
        wave = ((i % 9) - 4) * 0.012
        open_p = price
        close = max(1.0, price + drift + wave)
        high = max(open_p, close) + 0.35 + (i % 3) * 0.015
        low = min(open_p, close) - 0.35 - (i % 4) * 0.015
        vol = 1000.0 + i * 3
        rows.append(Candle(now - (n - i) * 60_000, open_p, high, low, close, vol))
        price = close
    return rows


def read_csv_candles(path: str) -> list[Candle]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    rows: list[Candle] = []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header")
        norm = {name: name.lower().strip() for name in reader.fieldnames}
        for i, raw in enumerate(reader):
            lower = {norm[k]: v for k, v in raw.items()}
            ts = lower.get("timestamp") or lower.get("time") or lower.get("datetime") or lower.get("date")
            c = Candle(
                ts=_ts_to_ms(ts, i),
                open=_float(lower.get("open")),
                high=_float(lower.get("high")),
                low=_float(lower.get("low")),
                close=_float(lower.get("close")),
                volume=_float(lower.get("volume"), 1000.0),
            )
            if c.open > 0 and c.high > 0 and c.low > 0 and c.close > 0:
                rows.append(c)
    if len(rows) < 260:
        raise ValueError(f"need at least 260 candles, got {len(rows)}")
    rows.sort(key=lambda x: x.ts)
    return rows


def to_bybit_klines(candles: Iterable[Candle]) -> list[list[str]]:
    """Return Bybit-like rows newest-first."""
    out: list[list[str]] = []
    for c in candles:
        out.append([
            str(int(c.ts)),
            f"{c.open:.10f}",
            f"{c.high:.10f}",
            f"{c.low:.10f}",
            f"{c.close:.10f}",
            f"{c.volume:.10f}",
            f"{(c.volume * c.close):.10f}",
        ])
    return list(reversed(out))


def normalize_result(res: Any) -> tuple[Any, str, int, float | None, float | None, float | None]:
    if not isinstance(res, tuple) or len(res) != 6:
        raise AssertionError(f"signal result must be 6-tuple, got: {res!r}")
    ok, reason, score, sl, tp, atr = res

    def rf(x: Any) -> float | None:
        if x is None:
            return None
        return round(float(x), 10)

    return bool(ok), str(reason), int(score or 0), rf(sl), rf(tp), rf(atr)


def import_runtime_stack():
    # Import main so we test the real patch order used by production runtime.
    import main as bot_main  # noqa: F401
    import trader as trader_module
    import signal_engine
    return bot_main, trader_module, signal_engine


def patch_market_data(trader_module: Any, candles: list[Candle], symbol: str) -> float:
    price = float(candles[-1].close)
    kl = to_bybit_klines(candles)
    trader_module.get_klines = lambda sym, interval, limit=240: kl[: max(1, int(limit or len(kl)))]
    trader_module.get_price = lambda sym: price
    trader_module.get_ticker = lambda sym: {
        "symbol": symbol,
        "bid1Price": f"{price * 0.99995:.10f}",
        "ask1Price": f"{price * 1.00005:.10f}",
        "turnover24h": "100000000",
    }
    return price


def check_one_window(bot_main: Any, trader_module: Any, signal_engine: Any, candles: list[Candle], symbol: str, mp: dict[str, Any]) -> list[dict[str, Any]]:
    price = patch_market_data(trader_module, candles, symbol)
    rows: list[dict[str, Any]] = []
    for side in ("LONG", "SHORT"):
        direct = normalize_result(signal_engine.evaluate_signal(symbol, side, price, mp, avoid_low_rsi=False, trader_module=trader_module))
        module = normalize_result(trader_module.compute_signal_and_exits(symbol, side, price, mp, avoid_low_rsi=False))
        method = normalize_result(bot_main.trader.compute_signal_and_exits(symbol, side, price, mp, avoid_low_rsi=False))

        if "SIGNAL_ENGINE" not in direct[1]:
            raise AssertionError(f"direct signal reason does not mention SIGNAL_ENGINE: {direct[1][:120]!r}")
        if "SIGNAL_ENGINE" not in module[1]:
            raise AssertionError(f"module signal reason does not mention SIGNAL_ENGINE. A later patch may have replaced it: {module[1][:120]!r}")
        if direct != module:
            raise AssertionError(json.dumps({"side": side, "direct": direct, "module": module}, ensure_ascii=False, indent=2))
        if direct != method:
            raise AssertionError(json.dumps({"side": side, "direct": direct, "method": method}, ensure_ascii=False, indent=2))

        rows.append({
            "side": side,
            "ok": direct[0],
            "score": direct[2],
            "sl": direct[3],
            "tp": direct[4],
            "atr": direct[5],
            "reason_head": direct[1].splitlines()[0] if direct[1] else "",
        })
    return rows


def sample_windows(candles: list[Candle], samples: int, window: int) -> list[list[Candle]]:
    if len(candles) < window:
        raise ValueError(f"need >= {window} candles, got {len(candles)}")
    samples = max(1, int(samples))
    if samples == 1:
        return [candles[-window:]]
    usable_end = len(candles)
    usable_start = window
    span = max(1, usable_end - usable_start)
    out: list[list[Candle]] = []
    for k in range(samples):
        end = usable_start + int(round(span * k / max(1, samples - 1)))
        end = max(window, min(usable_end, end))
        out.append(candles[end - window:end])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", help="Optional OHLCV CSV. Columns: timestamp/open/high/low/close/volume")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--window", type=int, default=260)
    parser.add_argument("--out", default="", help="Optional JSON report path")
    args = parser.parse_args(argv)

    set_safe_env()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    bot_main, trader_module, signal_engine = import_runtime_stack()
    if not callable(getattr(trader_module, "compute_signal_and_exits", None)):
        raise AssertionError("trader.compute_signal_and_exits missing")
    if not callable(getattr(bot_main.trader, "compute_signal_and_exits", None)):
        raise AssertionError("main.trader.compute_signal_and_exits missing")

    mp = {"enter_score": 1, "lev": 3, "order_usdt": 10, "stop_atr": 1.8, "tp_r": 1.5}
    report: list[dict[str, Any]] = []

    if args.csv:
        candles = read_csv_candles(args.csv)
        windows = sample_windows(candles, args.samples, args.window)
        for idx, w in enumerate(windows, start=1):
            rows = check_one_window(bot_main, trader_module, signal_engine, w, args.symbol, mp)
            report.append({"case": f"csv_sample_{idx}", "last_close": w[-1].close, "rows": rows})
    else:
        for kind in ("up", "down", "range"):
            candles = synthetic_candles(kind, max(args.window, 280))
            rows = check_one_window(bot_main, trader_module, signal_engine, candles[-args.window:], args.symbol, mp)
            report.append({"case": kind, "last_close": candles[-1].close, "rows": rows})

    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[PARITY] OK: signal_engine == trader.compute_signal_and_exits == main.trader.compute_signal_and_exits")
    for item in report:
        print(f"[PARITY] case={item['case']} close={float(item['last_close']):.6f}")
        for row in item["rows"]:
            print(f"  - {row['side']}: ok={row['ok']} score={row['score']} sl={row['sl']} tp={row['tp']} atr={row['atr']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"[PARITY] FAIL: {e}", file=sys.stderr)
        raise
