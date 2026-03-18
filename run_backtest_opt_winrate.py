#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import itertools
from dataclasses import dataclass, asdict
from typing import List, Dict

import pandas as pd


def parse_bool_list(text: str):
    vals = []
    for s in text.split(","):
        s = s.strip().lower()
        if s in ("1", "true", "t", "yes", "y", "on"):
            vals.append(True)
        elif s in ("0", "false", "f", "no", "n", "off"):
            vals.append(False)
        else:
            raise ValueError(f"bool parse error: {s}")
    return vals


def parse_float_list(text: str):
    return [float(x.strip()) for x in text.split(",") if x.strip()]


def parse_int_list(text: str):
    return [int(x.strip()) for x in text.split(",") if x.strip()]


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.astype("float64").ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    s = series.astype("float64")
    delta = s.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    avg_up = up.ewm(alpha=1 / period, adjust=False).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_up / avg_down.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0).astype("float64")


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"].astype("float64")
    low = df["low"].astype("float64")
    close = df["close"].astype("float64")
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
    high = df["high"].astype("float64")
    low = df["low"].astype("float64")
    close = df["close"].astype("float64")

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(0.0, index=df.index, dtype="float64")
    minus_dm = pd.Series(0.0, index=df.index, dtype="float64")

    mask_plus = (up_move > down_move) & (up_move > 0)
    mask_minus = (down_move > up_move) & (down_move > 0)

    plus_dm.loc[mask_plus] = up_move.loc[mask_plus].astype("float64")
    minus_dm.loc[mask_minus] = down_move.loc[mask_minus].astype("float64")

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


def enrich_indicators(df: pd.DataFrame, use_htf: bool) -> pd.DataFrame:
    out = df.copy()
    for c in ["open", "high", "low", "close", "volume"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0).astype("float64")

    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    out["rsi14"] = rsi(out["close"], 14)
    out["atr14"] = atr(out, 14)
    out["atr_pct"] = (out["atr14"] / out["close"].replace(0, pd.NA) * 100.0).fillna(0.0).astype("float64")
    out["adx14"] = adx(out, 14)
    out["vol_sma20"] = out["volume"].rolling(window=20, min_periods=1).mean().astype("float64")
    out["body_pct"] = (((out["close"] - out["open"]).abs() / out["open"].replace(0, pd.NA)) * 100.0).fillna(0.0).astype("float64")
    out["bar_move_pct"] = (((out["close"] - out["close"].shift(1)).abs() / out["close"].shift(1).replace(0, pd.NA)) * 100.0).fillna(0.0).astype("float64")

    out["htf_trend"] = 0
    if use_htf and isinstance(out.index, pd.DatetimeIndex):
        try:
            htf = out[["open", "high", "low", "close", "volume"]].resample("4h").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna()

            for c in ["open", "high", "low", "close", "volume"]:
                htf[c] = pd.to_numeric(htf[c], errors="coerce").fillna(0.0).astype("float64")

            htf["htf_ema20"] = ema(htf["close"], 20)
            htf["htf_ema50"] = ema(htf["close"], 50)
            htf["htf_trend"] = 0
            htf.loc[htf["htf_ema20"] > htf["htf_ema50"], "htf_trend"] = 1
            htf.loc[htf["htf_ema20"] < htf["htf_ema50"], "htf_trend"] = -1
            out["htf_trend"] = htf["htf_trend"].reindex(out.index, method="ffill").fillna(0).astype(int)
        except Exception:
            out["htf_trend"] = 0

    return out


@dataclass
class BTConfig:
    allow_long: bool = True
    allow_short: bool = True
    fee_pct_side: float = 0.06
    slip_bps_side: float = 5.0
    enter_score: int = 85
    htf_filter: bool = True
    adx_min: float = 20.0
    atr_min_pct: float = 0.20
    atr_max_pct: float = 3.00
    volume_filter: bool = True
    avoid_chase: bool = True
    chase_bar_pct: float = 1.20
    cooldown_bars: int = 3
    tp_pct: float = 0.80
    sl_pct: float = 0.90
    partial_tp_pct: float = 30.0
    move_sl_to_be_after_partial: bool = False
    initial_equity: float = 1000.0
    risk_per_trade_pct: float = 1.0


