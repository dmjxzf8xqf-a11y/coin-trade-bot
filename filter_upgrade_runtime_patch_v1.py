# filter_upgrade_runtime_patch_v1.py
# 목적: 기존 trader.py를 크게 갈아엎지 않고 필터 85점 업그레이드 런타임 적용
# 적용 위치: main.py에서 `import trader as trader_module` 및 `import ai_score_runtime_patch` 이후,
#           `from trader import Trader` / `trader = Trader(state)` 이전에 import.
# 기능:
# 1) COOLDOWN_SEC 기본 300초 강제
# 2) score 구간별 order_usdt/lev 차등화
# 3) 유동성 3M 하한 유지 + 5M/10M 이상 우선순위
# 4) MTF/range 차단 시, 아주 강한 돌파만 예외 허용
# 5) /filter85 상태/제어 명령 추가

try:
    import os
    import time
    import math
    import json
    from pathlib import Path
    import trader as _t
except Exception as _boot_e:  # pragma: no cover
    print(f"[FILTER85 PATCH] boot failed: {_boot_e}", flush=True)
    _t = None


def _env_bool(name: str, default=False) -> bool:
    try:
        v = str(os.getenv(name, str(default))).strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off"):
            return False
        return bool(default)
    except Exception:
        return bool(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(str(os.getenv(name, str(default))).strip()))
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except Exception:
        return float(default)


