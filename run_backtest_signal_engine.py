#!/usr/bin/env python3
"""run_backtest_signal_engine.py

A small backtester that uses the exact same signal_engine.evaluate_signal_detail()
function as live trading.

Why this exists
- The old run_backtest_opt_winrate.py had its own scoring function.
- This file removes that mismatch for verification/backtest runs.
- It does not place orders and does not call Bybit.

Usage
    python run_backtest_signal_engine.py --csv BTCUSDT_15m.csv --symbol BTCUSDT
    python run_backtest_signal_engine.py --csv BTCUSDT_15m.csv --allow-short --out trades.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

# Keep imports/offline evaluation safe.
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("SIGNAL_ENGINE_ON", "true")
os.environ.setdefault("SIGNAL_HTF_ON", "false")
os.environ.setdefault("SIGNAL_HTF_HARD", "false")

from signal_engine import evaluate_signal_detail  # noqa: E402


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 1000.0


@dataclass
class Trade:
    entry_ts: int
    exit_ts: int
    symbol: str
    side: str
    entry: float
    exit: float
    sl: float
    tp: float
    score: int
    reason: str
    pnl_pct_raw: float
    pnl_pct_net: float
    pnl_usdt: float
    bars: int


class OfflineTraderModule:
    EMA_FAST = 20
    EMA_SLOW = 50
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    ENTRY_INTERVAL = "15"
    KLINE_LIMIT = 260

    def __init__(self, symbol: str):
        self.symbol = symbol
        self._klines: list[list[str]] = []

    def set_window(self, candles: list[Candle]) -> None:
        self._klines = to_bybit_klines(candles)

    def get_klines(self, symbol: str, interval: str, limit: int = 240):
        return self._klines[: max(1, int(limit or len(self._klines)))]


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
    rows: list[list[str]] = []
    for c in candles:
        rows.append([
            str(int(c.ts)),
            f"{c.open:.10f}",
            f"{c.high:.10f}",
            f"{c.low:.10f}",
            f"{c.close:.10f}",
            f"{c.volume:.10f}",
            f"{(c.volume * c.close):.10f}",
        ])
    return list(reversed(rows))


def side_return_pct(side: str, entry: float, exit_price: float) -> float:
    if side == "SHORT":
        return ((entry - exit_price) / entry) * 100.0
    return ((exit_price - entry) / entry) * 100.0


def apply_costs(raw_pct: float, fee_rate: float, slip_bps: float) -> float:
    # fee_rate is decimal per side, e.g. 0.0006 = 0.06%.
    round_trip_fee_pct = fee_rate * 2.0 * 100.0
    round_trip_slip_pct = (slip_bps / 100.0) * 2.0
    return raw_pct - round_trip_fee_pct - round_trip_slip_pct


def run_backtest(candles: list[Candle], args: argparse.Namespace) -> tuple[dict[str, Any], list[Trade]]:
    fake = OfflineTraderModule(args.symbol)
    mp = {
        "enter_score": int(args.enter_score),
        "lev": float(args.leverage),
        "order_usdt": float(args.order_usdt),
        "stop_atr": float(args.stop_atr),
        "tp_r": float(args.tp_r),
    }
    trades: list[Trade] = []
    equity = float(args.initial_equity)
    peak = equity
    max_dd_pct = 0.0
    pos: dict[str, Any] | None = None
    cooldown_until = -1
    start = max(260, int(args.window))

    for i in range(start, len(candles)):
        row = candles[i]

        if pos is not None:
            exit_price = None
            exit_reason = ""
            if pos["side"] == "LONG":
                if row.low <= pos["sl"]:
                    exit_price = pos["sl"]
                    exit_reason = "SL"
                elif row.high >= pos["tp"]:
                    exit_price = pos["tp"]
                    exit_reason = "TP"
            else:
                if row.high >= pos["sl"]:
                    exit_price = pos["sl"]
                    exit_reason = "SL"
                elif row.low <= pos["tp"]:
                    exit_price = pos["tp"]
                    exit_reason = "TP"

            if exit_price is not None:
                raw = side_return_pct(pos["side"], pos["entry"], float(exit_price))
                net = apply_costs(raw, float(args.fee_rate), float(args.slip_bps))
                notional = float(args.order_usdt) * float(args.leverage)
                pnl = notional * (net / 100.0)
                equity += pnl
                peak = max(peak, equity)
                if peak > 0:
                    max_dd_pct = max(max_dd_pct, ((peak - equity) / peak) * 100.0)
                trades.append(Trade(
                    entry_ts=int(pos["entry_ts"]),
                    exit_ts=int(row.ts),
                    symbol=args.symbol,
                    side=pos["side"],
                    entry=float(pos["entry"]),
                    exit=float(exit_price),
                    sl=float(pos["sl"]),
                    tp=float(pos["tp"]),
                    score=int(pos["score"]),
                    reason=exit_reason,
                    pnl_pct_raw=raw,
                    pnl_pct_net=net,
                    pnl_usdt=pnl,
                    bars=i - int(pos["entry_i"]),
                ))
                pos = None
                cooldown_until = i + int(args.cooldown_bars)
                continue

        if pos is not None or i <= cooldown_until:
            continue

        window = candles[i - int(args.window): i]
        fake.set_window(window)
        price = float(window[-1].close)

        candidates = []
        if args.allow_long:
            candidates.append(evaluate_signal_detail(args.symbol, "LONG", price, mp, avoid_low_rsi=False, trader_module=fake))
        if args.allow_short:
            candidates.append(evaluate_signal_detail(args.symbol, "SHORT", price, mp, avoid_low_rsi=False, trader_module=fake))

        candidates = [c for c in candidates if c.ok and c.sl is not None and c.tp is not None]
        if not candidates:
            continue
        chosen = max(candidates, key=lambda x: int(x.score or 0))
        pos = {
            "entry_i": i,
            "entry_ts": row.ts,
            "side": chosen.side,
            "entry": price,
            "sl": float(chosen.sl),
            "tp": float(chosen.tp),
            "score": int(chosen.score),
        }

    wins = [t for t in trades if t.pnl_usdt > 0]
    losses = [t for t in trades if t.pnl_usdt <= 0]
    gross_profit = sum(t.pnl_usdt for t in wins)
    gross_loss = abs(sum(t.pnl_usdt for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    winrate = len(wins) / len(trades) * 100.0 if trades else 0.0
    avg_net = statistics.mean([t.pnl_pct_net for t in trades]) if trades else 0.0
    result = {
        "engine": "signal_engine.evaluate_signal_detail",
        "symbol": args.symbol,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(winrate, 4),
        "profit_factor": round(profit_factor, 4),
        "avg_net_pct": round(avg_net, 4),
        "total_pnl_usdt": round(sum(t.pnl_usdt for t in trades), 4),
        "equity_final": round(equity, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "open_position_left": bool(pos is not None),
    }
    return result, trades


def write_trades(path: str, trades: list[Trade]) -> None:
    if not path:
        return
    p = Path(path)
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = list(asdict(trades[0]).keys()) if trades else [
            "entry_ts", "exit_ts", "symbol", "side", "entry", "exit", "sl", "tp", "score", "reason", "pnl_pct_raw", "pnl_pct_net", "pnl_usdt", "bars"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow(asdict(t))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--out", default="signal_engine_backtest_trades.csv")
    ap.add_argument("--window", type=int, default=260)
    ap.add_argument("--enter-score", type=int, default=int(float(os.getenv("ENTER_SCORE_SAFE", "75"))))
    ap.add_argument("--allow-long", action="store_true", default=True)
    ap.add_argument("--allow-short", action="store_true")
    ap.add_argument("--stop-atr", type=float, default=float(os.getenv("STOP_ATR_MULT_SAFE", "1.8")))
    ap.add_argument("--tp-r", type=float, default=float(os.getenv("TP_R_MULT_SAFE", "1.5")))
    ap.add_argument("--cooldown-bars", type=int, default=3)
    ap.add_argument("--fee-rate", type=float, default=float(os.getenv("FEE_RATE", "0.0006")))
    ap.add_argument("--slip-bps", type=float, default=float(os.getenv("SLIPPAGE_BPS", "5")))
    ap.add_argument("--order-usdt", type=float, default=float(os.getenv("ORDER_USDT_SAFE", "30")))
    ap.add_argument("--leverage", type=float, default=float(os.getenv("LEVERAGE_SAFE", "3")))
    ap.add_argument("--initial-equity", type=float, default=1000.0)
    args = ap.parse_args()

    candles = read_csv_candles(args.csv)
    result, trades = run_backtest(candles, args)
    write_trades(args.out, trades)
    print("[BT_SIGNAL_ENGINE] result")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[BT_SIGNAL_ENGINE] trades saved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