def build_signal_score(df: pd.DataFrame, i: int, side: str, cfg: BTConfig) -> int:
    row = df.iloc[i]
    prev = df.iloc[i - 1]
    score = 0

    if side == "LONG":
        if row["htf_trend"] == 1:
            score += 20
        if row["ema20"] > row["ema50"] > row["ema200"]:
            score += 20
        if row["close"] > row["ema20"] and prev["low"] <= prev["ema20"]:
            score += 15
        if 45 <= row["rsi14"] <= 62:
            score += 10
        if row["close"] > prev["high"]:
            score += 10
    else:
        if row["htf_trend"] == -1:
            score += 20
        if row["ema20"] < row["ema50"] < row["ema200"]:
            score += 20
        if row["close"] < row["ema20"] and prev["high"] >= prev["ema20"]:
            score += 15
        if 38 <= row["rsi14"] <= 55:
            score += 10
        if row["close"] < prev["low"]:
            score += 10

    if row["adx14"] >= cfg.adx_min:
        score += 15
    if cfg.atr_min_pct <= row["atr_pct"] <= cfg.atr_max_pct:
        score += 10
    if (not cfg.volume_filter) or (row["volume"] >= row["vol_sma20"]):
        score += 10
    if cfg.avoid_chase and row["bar_move_pct"] > cfg.chase_bar_pct:
        score -= 25

    return int(score)


def apply_costs_return_pct(raw_return_pct: float, cfg: BTConfig) -> float:
    round_trip_cost_pct = (cfg.fee_pct_side * 2.0) + ((cfg.slip_bps_side / 100.0) * 2.0)
    return raw_return_pct - round_trip_cost_pct


def calc_trade_pnl_usd(equity: float, risk_per_trade_pct: float, ret_pct: float) -> float:
    used = equity * (risk_per_trade_pct / 100.0) * 10.0
    return used * (ret_pct / 100.0)


