#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import pandas as pd


# =========================
# 유틸
# =========================
def ema(series: pd.Series, period: int) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("float64").ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").astype("float64")
    delta = s.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)

    avg_up = up.ewm(alpha=1 / period, adjust=False).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_up / avg_down.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0).astype("float64")


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = pd.to_numeric(df["high"], errors="coerce").astype("float64")
    low = pd.to_numeric(df["low"], errors="coerce").astype("float64")
    close = pd.to_numeric(df["close"], errors="coerce").astype("float64")
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.astype("float64").ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = pd.to_numeric(df["high"], errors="coerce").astype("float64")
    low = pd.to_numeric(df["low"], errors="coerce").astype("float64")
    close = pd.to_numeric(df["close"], errors="coerce").astype("float64")

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(0.0, index=df.index, dtype="float64")
    minus_dm = pd.Series(0.0, index=df.index, dtype="float64")

    mask_plus = (up_move > down_move) & (up_move > 0)
    mask_minus = (down_move > up_move) & (down_move > 0)

    plus_dm.loc[mask_plus] = up_move.loc[mask_plus]
    minus_dm.loc[mask_minus] = down_move.loc[mask_minus]

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1).astype("float64")

    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100.0 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s.replace(0, pd.NA))
    minus_di = 100.0 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s.replace(0, pd.NA))
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)) * 100.0
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0.0).astype("float64")


def parse_symbols(text: str) -> List[str]:
    return [x.strip().upper() for x in text.split(",") if x.strip()]


def tf_to_resample_rule(interval: str) -> str:
    m = {
        "1": "1min",
        "3": "3min",
        "5": "5min",
        "15": "15min",
        "30": "30min",
        "60": "1h",
        "120": "2h",
        "240": "4h",
        "D": "1D",
    }
    return m.get(str(interval), "15min")


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)

    rename_map = {}
    for c in df.columns:
        cl = c.lower().strip()
        if cl in ("time", "datetime", "date"):
            rename_map[c] = "timestamp"
        elif cl in ("timestamp", "open", "high", "low", "close", "volume"):
            rename_map[c] = cl
    df = df.rename(columns=rename_map)

    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            raise ValueError(f"missing required column: {col}")

    if "volume" not in df.columns:
        df["volume"] = 0.0

    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce", unit="s")
        bad = ts.isna()
        if bad.any():
            ts2 = pd.to_datetime(df.loc[bad, "timestamp"], utc=True, errors="coerce")
            ts.loc[bad] = ts2
        df["timestamp"] = ts
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    else:
        df = df.reset_index(drop=True)

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0).astype("float64")

    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)].copy()
    return df


def load_symbol_csv(symbol: str, interval: str) -> pd.DataFrame:
    candidates = [
        f"{symbol}_{interval}m.csv",
        f"{symbol}_{interval}.csv",
        f"{symbol}.csv",
    ]
    last_err = None
    for p in candidates:
        try:
            return load_csv(p)
        except Exception as e:
            last_err = e
    raise FileNotFoundError(f"{symbol} CSV not found. tried={candidates}. last_err={last_err}")


# =========================
# 설정
# =========================
@dataclass
class Config:
    symbol: str
    interval: str
    fee_pct_side: float = 0.06
    slip_bps_side: float = 5.0
    allow_short: bool = True

    # 기본 지표
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    atr_period: int = 14
    adx_period: int = 14

    # 핵심 필터
    htf_filter: bool = True
    htf_fast: int = 20
    htf_slow: int = 50
    htf_multiple: int = 4  # 15m -> 1h
    adx_min: float = 18.0
    atr_min_pct: float = 0.25
    atr_max_pct: float = 2.50
    avoid_chase_pct: float = 1.20
    volume_filter: bool = False

    # 진입 스코어
    enter_score: int = 70

    # 청산
    sl_atr: float = 1.0
    tp_atr: float = 1.2
    time_exit_bars: int = 48

    # 자본
    initial_balance: float = 100.0