def _safe_float(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _clip(x, lo, hi):
    try:
        return max(lo, min(hi, x))
    except Exception:
        return lo


def _set_dotenv_value(name: str, value: str) -> bool:
    """가능하면 기존 컨트롤 패치의 .env setter를 쓰고, 없으면 직접 저장."""
    try:
        fn = getattr(_t, "_ctl_set_dotenv_value", None) or getattr(_t, "_ailev_set_dotenv_value", None)
        if callable(fn):
            return bool(fn(name, value))
    except Exception:
        pass
    try:
        name = str(name).strip()
        value = str(value).strip()
        os.environ[name] = value
        p = Path(".env")
        raw = p.read_text(encoding="utf-8") if p.exists() else ""
        out = []
        written = False
        prefix = name + "="
        for line in raw.splitlines():
            if line.strip().startswith(prefix):
                if not written:
                    out.append(f"{name}={value}")
                    written = True
            else:
                out.append(line)
        if not written:
            if out and out[-1].strip() != "":
                out.append("")
            out.append(f"{name}={value}")
        p.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


# =========================
# FILTER85 env/defaults
# =========================
FILTER85_ON = _env_bool("FILTER85_ON", True)
FILTER85_COOLDOWN_SEC = _env_int("FILTER85_COOLDOWN_SEC", 300)
FILTER85_MIN_TURNOVER = _env_float("FILTER85_MIN_TURNOVER24H_USDT", 3_000_000.0)
FILTER85_LIQ_MID = _env_float("FILTER85_LIQ_MID_USDT", 5_000_000.0)
FILTER85_LIQ_HIGH = _env_float("FILTER85_LIQ_HIGH_USDT", 10_000_000.0)
FILTER85_MAX_LEV = _env_int("FILTER85_MAX_LEV", 12)
FILTER85_MIN_AI_TRADES = _env_int("FILTER85_MIN_AI_TRADES", 10)
FILTER85_MIN_WINRATE_TO_BOOST = _env_float("FILTER85_MIN_WINRATE_TO_BOOST", 55.0)
FILTER85_EXCEPTION_MIN_SCORE = _env_int("FILTER85_EXCEPTION_MIN_SCORE", 88)
FILTER85_EXCEPTION_MIN_ATR = _env_float("FILTER85_EXCEPTION_MIN_ATR_PCT", 0.005)
FILTER85_EXCEPTION_MAX_ATR = _env_float("FILTER85_EXCEPTION_MAX_ATR_PCT", 0.040)
FILTER85_BREAKOUT_LOOKBACK = _env_int("FILTER85_BREAKOUT_LOOKBACK", 20)
FILTER85_BREAKOUT_BUFFER = _env_float("FILTER85_BREAKOUT_BUFFER", 0.0015)  # 0.15%


if _t is not None:
    try:
        # 1) 기존 COOLDOWN_SEC=0이어도 패치 로드시 300초로 올림.
        #    사용자가 나중에 FILTER85_COOLDOWN_SEC로 직접 낮추면 그 값을 적용.
        if FILTER85_ON:
            os.environ["COOLDOWN_SEC"] = str(max(1, FILTER85_COOLDOWN_SEC))
            setattr(_t, "COOLDOWN_SEC", max(1, FILTER85_COOLDOWN_SEC))

        # 유동성 하한은 낮추지 않고 3M 이상으로 유지.
        cur_min = _safe_float(getattr(_t, "MIN_TURNOVER24H_USDT", 0), 0.0)
        if FILTER85_ON:
            setattr(_t, "MIN_TURNOVER24H_USDT", max(cur_min, FILTER85_MIN_TURNOVER))
    except Exception as _cfg_e:
        print(f"[FILTER85 PATCH] config apply warn: {_cfg_e}", flush=True)


# =========================
# scoring/liquidity helpers
# =========================
def _ai_stats_ok_for_boost():
    """실거래/학습 표본이 부족하면 레버리지 상승은 막고, 비중 상승도 완화."""
    try:
        fn = getattr(_t, "get_ai_stats", None)
        if not callable(fn):
            return False, "stats_fn_missing"
        st = fn() or {}
        wins = int(_safe_float(st.get("wins"), 0))
        losses = int(_safe_float(st.get("losses"), 0))
        trades = wins + losses
        wr = _safe_float(st.get("winrate"), 0.0)
        # winrate가 0~1로 오는 경우 보정
        if 0 < wr <= 1.0:
            wr *= 100.0
        if trades < FILTER85_MIN_AI_TRADES:
            return False, f"warmup {trades}/{FILTER85_MIN_AI_TRADES}"
        if wr < FILTER85_MIN_WINRATE_TO_BOOST:
            return False, f"winrate {wr:.1f}%<{FILTER85_MIN_WINRATE_TO_BOOST:.1f}%"
        return True, f"winrate {wr:.1f}% trades={trades}"
    except Exception as e:
        return False, f"stats_err {e}"


def _score_tier(score: float):
    s = _safe_float(score, 0.0)
    # mult는 order_usdt, lev_add는 레버리지 추가값
    if s >= 90:
        return "S", 1.45, 2
    if s >= 83:
        return "A", 1.25, 1
    if s >= 73:
        return "B", 1.00, 0
    if s >= 65:
        return "C", 0.70, -1
    return "D", 0.50, -2


def _market_liquidity(symbol: str):
    out = {"turnover": 0.0, "spread_bps": None, "liq_mult": 0.75, "liq_tier": "LOW", "ok": True, "why": ""}
    try:
        t = _t.get_ticker(str(symbol).upper()) or {}
        bid = _safe_float(t.get("bid1Price"), 0.0)
        ask = _safe_float(t.get("ask1Price"), 0.0)
        last = _safe_float(t.get("lastPrice") or t.get("markPrice"), 0.0)
        turnover = _safe_float(t.get("turnover24h"), 0.0)
        mid = ((bid + ask) / 2.0) if bid > 0 and ask > 0 else last
        spread_bps = ((ask - bid) / mid) * 10000.0 if bid > 0 and ask > 0 and mid > 0 else None
        out["turnover"] = turnover
        out["spread_bps"] = spread_bps

        max_spread = _safe_float(getattr(_t, "MAX_SPREAD_BPS", 12.0), 12.0)
        if turnover < FILTER85_MIN_TURNOVER:
            out.update({"ok": False, "liq_tier": "BLOCK", "liq_mult": 0.0, "why": f"LIQ_TIER_BLOCK turnover={turnover:.0f}<{FILTER85_MIN_TURNOVER:.0f}"})
            return out
        if spread_bps is not None and max_spread > 0 and spread_bps > max_spread:
            out.update({"ok": False, "liq_tier": "SPREAD_BLOCK", "liq_mult": 0.0, "why": f"SPREAD_TIER_BLOCK spread={spread_bps:.2f}bps>{max_spread:.2f}bps"})
            return out

        if turnover >= FILTER85_LIQ_HIGH:
            out.update({"liq_tier": "HIGH", "liq_mult": 1.05})
        elif turnover >= FILTER85_LIQ_MID:
            out.update({"liq_tier": "MID", "liq_mult": 1.00})
        else:
            out.update({"liq_tier": "BASE", "liq_mult": 0.75})
        return out
    except Exception as e:
        out["why"] = f"LIQ_TIER_ERR {e}"
        return out


def _ema(vals, period):
    try:
        fn = getattr(_t, "ema", None)
        if callable(fn):
            return float(fn(vals, int(period)))
    except Exception:
        pass
    if not vals:
        return 0.0
    k = 2.0 / (int(period) + 1.0)
    prev = float(vals[0])
    for x in vals:
        prev = float(x) * k + prev * (1.0 - k)
    return prev


def _atr(highs, lows, closes, period):
    try:
        fn = getattr(_t, "atr", None)
        if callable(fn):
            v = fn(highs, lows, closes, int(period))
            if v is not None:
                return float(v)
    except Exception:
        pass
    p = int(period)
    if len(closes) < p + 1:
        return 0.0
    trs = []
    for i in range(-p, 0):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return sum(trs) / max(1, len(trs))


def _breakout_exception(symbol: str, side: str, price: float):
    """MTF/range 차단을 예외로 뚫는 조건. 매우 강한 돌파만 허용."""
    try:
        limit = max(120, int(getattr(_t, "EMA_SLOW", 50)) * 3, FILTER85_BREAKOUT_LOOKBACK + 60)
        kl = _t.get_klines(symbol, str(getattr(_t, "ENTRY_INTERVAL", "15")), limit)
        if not kl or len(kl) < FILTER85_BREAKOUT_LOOKBACK + 30:
            return False, "exception:no_kline"
        kl = list(reversed(kl))
        highs = [float(x[2]) for x in kl]
        lows = [float(x[3]) for x in kl]
        closes = [float(x[4]) for x in kl]
        opens = [float(x[1]) if len(x) > 1 else closes[i] for i, x in enumerate(kl)]
        last_close = closes[-1]
        last_open = opens[-1]
        ef = _ema(closes[-int(getattr(_t, "EMA_FAST", 20)) * 3:], int(getattr(_t, "EMA_FAST", 20)))
        es = _ema(closes[-int(getattr(_t, "EMA_SLOW", 50)) * 3:], int(getattr(_t, "EMA_SLOW", 50)))
        a = _atr(highs, lows, closes, int(getattr(_t, "ATR_PERIOD", 14)))
        atr_pct = (a / last_close) if last_close > 0 else 0.0
        if atr_pct < FILTER85_EXCEPTION_MIN_ATR or atr_pct > FILTER85_EXCEPTION_MAX_ATR:
            return False, f"exception:atr_pct {atr_pct:.4f}"

        lb = max(5, FILTER85_BREAKOUT_LOOKBACK)
        box_high = max(highs[-lb-1:-1])
        box_low = min(lows[-lb-1:-1])
        body = abs(last_close - last_open)
        avg_body = sum(abs(closes[i] - opens[i]) for i in range(max(0, len(closes)-20), len(closes))) / max(1, min(20, len(closes)))
        body_ok = body >= max(avg_body * 1.20, a * 0.12)

        side_u = str(side).upper()
        if side_u == "LONG":
            return (last_close > box_high * (1.0 + FILTER85_BREAKOUT_BUFFER) and last_close > last_open and last_close > ef and ef >= es and body_ok,
                    f"exception:LONG breakout box={box_high:.6f} atr%={atr_pct:.4f} body_ok={body_ok}")
        if side_u == "SHORT":
            return (last_close < box_low * (1.0 - FILTER85_BREAKOUT_BUFFER) and last_close < last_open and last_close < ef and ef <= es and body_ok,
                    f"exception:SHORT breakout box={box_low:.6f} atr%={atr_pct:.4f} body_ok={body_ok}")
        return False, "exception:bad_side"
    except Exception as e:
        return False, f"exception:err {e}"


def _exception_signal(self, symbol: str, price: float, original_reason: str):
    try:
        mp = dict(self._mp())
        mp["enter_score"] = max(int(mp.get("enter_score", 0) or 0), FILTER85_EXCEPTION_MIN_SCORE)
        avoid = bool(getattr(self, "state", {}).get("avoid_low_rsi", False))
        reason_l = str(original_reason or "")
        candidates = []
        if "MTF_BLOCK(LONG)" in reason_l:
            candidates = ["LONG"]
        elif "MTF_BLOCK(SHORT)" in reason_l:
            candidates = ["SHORT"]
        else:
            # range/no_trade는 양방향 중 강한 쪽만 예외 평가
            if bool(getattr(self, "allow_long", True)):
                candidates.append("LONG")
            if bool(getattr(self, "allow_short", True)):
                candidates.append("SHORT")

        best = None
        for side in candidates:
            ok_br, br_msg = _breakout_exception(symbol, side, price)
            if not ok_br:
                continue
            ok, reason, score, sl, tp, a = _t.compute_signal_and_exits(symbol, side, price, mp, avoid_low_rsi=avoid)
            if ok and float(score or 0) >= FILTER85_EXCEPTION_MIN_SCORE:
                item = {"ok": True, "symbol": symbol, "side": side, "reason": f"MTF/RANGE_EXCEPTION: {br_msg}\n{reason}", "score": float(score or 0), "sl": sl, "tp": tp, "atr": a, "strategy": "breakout_exception"}
                if best is None or item["score"] > best.get("score", -999):
                    best = item
        return best
    except Exception:
        return None


# =========================
# Monkey patches
# =========================
def _install_filter85():
    if _t is None or not FILTER85_ON:
        return
    Trader = getattr(_t, "Trader", None)
    if Trader is None:
        return

    # __init__: tune/status state 초기화
    _prev_init = getattr(Trader, "__init__", None)
    if callable(_prev_init) and not getattr(Trader, "_filter85_init_patched", False):
        def _filter85_init(self, *args, **kwargs):
            _prev_init(self, *args, **kwargs)
            try:
                self.state["filter85"] = {
                    "on": True,
                    "cooldown_sec": int(getattr(_t, "COOLDOWN_SEC", FILTER85_COOLDOWN_SEC)),
                    "min_turnover": float(getattr(_t, "MIN_TURNOVER24H_USDT", FILTER85_MIN_TURNOVER)),
                    "score_tiers": "65-72 small / 73-82 normal / 83+ boost / 90+ max",
                    "liq_tiers": f"3M base / 5M mid / 10M high",
                    "mtf_exception_min_score": FILTER85_EXCEPTION_MIN_SCORE,
                }
            except Exception:
                pass
        Trader.__init__ = _filter85_init
        Trader._filter85_init_patched = True

    # _score_symbol: 유동성 티어/점수 보정 + MTF/range 예외
    _prev_score = getattr(Trader, "_score_symbol", None)
    if callable(_prev_score) and not getattr(Trader, "_filter85_score_patched", False):
        def _filter85_score(self, symbol: str, price: float):
            info = _prev_score(self, symbol, price)
            if not isinstance(info, dict):
                return info

            reason0 = str(info.get("reason") or "")
            # 4) MTF/range 차단 예외: 기존이 막았을 때만, 강한 돌파만 재평가
            if not info.get("ok") and _env_bool("FILTER85_MTF_EXCEPTION_ON", True):
                low_reason = reason0.lower()
                if ("mtf_block" in low_reason) or ("strategy_block: range" in low_reason) or ("range -> no_trade" in low_reason):
                    ex = _exception_signal(self, symbol, price, reason0)
                    if isinstance(ex, dict) and ex.get("ok"):
                        try:
                            self.state["filter85_last_exception"] = {"symbol": symbol, "side": ex.get("side"), "score": ex.get("score"), "from": reason0[:90]}
                        except Exception:
                            pass
                        return ex
                return info

            if not info.get("ok"):
                return info

            # 3) 유동성 하한 유지 + 고유동성 우선순위
            liq = _market_liquidity(symbol)
            try:
                self.state["filter85_last_liq"] = {"symbol": symbol, **liq}
            except Exception:
                pass
            if not liq.get("ok", True):
                return {"ok": False, "reason": liq.get("why") or "LIQ_TIER_BLOCK", "strategy": info.get("strategy")}

            score = _safe_float(info.get("score"), 0.0)
            if liq.get("liq_tier") == "HIGH":
                score += 3.0
            elif liq.get("liq_tier") == "MID":
                score += 1.0
            elif liq.get("liq_tier") == "BASE":
                score -= 3.0
            info["score_raw_filter85"] = _safe_float(info.get("score"), 0.0)
            info["score"] = score
            info["filter85_liq_tier"] = liq.get("liq_tier")
            info["filter85_liq_mult"] = float(liq.get("liq_mult") or 1.0)
            info["filter85_turnover24h"] = float(liq.get("turnover") or 0.0)

            tier, mult, lev_add = _score_tier(score)
            info["filter85_score_tier"] = tier
            info["filter85_score_mult"] = mult
            info["filter85_lev_add"] = lev_add
            info["reason"] = str(info.get("reason") or "").rstrip() + f"\nFILTER85 tier={tier} score={score:.1f} liq={liq.get('liq_tier')} turn={float(liq.get('turnover') or 0)/1_000_000:.2f}M mult={mult:.2f}"
            return info
        Trader._score_symbol = _filter85_score
        Trader._filter85_score_patched = True

    # _enter: score tier / liq tier 기반 비중·레버리지 임시 조정
    _prev_enter = getattr(Trader, "_enter", None)
    if callable(_prev_enter) and not getattr(Trader, "_filter85_enter_patched", False):
        def _filter85_enter(self, symbol: str, side: str, price: float, reason: str, sl: float, tp: float, strategy: str = "", score: float = 0.0, atr: float = 0.0, *args, **kwargs):
            mode = str(getattr(self, "mode", "AGGRO") or "AGGRO").upper()
            old_tune = None
            try:
                old_tune = dict(self.tune.get(mode, {}))
                score_f = _safe_float(score, 0.0)
                tier, score_mult, lev_add = _score_tier(score_f)
                ai_ok, ai_msg = _ai_stats_ok_for_boost()
                liq = _market_liquidity(symbol)
                liq_mult = float(liq.get("liq_mult") or 1.0)

                base_usdt = _safe_float(old_tune.get("order_usdt"), _safe_float(getattr(_t, "ORDER_USDT_AGGRO", 12), 12))
                base_lev = int(_safe_float(old_tune.get("lev"), _safe_float(getattr(_t, "LEVERAGE_AGGRO", 8), 8)))

                final_mult = score_mult * liq_mult
                final_lev = base_lev + int(lev_add)

                # 학습 표본/승률 부족하면 증가는 막고, 축소는 허용.
                if not ai_ok:
                    final_mult = min(final_mult, 1.0)
                    final_lev = min(final_lev, base_lev)

                final_usdt = _clip(base_usdt * final_mult, max(3.0, base_usdt * 0.50), max(base_usdt, _env_float("FILTER85_MAX_ORDER_USDT", 45.0)))
                final_lev = int(_clip(final_lev, 1, max(1, FILTER85_MAX_LEV)))

                self.tune.setdefault(mode, {})
                self.tune[mode]["order_usdt"] = float(final_usdt)
                self.tune[mode]["lev"] = int(final_lev)
                reason = str(reason or "").rstrip() + f"\nFILTER85_SIZE tier={tier} base={base_usdt:.2f}x{base_lev} -> {final_usdt:.2f}x{final_lev} | ai={ai_msg} | liq={liq.get('liq_tier')}"
                try:
                    self.state["filter85_last_size"] = {"symbol": symbol, "tier": tier, "base_usdt": base_usdt, "final_usdt": final_usdt, "base_lev": base_lev, "final_lev": final_lev, "ai": ai_msg, "liq": liq}
                except Exception:
                    pass
                return _prev_enter(self, symbol, side, price, reason, sl, tp, strategy, score, atr, *args, **kwargs)
            finally:
                # 다음 진입에 영향 안 남도록 원복
                try:
                    if old_tune is not None:
                        self.tune[mode].update(old_tune)
                except Exception:
                    pass
        Trader._enter = _filter85_enter
        Trader._filter85_enter_patched = True

    # /filter85 명령
    _prev_handle = getattr(Trader, "handle_command", None)
    if callable(_prev_handle) and not getattr(Trader, "_filter85_cmd_patched", False):
        def _filter85_status_text(self):
            try:
                st = getattr(self, "state", {}) or {}
                sz = st.get("filter85_last_size") if isinstance(st.get("filter85_last_size"), dict) else {}
                liq = st.get("filter85_last_liq") if isinstance(st.get("filter85_last_liq"), dict) else {}
                ex = st.get("filter85_last_exception") if isinstance(st.get("filter85_last_exception"), dict) else {}
                return (
                    "🧠 FILTER85=ON\n"
                    f"cooldown={getattr(_t, 'COOLDOWN_SEC', FILTER85_COOLDOWN_SEC)}s | minTurn={getattr(_t, 'MIN_TURNOVER24H_USDT', FILTER85_MIN_TURNOVER)/1_000_000:.2f}M | maxLev={FILTER85_MAX_LEV}\n"
                    f"scoreTier: 65-72 축소 / 73-82 기본 / 83+ 증액 / 90+ 최대\n"
                    f"lastSize={sz.get('symbol','-')} {sz.get('base_usdt','-')}->{sz.get('final_usdt','-')} USDT lev {sz.get('base_lev','-')}->{sz.get('final_lev','-')} ai={sz.get('ai','-')}\n"
                    f"lastLiq={liq.get('symbol','-')} {liq.get('liq_tier','-')} turn={_safe_float(liq.get('turnover'),0)/1_000_000:.2f}M\n"
                    f"lastException={ex.get('symbol','-')} {ex.get('side','-')} score={ex.get('score','-')}"
                )
            except Exception as e:
                return f"🧠 FILTER85=ON status_err={e}"

        def _filter85_handle(self, text):
            raw = str(text or "").strip()
            low = raw.lower()
            if low.startswith("/filter85"):
                parts = raw.split()
                if len(parts) >= 2:
                    sub = parts[1].lower()
                    if sub in ("off", "0", "false"):
                        ok = _set_dotenv_value("FILTER85_ON", "false")
                        self.notify("🧠 FILTER85 OFF 저장됨. 재시작 후 완전 적용" if ok else "⚠️ 저장 실패")
                        return
                    if sub in ("on", "1", "true"):
                        ok = _set_dotenv_value("FILTER85_ON", "true")
                        self.notify("🧠 FILTER85 ON 저장됨. 재시작 후 완전 적용" if ok else "⚠️ 저장 실패")
                        return
                    if sub == "cooldown" and len(parts) >= 3:
                        v = max(60, min(1800, _env_int("_NOENV_", 300) if False else int(float(parts[2]))))
                        setattr(_t, "COOLDOWN_SEC", v)
                        os.environ["COOLDOWN_SEC"] = str(v)
                        ok1 = _set_dotenv_value("FILTER85_COOLDOWN_SEC", str(v))
                        ok2 = _set_dotenv_value("COOLDOWN_SEC", str(v))
                        self.notify(f"🧠 FILTER85 cooldown={v}s 저장됨" + ("" if (ok1 or ok2) else "\n⚠️ .env 저장 실패"))
                        return
                    if sub == "maxlev" and len(parts) >= 3:
                        v = max(1, min(25, int(float(parts[2]))))
                        ok = _set_dotenv_value("FILTER85_MAX_LEV", str(v))
                        self.notify(f"🧠 FILTER85_MAX_LEV={v} 저장됨. 재시작 후 적용" if ok else "⚠️ 저장 실패")
                        return
                self.notify(_filter85_status_text(self) + "\n\n사용법: /filter85 | /filter85 cooldown 300 | /filter85 maxlev 12 | /filter85 on|off")
                return
            return _prev_handle(self, text)
        Trader.handle_command = _filter85_handle
        Trader._filter85_cmd_patched = True

    # status/why에 한 줄 추가
    for meth_name in ("status_text", "why_text"):
        prev = getattr(Trader, meth_name, None)
        flag = f"_filter85_{meth_name}_patched"
        if callable(prev) and not getattr(Trader, flag, False):
            def _wrap(prev_fn, name):
                def _inner(self, *args, **kwargs):
                    txt = prev_fn(self, *args, **kwargs)
                    try:
                        st = getattr(self, "state", {}) or {}
                        liq = st.get("filter85_last_liq") if isinstance(st.get("filter85_last_liq"), dict) else {}
                        extra = f"\n🧠 FILTER85 ON | cooldown={getattr(_t, 'COOLDOWN_SEC', FILTER85_COOLDOWN_SEC)}s | liqTier={liq.get('liq_tier','-')} | /filter85"
                        return str(txt).rstrip() + extra
                    except Exception:
                        return txt
                return _inner
            setattr(Trader, meth_name, _wrap(prev, meth_name))
            setattr(Trader, flag, True)


try:
    _install_filter85()
    print("[FILTER85 PATCH V1] loaded: cooldown + score sizing + liquidity tier + MTF/range exception", flush=True)
except Exception as _e:
    try:
        print(f"[FILTER85 PATCH V1] load failed: {_e}", flush=True)
    except Exception:
        pass