def run_backtest(df: pd.DataFrame, cfg: BTConfig) -> Dict:
    equity = cfg.initial_equity
    peak_equity = equity
    max_dd_pct = 0.0
    trades = []
    cooldown_until = -1

    pos = None
    partial_taken = False
    be_stop_price = None

    start_i = max(220, 2)
    for i in range(start_i, len(df)):
        row = df.iloc[i]

        if pos is not None:
            side = pos["side"]
            entry = pos["entry_price"]
            tp_price = pos["tp_price"]
            sl_price = pos["sl_price"]
            partial_tp_price = pos["partial_tp_price"]

            high = row["high"]
            low = row["low"]

            exit_reason = None
            exit_price = None

            if (not partial_taken) and cfg.partial_tp_pct > 0:
                if side == "LONG" and high >= partial_tp_price:
                    partial_taken = True
                    if cfg.move_sl_to_be_after_partial:
                        be_stop_price = entry
                elif side == "SHORT" and low <= partial_tp_price:
                    partial_taken = True
                    if cfg.move_sl_to_be_after_partial:
                        be_stop_price = entry

            active_sl = be_stop_price if (partial_taken and be_stop_price is not None) else sl_price

            if side == "LONG":
                if low <= active_sl:
                    exit_reason = "SL" if active_sl == sl_price else "BE"
                    exit_price = active_sl
                elif high >= tp_price:
                    exit_reason = "TP"
                    exit_price = tp_price
            else:
                if high >= active_sl:
                    exit_reason = "SL" if active_sl == sl_price else "BE"
                    exit_price = active_sl
                elif low <= tp_price:
                    exit_reason = "TP"
                    exit_price = tp_price

            if exit_reason is not None:
                if side == "LONG":
                    total_ret_pct = ((exit_price - entry) / entry) * 100.0
                    partial_ret_pct = ((partial_tp_price - entry) / entry) * 100.0
                else:
                    total_ret_pct = ((entry - exit_price) / entry) * 100.0
                    partial_ret_pct = ((entry - partial_tp_price) / entry) * 100.0

                if partial_taken and cfg.partial_tp_pct > 0:
                    part = cfg.partial_tp_pct / 100.0
                    raw_ret = (partial_ret_pct * part) + (total_ret_pct * (1.0 - part))
                else:
                    raw_ret = total_ret_pct

                net_ret_pct = apply_costs_return_pct(raw_ret, cfg)
                pnl_usd = calc_trade_pnl_usd(equity, cfg.risk_per_trade_pct, net_ret_pct)
                equity += pnl_usd
                trades.append({"pnl_usd": pnl_usd, "pnl_pct": net_ret_pct})

                peak_equity = max(peak_equity, equity)
                dd = ((peak_equity - equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0
                max_dd_pct = max(max_dd_pct, dd)

                pos = None
                partial_taken = False
                be_stop_price = None
                cooldown_until = i + cfg.cooldown_bars
                continue

        if pos is not None:
            continue
        if i <= cooldown_until:
            continue

        long_score = build_signal_score(df, i, "LONG", cfg)
        short_score = build_signal_score(df, i, "SHORT", cfg)

        can_long = cfg.allow_long
        can_short = cfg.allow_short

        if cfg.htf_filter:
            can_long = can_long and (row["htf_trend"] == 1)
            can_short = can_short and (row["htf_trend"] == -1)

        if not (cfg.atr_min_pct <= row["atr_pct"] <= cfg.atr_max_pct):
            can_long = False
            can_short = False
        if row["adx14"] < cfg.adx_min:
            can_long = False
            can_short = False
        if cfg.volume_filter and row["volume"] < row["vol_sma20"]:
            can_long = False
            can_short = False
        if cfg.avoid_chase and row["bar_move_pct"] > cfg.chase_bar_pct:
            can_long = False
            can_short = False

        chosen = None
        if can_long and long_score >= cfg.enter_score and long_score >= short_score:
            chosen = "LONG"
        elif can_short and short_score >= cfg.enter_score and short_score > long_score:
            chosen = "SHORT"

        if chosen:
            entry = row["close"]
            if chosen == "LONG":
                tp_price = entry * (1 + cfg.tp_pct / 100.0)
                sl_price = entry * (1 - cfg.sl_pct / 100.0)
                partial_tp_price = entry * (1 + (cfg.tp_pct * 0.5) / 100.0)
            else:
                tp_price = entry * (1 - cfg.tp_pct / 100.0)
                sl_price = entry * (1 + cfg.sl_pct / 100.0)
                partial_tp_price = entry * (1 - (cfg.tp_pct * 0.5) / 100.0)

            pos = {
                "side": chosen,
                "entry_price": entry,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "partial_tp_price": partial_tp_price,
            }
            partial_taken = False
            be_stop_price = None

    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    gross_profit = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    winrate = (len(wins) / len(trades) * 100.0) if trades else 0.0
    avg_pnl_pct = sum(t["pnl_pct"] for t in trades) / len(trades) if trades else 0.0
    total_return_pct = ((equity - cfg.initial_equity) / cfg.initial_equity) * 100.0
    trade_count_score = min(len(trades), 150) / 150.0 * 100.0
    score = winrate * 0.40 + profit_factor * 15.0 + avg_pnl_pct * 4.0 - max_dd_pct * 0.60 + trade_count_score * 0.10

    return {
        "equity_final": round(equity, 4),
        "total_return_pct": round(total_return_pct, 4),
        "trade_count": len(trades),
        "winrate": round(winrate, 4),
        "profit_factor": round(profit_factor, 4),
        "avg_pnl_pct": round(avg_pnl_pct, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "score": round(score, 4),
    }


def grid_search(df: pd.DataFrame, configs: List[BTConfig]) -> pd.DataFrame:
    rows = []
    total = len(configs)
    for idx, cfg in enumerate(configs, start=1):
        enriched = enrich_indicators(df, use_htf=cfg.htf_filter)
        res = run_backtest(enriched, cfg)
        row = res.copy()
        row.update({f"cfg_{k}": v for k, v in asdict(cfg).items()})
        rows.append(row)
        if idx % 25 == 0 or idx == total:
            print(f"[GRID] {idx}/{total}")
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(
            by=["score", "winrate", "profit_factor", "total_return_pct"],
            ascending=[False, False, False, False]
        ).reset_index(drop=True)
    return out


def build_config_grid(args):
    enter_scores = parse_int_list(args.enter_scores)
    tp_pcts = parse_float_list(args.tp_pcts)
    sl_pcts = parse_float_list(args.sl_pcts)
    partials = parse_float_list(args.partial_tp_pcts)
    adx_mins = parse_float_list(args.adx_mins)
    atr_mins = parse_float_list(args.atr_min_pcts)
    atr_maxs = parse_float_list(args.atr_max_pcts)
    cooldowns = parse_int_list(args.cooldowns)
    htf_filters = parse_bool_list(args.htf_filters)
    volume_filters = parse_bool_list(args.volume_filters)
    avoid_chases = parse_bool_list(args.avoid_chases)

    configs = []
    for vals in itertools.product(
        enter_scores, tp_pcts, sl_pcts, partials, adx_mins, atr_mins, atr_maxs,
        cooldowns, htf_filters, volume_filters, avoid_chases
    ):
        enter_score, tp_pct, sl_pct, partial_tp_pct, adx_min, atr_min_pct, atr_max_pct, cooldown_bars, htf_filter, volume_filter, avoid_chase = vals
        configs.append(
            BTConfig(
                allow_long=not args.short_only,
                allow_short=(args.short_on or args.short_only),
                fee_pct_side=args.fee_pct_side,
                slip_bps_side=args.slip_bps_side,
                enter_score=enter_score,
                htf_filter=htf_filter,
                adx_min=adx_min,
                atr_min_pct=atr_min_pct,
                atr_max_pct=atr_max_pct,
                volume_filter=volume_filter,
                avoid_chase=avoid_chase,
                chase_bar_pct=args.chase_bar_pct,
                cooldown_bars=cooldown_bars,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                partial_tp_pct=partial_tp_pct,
                move_sl_to_be_after_partial=args.move_sl_to_be_after_partial,
                initial_equity=args.initial_equity,
                risk_per_trade_pct=args.risk_per_trade_pct,
            )
        )
    return configs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default="winrate_grid_results.csv")
    ap.add_argument("--short_on", action="store_true")
    ap.add_argument("--short_only", action="store_true")
    ap.add_argument("--fee_pct_side", type=float, default=0.06)
    ap.add_argument("--slip_bps_side", type=float, default=5.0)
    ap.add_argument("--initial_equity", type=float, default=1000.0)
    ap.add_argument("--risk_per_trade_pct", type=float, default=1.0)
    ap.add_argument("--enter_scores", default="80,85,90")
    ap.add_argument("--tp_pcts", default="0.7,0.8,0.9")
    ap.add_argument("--sl_pcts", default="0.8,0.9,1.0")
    ap.add_argument("--partial_tp_pcts", default="0,30,50")
    ap.add_argument("--adx_mins", default="18,20,22")
    ap.add_argument("--atr_min_pcts", default="0.20,0.30")
    ap.add_argument("--atr_max_pcts", default="2.50,3.00")
    ap.add_argument("--cooldowns", default="0,3,5")
    ap.add_argument("--htf_filters", default="true,false")
    ap.add_argument("--volume_filters", default="true,false")
    ap.add_argument("--avoid_chases", default="true,false")
    ap.add_argument("--chase_bar_pct", type=float, default=1.2)
    ap.add_argument("--move_sl_to_be_after_partial", action="store_true")
    args = ap.parse_args()

    df = load_csv(args.csv)
    configs = build_config_grid(args)
    print(f"[INFO] grid count: {len(configs)}")
    result_df = grid_search(df, configs)
    result_df.to_csv(args.out, index=False, encoding="utf-8-sig")

    cols = [
        "score", "winrate", "profit_factor", "max_drawdown_pct", "trade_count",
        "total_return_pct", "cfg_enter_score", "cfg_tp_pct", "cfg_sl_pct",
        "cfg_partial_tp_pct", "cfg_adx_min", "cfg_htf_filter", "cfg_volume_filter",
    ]
    print(result_df[cols].head(10).to_string(index=False))
    print(f"\\nSaved: {args.out}")


if __name__ == "__main__":
    main()