# =========================
# 지표 추가
# =========================
def add_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = df.copy()

    out["ema_fast"] = ema(out["close"], cfg.ema_fast)
    out["ema_slow"] = ema(out["close"], cfg.ema_slow)
    out["ema_200"] = ema(out["close"], 200)
    out["rsi"] = rsi(out["close"], cfg.rsi_period)
    out["atr"] = atr(out, cfg.atr_period)
    out["atr_pct"] = (out["atr"] / out["close"].replace(0, pd.NA) * 100.0).fillna(0.0).astype("float64")
    out["adx"] = adx(out, cfg.adx_period)

    out["vol_sma20"] = out["volume"].rolling(window=20, min_periods=1).mean().astype("float64")
    out["body_pct"] = (((out["close"] - out["open"]).abs() / out["open"].replace(0, pd.NA)) * 100.0).fillna(0.0)
    out["bar_move_pct"] = (((out["close"] - out["close"].shift(1)).abs() / out["close"].shift(1).replace(0, pd.NA)) * 100.0).fillna(0.0)

    out["htf_trend"] = 0
    if cfg.htf_filter and isinstance(out.index, pd.DatetimeIndex):
        try:
            base_rule = tf_to_resample_rule(cfg.interval)
            htf_rule = "1h" if base_rule == "15min" else ("4h" if base_rule == "1h" else None)

            if htf_rule is not None:
                htf = out[["open", "high", "low", "close", "volume"]].resample(htf_rule).agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
                ).dropna()

                for c in ["open", "high", "low", "close", "volume"]:
                    htf[c] = pd.to_numeric(htf[c], errors="coerce").fillna(0.0).astype("float64")

                htf["htf_fast"] = ema(htf["close"], cfg.htf_fast)
                htf["htf_slow"] = ema(htf["close"], cfg.htf_slow)
                htf["htf_trend"] = 0
                htf.loc[htf["htf_fast"] > htf["htf_slow"], "htf_trend"] = 1
                htf.loc[htf["htf_fast"] < htf["htf_slow"], "htf_trend"] = -1

                out["htf_trend"] = htf["htf_trend"].reindex(out.index, method="ffill").fillna(0).astype(int)
        except Exception:
            out["htf_trend"] = 0

    return out


# =========================
# 시그널
# =========================
def build_entry_score(df: pd.DataFrame, i: int, side: str, cfg: Config) -> int:
    row = df.iloc[i]
    prev = df.iloc[i - 1]
    score = 0

    if side == "LONG":
        if row["htf_trend"] == 1:
            score += 25
        if row["ema_fast"] > row["ema_slow"] > row["ema_200"]:
            score += 20
        if row["close"] > row["ema_fast"] and prev["low"] <= prev["ema_fast"]:
            score += 15
        if 45 <= row["rsi"] <= 62:
            score += 10
        if row["close"] > prev["high"]:
            score += 10
    else:
        if row["htf_trend"] == -1:
            score += 25
        if row["ema_fast"] < row["ema_slow"] < row["ema_200"]:
            score += 20
        if row["close"] < row["ema_fast"] and prev["high"] >= prev["ema_fast"]:
            score += 15
        if 38 <= row["rsi"] <= 55:
            score += 10
        if row["close"] < prev["low"]:
            score += 10

    if row["adx"] >= cfg.adx_min:
        score += 15

    if cfg.atr_min_pct <= row["atr_pct"] <= cfg.atr_max_pct:
        score += 10

    if (not cfg.volume_filter) or (row["volume"] >= row["vol_sma20"]):
        score += 5

    if row["bar_move_pct"] > cfg.avoid_chase_pct:
        score -= 20

    return int(score)


# =========================
# 백테스트
# =========================
def cost_pct(cfg: Config) -> float:
    return (cfg.fee_pct_side * 2.0) + ((cfg.slip_bps_side / 100.0) * 2.0)


