#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from dataclasses import dataclass
from typing import List
import pandas as pd

def parse_symbols(text: str) -> List[str]:
    return [x.strip().upper() for x in text.split(",") if x.strip()]

def ema(series: pd.Series, period: int) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").astype("float64")
    return s.ewm(span=period, adjust=False).mean()

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = pd.to_numeric(df["high"], errors="coerce").astype("float64")
    low = pd.to_numeric(df["low"], errors="coerce").astype("float64")
    close = pd.to_numeric(df["close"], errors="coerce").astype("float64")
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"time":"timestamp","date":"timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.set_index("timestamp")
    return df

@dataclass
class Config:
    symbol: str
    interval: str
    sl_atr: float = 1.0
    tp_atr: float = 1.5

def backtest(df: pd.DataFrame, cfg: Config):
    balance = 100
    trades = 0
    for i in range(20, len(df)):
        if df["close"].iloc[i] > df["high"].iloc[i-1]:
            entry = df["close"].iloc[i]
            sl = entry - cfg.sl_atr
            tp = entry + cfg.tp_atr
            trades += 1
            if df["low"].iloc[i] < sl:
                balance *= 0.99
            elif df["high"].iloc[i] > tp:
                balance *= 1.01
    return balance, trades

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT")
    ap.add_argument("--interval", default="15")
    args = ap.parse_args()

    for sym in parse_symbols(args.symbols):
        df = load_csv(f"{sym}_{args.interval}m.csv")
        bal, tr = backtest(df, Config(sym, args.interval))
        print(sym, "balance:", round(bal,2), "trades:", tr)

if __name__ == "__main__":
    main()
