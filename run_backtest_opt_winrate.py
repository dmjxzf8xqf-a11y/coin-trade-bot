#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_backtest_opt_winrate.py

목적:
- "승률형" 필터를 넣은 독립 실행형 백테스트/그리드서치 스크립트
- CSV OHLCV 데이터로 백테스트
- 상위 추세 필터 / ADX / ATR / 거래량 / 장대봉 추격금지 / 재진입 쿨다운 / partial TP / walk-forward 지원
- 목표: 승률 80%에 가까운 조합 탐색 (단, PF/MDD/거래수도 같이 확인)

주의:
- 이 파일은 네 기존 run_backtest_opt.py를 "정확히 덮어쓰는 패치"가 아니라
  독립 실행형 대체 스크립트다.
- 네 기존 프로젝트 구조를 내가 지금 직접 볼 수 없어서,
  안전하게 단독 실행 가능한 형태로 만들었다.
- CSV 컬럼은 기본적으로:
  timestamp, open, high, low, close, volume
  를 기대한다.
"""

import argparse
import itertools
import math
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

import pandas as pd


# =========================
# 유틸
# =========================

def _to_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def parse_bool_list(text: str) -> List[bool]:
    vals = []
    for s in text.split(","):
        s = s.strip().lower()
        if s in ("1", "true", "t", "yes", "y", "on"):
            vals.append(True)
        elif s in ("0", "false", "f", "no", "n", "off"):
            vals.append(False)
        else:
            raise ValueError(f"bool 파싱 실패: {s}")
    return vals


def parse_float_list(text: str) -> List[float]:
    return [float(x.strip()) for x in text.split(",") if x.strip()]


def parse_int_list(text: str) -> List[int]:
    return [int(x.strip()) for x in text.split(",") if x.strip()]


# =========================
# 지표 계산
# =========================

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / period, adjust=False).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_up / avg_down.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)

    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move[(up_move > down_move) & (up_move > 0)]
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move[(down_move > up_move) & (down_move > 0)]

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s.replace(0, pd.NA))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s.replace(0, pd.NA))
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)) * 100
    out = dx.ewm(alpha=1 / period, adjust=False).mean()
    return out.fillna(0.0)


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    out["rsi14"] = rsi(out["close"], 14)
    out["atr14"] = atr(out, 14)
    out["atr_pct"] = safe_div_series(out["atr14"], out["close"]) * 100.0
    out["adx14"] = adx(out, 14)
    out["vol_sma20"] = out["volume"].rolling(20).mean()
    out["body_pct"] = ((out["close"] - out["open"]).abs() / out["open"].replace(0, pd.NA)) * 100.0
    out["bar_move_pct"] = ((out["close"] - out["close"].shift(1)).abs() / out["close"].shift(1).replace(0, pd.NA)) * 100.0

    # 상위 추세용 간단한 리샘플(4배 봉)
    if isinstance(out.index, pd.DatetimeIndex):
        try:
            htf = out[["open", "high", "low", "close", "volume"]].resample("4h").agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            ).dropna()
            htf["htf_ema20"] = ema(htf["close"], 20)
            htf["htf_ema50"] = ema(htf["close"], 50)
            htf["htf_trend"] = 0
            htf.loc[htf["htf_ema20"] > htf["htf_ema50"], "htf_trend"] = 1
            htf.loc[htf["htf_ema20"] < htf["htf_ema50"], "htf_trend"] = -1
            out["htf_trend"] = htf["htf_trend"].reindex(out.index, method="ffill").fillna(0)
        except Exception:
            out["htf_trend"] = 0
    else:
        out["htf_trend"] = 0

    return out


def safe_div_series(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.replace(0, pd.NA)


# =========================
# 데이터 로드
# =========================

def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)

    # 컬럼 정규화
    cols = {c.lower().strip(): c for c in df.columns}
    required = ["open", "high", "low", "close"]
    for col in required:
        if col not in cols:
            raise ValueError(f"CSV에 필수 컬럼 없음: {col}")

    rename_map = {}
    for k in ["timestamp", "time", "datetime", "date", "open", "high", "low", "close", "volume"]:
        if k in cols:
            rename_map[cols[k]] = "timestamp" if k in ("time", "datetime", "date") else k
    df = df.rename(columns=rename_map)

    if "volume" not in df.columns:
        df["volume"] = 0.0

    if "timestamp" in df.columns:
        try:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            df = df.dropna(subset=["timestamp"]).sort_values("timestamp").set_index("timestamp")
        except Exception:
            df = df.reset_index(drop=True)
    else:
        df = df.reset_index(drop=True)

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).copy()

    return df


# =========================
# 백테스트 설정/결과
# =========================

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
    partial_tp_pct: float = 30.0  # 0이면 OFF
    move_sl_to_be_after_partial: bool = True

    initial_equity: float = 1000.0
    risk_per_trade_pct: float = 1.0


@dataclass
class Trade:
    side: str
    entry_idx: int
    entry_time: str
    entry_price: float
    exit_idx: int
    exit_time: str
    exit_price: float
    pnl_pct: float
    pnl_usd: float
    reason: str
    partial_taken: bool


# =========================
# 점수 로직
# =========================

def build_signal_score(df: pd.DataFrame, i: int, side: str, cfg: BTConfig) -> int:
    row = df.iloc[i]
    prev = df.iloc[i - 1]

    score = 0

    # 1) 상위 추세
    if side == "LONG":
        if row["htf_trend"] == 1:
            score += 20
    else:
        if row["htf_trend"] == -1:
            score += 20

    # 2) 중기 추세 정렬
    if side == "LONG":
        if row["ema20"] > row["ema50"] > row["ema200"]:
            score += 20
    else:
        if row["ema20"] < row["ema50"] < row["ema200"]:
            score += 20

    # 3) 눌림/반등 성격
    if side == "LONG":
        if row["close"] > row["ema20"] and prev["low"] <= prev["ema20"]:
            score += 15
        if row["rsi14"] >= 45 and row["rsi14"] <= 62:
            score += 10
        if row["close"] > prev["high"]:
            score += 10
    else:
        if row["close"] < row["ema20"] and prev["high"] >= prev["ema20"]:
            score += 15
        if row["rsi14"] >= 38 and row["rsi14"] <= 55:
            score += 10
        if row["close"] < prev["low"]:
            score += 10

    # 4) ADX
    if row["adx14"] >= cfg.adx_min:
        score += 15

    # 5) ATR 범위
    if cfg.atr_min_pct <= row["atr_pct"] <= cfg.atr_max_pct:
        score += 10

    # 6) 거래량
    if (not cfg.volume_filter) or (row["volume"] >= row["vol_sma20"]):
        score += 10

    # 7) 추격 금지 감점
    if cfg.avoid_chase and row["bar_move_pct"] > cfg.chase_bar_pct:
        score -= 25

    return int(score)


# =========================
# 백테스트 엔진
# =========================

def apply_costs_return_pct(raw_return_pct: float, cfg: BTConfig) -> float:
    # 왕복 비용(%)
    round_trip_cost_pct = (cfg.fee_pct_side * 2.0) + ((cfg.slip_bps_side / 100.0) * 2.0)
    return raw_return_pct - round_trip_cost_pct


def calc_trade_pnl_usd(equity: float, risk_per_trade_pct: float, ret_pct: float) -> float:
    # 단순화 모델:
    # equity 전체를 쓰는 선물 실제 포지션 모델이 아니라,
    # "리스크 단위"가 아니라 성과 비교용 퍼센트 기반 모델.
    # 그래도 결과 비교에는 충분히 쓸 수 있게 설계.
    used = equity * (risk_per_trade_pct / 100.0) * 10.0
    return used * (ret_pct / 100.0)


def run_backtest(df: pd.DataFrame, cfg: BTConfig) -> Dict:
    equity = cfg.initial_equity
    peak_equity = equity
    max_dd_pct = 0.0
    trades: List[Trade] = []
    cooldown_until = -1

    pos = None
    partial_taken = False
    be_stop_price = None

    start_i = 220
    for i in range(start_i, len(df)):
        row = df.iloc[i]

        # 포지션 관리
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

            # partial TP
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

                trade = Trade(
                    side=side,
                    entry_idx=pos["entry_idx"],
                    entry_time=str(pos["entry_time"]),
                    entry_price=entry,
                    exit_idx=i,
                    exit_time=str(df.index[i]) if isinstance(df.index, pd.DatetimeIndex) else str(i),
                    exit_price=exit_price,
                    pnl_pct=net_ret_pct,
                    pnl_usd=pnl_usd,
                    reason=exit_reason,
                    partial_taken=partial_taken,
                )
                trades.append(trade)

                peak_equity = max(peak_equity, equity)
                dd = 0.0 if peak_equity <= 0 else ((peak_equity - equity) / peak_equity) * 100.0
                max_dd_pct = max(max_dd_pct, dd)

                pos = None
                partial_taken = False
                be_stop_price = None
                cooldown_until = i + cfg.cooldown_bars
                continue

        # 신규 진입
        if pos is not None:
            continue
        if i <= cooldown_until:
            continue

        long_score = build_signal_score(df, i, "LONG", cfg)
        short_score = build_signal_score(df, i, "SHORT", cfg)

        # 필터 확인
        row = df.iloc[i]
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
                # partial tp를 "최종 tp의 절반 지점"으로 둠
                partial_tp_price = entry * (1 + (cfg.tp_pct * 0.5) / 100.0)
            else:
                tp_price = entry * (1 - cfg.tp_pct / 100.0)
                sl_price = entry * (1 + cfg.sl_pct / 100.0)
                partial_tp_price = entry * (1 - (cfg.tp_pct * 0.5) / 100.0)

            pos = {
                "side": chosen,
                "entry_idx": i,
                "entry_time": str(df.index[i]) if isinstance(df.index, pd.DatetimeIndex) else str(i),
                "entry_price": entry,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "partial_tp_price": partial_tp_price,
            }
            partial_taken = False
            be_stop_price = None

    # 결과 계산
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    winrate = (len(wins) / len(trades) * 100.0) if trades else 0.0
    avg_pnl_pct = sum(t.pnl_pct for t in trades) / len(trades) if trades else 0.0
    total_return_pct = ((equity - cfg.initial_equity) / cfg.initial_equity) * 100.0

    # "승률형 점수"
    # 승률을 높게 보지만 PF/MDD/거래수도 같이 반영
    trade_count_score = min(len(trades), 150) / 150.0 * 100.0
    score = (
        winrate * 0.40
        + profit_factor * 15.0
        + avg_pnl_pct * 4.0
        - max_dd_pct * 0.60
        + trade_count_score * 0.10
    )

    return {
        "config": asdict(cfg),
        "equity_final": round(equity, 4),
        "total_return_pct": round(total_return_pct, 4),
        "trade_count": len(trades),
        "winrate": round(winrate, 4),
        "profit_factor": round(profit_factor, 4),
        "avg_pnl_pct": round(avg_pnl_pct, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "score": round(score, 4),
        "trades": [asdict(t) for t in trades],
    }


# =========================
# 워크포워드
# =========================

def split_walk_forward(df: pd.DataFrame, train_bars: int, test_bars: int, step_bars: int) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
    chunks = []
    start = 0
    while start + train_bars + test_bars <= len(df):
        train_df = df.iloc[start:start + train_bars].copy()
        test_df = df.iloc[start + train_bars:start + train_bars + test_bars].copy()
        chunks.append((train_df, test_df))
        start += step_bars
    return chunks


def grid_search(df: pd.DataFrame, configs: List[BTConfig]) -> pd.DataFrame:
    rows = []
    total = len(configs)
    for idx, cfg in enumerate(configs, start=1):
        res = run_backtest(df, cfg)
        row = {k: v for k, v in res.items() if k != "trades"}
        row.update({f"cfg_{k}": v for k, v in row["config"].items()})
        del row["config"]
        rows.append(row)

        if idx % 25 == 0 or idx == total:
            print(f"[GRID] {idx}/{total} 완료")

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(
            by=["score", "winrate", "profit_factor", "total_return_pct"],
            ascending=[False, False, False, False]
        ).reset_index(drop=True)
    return out


def build_config_grid(args) -> List[BTConfig]:
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

    configs: List[BTConfig] = []
    for (
        enter_score,
        tp_pct,
        sl_pct,
        partial_tp_pct,
        adx_min,
        atr_min_pct,
        atr_max_pct,
        cooldown_bars,
        htf_filter,
        volume_filter,
        avoid_chase,
    ) in itertools.product(
        enter_scores,
        tp_pcts,
        sl_pcts,
        partials,
        adx_mins,
        atr_mins,
        atr_maxs,
        cooldowns,
        htf_filters,
        volume_filters,
        avoid_chases,
    ):
        cfg = BTConfig(
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
        configs.append(cfg)
    return configs


def print_top(df: pd.DataFrame, top_n: int = 10):
    if df.empty:
        print("결과 없음")
        return
    cols = [
        "score", "winrate", "profit_factor", "max_drawdown_pct", "trade_count",
        "total_return_pct",
        "cfg_enter_score", "cfg_tp_pct", "cfg_sl_pct", "cfg_partial_tp_pct",
        "cfg_adx_min", "cfg_atr_min_pct", "cfg_atr_max_pct", "cfg_cooldown_bars",
        "cfg_htf_filter", "cfg_volume_filter", "cfg_avoid_chase",
    ]
    print(df[cols].head(top_n).to_string(index=False))


# =========================
# 메인
# =========================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="OHLCV CSV 파일 경로")
    ap.add_argument("--out", default="winrate_grid_results.csv", help="결과 CSV 저장 경로")

    # 방향
    ap.add_argument("--short_on", action="store_true", help="숏 허용")
    ap.add_argument("--short_only", action="store_true", help="숏만")

    # 비용
    ap.add_argument("--fee_pct_side", type=float, default=0.06, help="수수료 %/side")
    ap.add_argument("--slip_bps_side", type=float, default=5.0, help="슬리피지 bps/side")

    # 자본
    ap.add_argument("--initial_equity", type=float, default=1000.0)
    ap.add_argument("--risk_per_trade_pct", type=float, default=1.0)

    # 그리드
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

    # walk-forward
    ap.add_argument("--walk_forward", action="store_true")
    ap.add_argument("--train_bars", type=int, default=3000)
    ap.add_argument("--test_bars", type=int, default=1000)
    ap.add_argument("--step_bars", type=int, default=1000)
    ap.add_argument("--top_n", type=int, default=10)
    args = ap.parse_args()

    df = load_csv(args.csv)
    df = enrich_indicators(df)

    configs = build_config_grid(args)
    print(f"[INFO] grid 개수: {len(configs)}")

    if not args.walk_forward:
        result_df = grid_search(df, configs)
        result_df.to_csv(args.out, index=False, encoding="utf-8-sig")
        print_top(result_df, args.top_n)
        print(f"\n저장 완료: {args.out}")
        return

    # Walk-forward
    wf_chunks = split_walk_forward(df, args.train_bars, args.test_bars, args.step_bars)
    if not wf_chunks:
        raise ValueError("walk-forward 구간이 부족함. train/test/step 조정 필요")

    wf_rows = []
    for idx, (train_df, test_df) in enumerate(wf_chunks, start=1):
        print(f"\n[WF] chunk {idx}/{len(wf_chunks)}")
        train_res = grid_search(train_df, configs)
        if train_res.empty:
            continue

        best = train_res.iloc[0]
        best_cfg = BTConfig(
            allow_long=bool(best["cfg_allow_long"]),
            allow_short=bool(best["cfg_allow_short"]),
            fee_pct_side=float(best["cfg_fee_pct_side"]),
            slip_bps_side=float(best["cfg_slip_bps_side"]),
            enter_score=int(best["cfg_enter_score"]),
            htf_filter=bool(best["cfg_htf_filter"]),
            adx_min=float(best["cfg_adx_min"]),
            atr_min_pct=float(best["cfg_atr_min_pct"]),
            atr_max_pct=float(best["cfg_atr_max_pct"]),
            volume_filter=bool(best["cfg_volume_filter"]),
            avoid_chase=bool(best["cfg_avoid_chase"]),
            chase_bar_pct=float(best["cfg_chase_bar_pct"]),
            cooldown_bars=int(best["cfg_cooldown_bars"]),
            tp_pct=float(best["cfg_tp_pct"]),
            sl_pct=float(best["cfg_sl_pct"]),
            partial_tp_pct=float(best["cfg_partial_tp_pct"]),
            move_sl_to_be_after_partial=bool(best["cfg_move_sl_to_be_after_partial"]),
            initial_equity=float(best["cfg_initial_equity"]),
            risk_per_trade_pct=float(best["cfg_risk_per_trade_pct"]),
        )

        test_res = run_backtest(test_df, best_cfg)
        wf_rows.append({
            "chunk": idx,
            "train_best_score": float(best["score"]),
            "train_best_winrate": float(best["winrate"]),
            "test_score": test_res["score"],
            "test_winrate": test_res["winrate"],
            "test_profit_factor": test_res["profit_factor"],
            "test_max_drawdown_pct": test_res["max_drawdown_pct"],
            "test_trade_count": test_res["trade_count"],
            "test_total_return_pct": test_res["total_return_pct"],
            **{f"best_{k}": v for k, v in asdict(best_cfg).items()},
        })

    wf_df = pd.DataFrame(wf_rows)
    wf_df.to_csv(args.out, index=False, encoding="utf-8-sig")
    print("\n[WF RESULT]")
    print(wf_df.head(args.top_n).to_string(index=False))
    print(f"\n저장 완료: {args.out}")


if __name__ == "__main__":
    main()