def backtest_symbol(df: pd.DataFrame, cfg: Config) -> Dict:
    balance = cfg.initial_balance
    peak = balance
    max_dd = 0.0

    pos = None
    trades = []
    cooldown_until = -1

    start_i = max(220, 2)
    fee_slip = cost_pct(cfg)

    for i in range(start_i, len(df)):
        row = df.iloc[i]

        # 청산
        if pos is not None:
            side = pos["side"]
            bars_held = i - pos["entry_i"]
            exit_reason = None
            exit_price = None

            if side == "LONG":
                if row["low"] <= pos["sl_price"]:
                    exit_reason = "SL"
                    exit_price = pos["sl_price"]
                elif row["high"] >= pos["tp_price"]:
                    exit_reason = "TP"
                    exit_price = pos["tp_price"]
            else:
                if row["high"] >= pos["sl_price"]:
                    exit_reason = "SL"
                    exit_price = pos["sl_price"]
                elif row["low"] <= pos["tp_price"]:
                    exit_reason = "TP"
                    exit_price = pos["tp_price"]

            if exit_reason is None and cfg.time_exit_bars > 0 and bars_held >= cfg.time_exit_bars:
                exit_reason = "TIME"
                exit_price = row["close"]

            if exit_reason is not None:
                if side == "LONG":
                    raw_ret = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100.0
                else:
                    raw_ret = ((pos["entry_price"] - exit_price) / pos["entry_price"]) * 100.0

                net_ret = raw_ret - fee_slip
                balance *= (1.0 + net_ret / 100.0)
                peak = max(peak, balance)
                dd = ((peak - balance) / peak) * 100.0 if peak > 0 else 0.0
                max_dd = max(max_dd, dd)

                trades.append(
                    {
                        "side": side,
                        "ret_pct": net_ret,
                        "reason": exit_reason,
                    }
                )
                pos = None
                cooldown_until = i + 1
                continue

        # 신규 진입
        if pos is not None:
            continue
        if i <= cooldown_until:
            continue

        atr_now = row["atr"]
        if pd.isna(atr_now) or atr_now <= 0:
            continue
        if not (cfg.atr_min_pct <= row["atr_pct"] <= cfg.atr_max_pct):
            continue
        if row["adx"] < cfg.adx_min:
            continue
        if row["bar_move_pct"] > cfg.avoid_chase_pct:
            continue

        long_score = build_entry_score(df, i, "LONG", cfg)
        short_score = build_entry_score(df, i, "SHORT", cfg)

        chosen = None

        if row["htf_trend"] == 1 and long_score >= cfg.enter_score and long_score >= short_score:
            chosen = "LONG"
        elif cfg.allow_short and row["htf_trend"] == -1 and short_score >= cfg.enter_score and short_score > long_score:
            chosen = "SHORT"

        if chosen is None:
            continue

        entry = row["close"]

        if chosen == "LONG":
            sl_price = entry - (atr_now * cfg.sl_atr)
            tp_price = entry + (atr_now * cfg.tp_atr)
        else:
            sl_price = entry + (atr_now * cfg.sl_atr)
            tp_price = entry - (atr_now * cfg.tp_atr)

        pos = {
            "side": chosen,
            "entry_i": i,
            "entry_price": entry,
            "sl_price": sl_price,
            "tp_price": tp_price,
        }

    wins = [t for t in trades if t["ret_pct"] > 0]
    losses = [t for t in trades if t["ret_pct"] <= 0]
    gross_profit = sum(t["ret_pct"] for t in wins)
    gross_loss = abs(sum(t["ret_pct"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    winrate = (len(wins) / len(trades) * 100.0) if trades else 0.0

    return {
        "symbol": cfg.symbol,
        "trades": len(trades),
        "winrate": round(winrate, 2),
        "balance_pct": round(balance, 2),
        "profit_factor": round(profit_factor, 3),
        "max_dd_pct": round(max_dd, 2),
        "params": {
            "enter_score": cfg.enter_score,
            "sl_atr": cfg.sl_atr,
            "tp_atr": cfg.tp_atr,
            "time_exit_bars": cfg.time_exit_bars,
            "short": cfg.allow_short,
            "adx_min": cfg.adx_min,
            "atr_min_pct": cfg.atr_min_pct,
            "atr_max_pct": cfg.atr_max_pct,
            "avoid_chase_pct": cfg.avoid_chase_pct,
            "htf_filter": cfg.htf_filter,
        },
    }


# =========================
# 메인
# =========================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--interval", default="15")
    ap.add_argument("--fee", type=float, default=0.06)
    ap.add_argument("--slip", type=float, default=5.0)
    ap.add_argument("--short", choices=["on", "off"], default="on")

    ap.add_argument("--enter_score", type=int, default=70)
    ap.add_argument("--ema_fast", type=int, default=20)
    ap.add_argument("--ema_slow", type=int, default=50)
    ap.add_argument("--rsi", type=int, default=14)
    ap.add_argument("--atr", type=int, default=14)

    ap.add_argument("--adx_min", type=float, default=18.0)
    ap.add_argument("--atr_min_pct", type=float, default=0.25)
    ap.add_argument("--atr_max_pct", type=float, default=2.50)
    ap.add_argument("--avoid_chase_pct", type=float, default=1.20)

    ap.add_argument("--sl_atr", type=float, default=1.0)
    ap.add_argument("--tp_atr", type=float, default=1.2)
    ap.add_argument("--time_exit_bars", type=int, default=48)

    args = ap.parse_args()

    symbols = parse_symbols(args.symbols)
    allow_short = args.short == "on"

    results = []
    print(f"Backtest pro | short={allow_short} | time_exit_bars={args.time_exit_bars}")

    for symbol in symbols:
        df = load_symbol_csv(symbol, args.interval)
        cfg = Config(
            symbol=symbol,
            interval=args.interval,
            fee_pct_side=args.fee,
            slip_bps_side=args.slip,
            allow_short=allow_short,
            ema_fast=args.ema_fast,
            ema_slow=args.ema_slow,
            rsi_period=args.rsi,
            atr_period=args.atr,
            adx_period=14,
            htf_filter=True,
            adx_min=args.adx_min,
            atr_min_pct=args.atr_min_pct,
            atr_max_pct=args.atr_max_pct,
            avoid_chase_pct=args.avoid_chase_pct,
            volume_filter=False,
            enter_score=args.enter_score,
            sl_atr=args.sl_atr,
            tp_atr=args.tp_atr,
            time_exit_bars=args.time_exit_bars,
            initial_balance=100.0,
        )
        df2 = add_indicators(df, cfg)
        res = backtest_symbol(df2, cfg)
        results.append(res)
        print(
            f"🏆 {symbol}: trades={res['trades']} "
            f"win={res['winrate']}% bal={res['balance_pct']}% "
            f"pf={res['profit_factor']} dd={res['max_dd_pct']}% params={res['params']}"
        )

    print("\\n===== TOP SUMMARY =====")
    results = sorted(results, key=lambda x: (x["balance_pct"], x["profit_factor"], x["winrate"]), reverse=True)
    for r in results:
        p = r["params"]
        print(
            f"{r['symbol']:8s} bal={r['balance_pct']:7.2f}% "
            f"win={r['winrate']:6.2f}% trades={r['trades']:4d} "
            f"pf={r['profit_factor']:5.3f} dd={r['max_dd_pct']:6.2f}% "
            f"enter={p['enter_score']} sl={p['sl_atr']} tp={p['tp_atr']} "
            f"adx>={p['adx_min']} atr%=[{p['atr_min_pct']},{p['atr_max_pct']}] "
            f"chase<={p['avoid_chase_pct']} short={p['short']} timeBars={p['time_exit_bars']}"
        )


if __name__ == "__main__":
    main()
