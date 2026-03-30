import json
import os
import time
from typing import Any, Dict

from storage_utils import data_path, safe_read_json, atomic_write_json
from trader import Trader

try:
    from market_regime import get_regime_metrics_from_ohlcv
except Exception:
    def get_regime_metrics_from_ohlcv(_ohlcv):
        return {"regime": "sideways", "adx": 0.0, "atr_pct": 0.0, "ema_gap_pct": 0.0, "slope": 0.0}

try:
    from position_ai import ai_position_profile
except Exception:
    def ai_position_profile(*args, **kwargs):
        return {"size_mult": 1.0, "lev_mult": 1.0, "confidence": 1.0, "reason": "fallback"}

try:
    from symbol_weight import get_symbol_weight
except Exception:
    def get_symbol_weight(symbol: str, side: str = "LONG", strategy: str = "trend", regime: str = "sideways"):
        return 1.0

try:
    from walkforward_lite import evaluate_portfolio
except Exception:
    def evaluate_portfolio(symbols):
        return {"portfolio": {"enter_score_delta": 0, "order_usdt_mult": 1.0, "lev_mult": 1.0}, "reports": []}

try:
    import trader as trader_module
except Exception:
    trader_module = None

_WF_FILE = data_path("walkforward_portfolio.json")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _get_recent_ohlcv(symbol: str, limit: int = 160):
    if trader_module is None or not hasattr(trader_module, "get_klines"):
        return []
    try:
        interval = str(getattr(trader_module, "ENTRY_INTERVAL", "15"))
        kl = trader_module.get_klines(symbol, interval, limit) or []
        if kl and len(kl) >= 3:
            return list(reversed(kl))
    except Exception:
        return []
    return []


def _apply_regime_tp_sl(side: str, price: float, sl: float, tp: float, atr: float, regime: str):
    price = float(price or 0.0)
    sl = float(sl or 0.0)
    tp = float(tp or 0.0)
    atr = float(atr or 0.0)
    if price <= 0 or sl <= 0 or tp <= 0:
        return sl, tp
    dist_sl = abs(price - sl)
    dist_tp = abs(tp - price)
    if atr > 0:
        dist_sl = max(dist_sl, atr * 0.8)
        dist_tp = max(dist_tp, atr * 1.0)

    r = (regime or "sideways").lower()
    if r in ("bull_strong", "bear_strong", "trend"):
        dist_sl *= 0.95
        dist_tp *= 1.25
    elif r in ("sideways", "range", "chop"):
        dist_sl *= 0.90
        dist_tp *= 0.85
    elif r in ("high_volatility", "volatile"):
        dist_sl *= 1.20
        dist_tp *= 1.10

    if side == "LONG":
        return price - dist_sl, price + dist_tp
    return price + dist_sl, price - dist_tp


def _maybe_refresh_walkforward(self):
    refresh_sec = int(os.getenv("WFL_REFRESH_SEC", "1800"))
    now = time.time()
    if now - float(getattr(self, "_wfl_last_ts", 0.0) or 0.0) < refresh_sec:
        return
    self._wfl_last_ts = now
    symbols = list(dict.fromkeys([str(s).upper() for s in (getattr(self, "symbols", []) or []) if s]))[:8]
    if not symbols:
        return
    report = evaluate_portfolio(symbols)
    atomic_write_json(_WF_FILE, report)
    self.state["walkforward_lite"] = report.get("portfolio", {})
    self.state["walkforward_top"] = [
        {"symbol": r.get("symbol"), "best": r.get("best")}
        for r in report.get("reports", [])[:3]
    ]


def _load_walkforward_state():
    return safe_read_json(_WF_FILE, {"portfolio": {"enter_score_delta": 0, "order_usdt_mult": 1.0, "lev_mult": 1.0}})


_orig_score_symbol = getattr(Trader, "_score_symbol", None)
if callable(_orig_score_symbol):
    def _patched_score_symbol(self, symbol: str, price: float):
        info = _orig_score_symbol(self, symbol, price)
        if not isinstance(info, dict):
            return info
        if not info.get("ok"):
            return info

        ohlcv = _get_recent_ohlcv(symbol)
        metrics = get_regime_metrics_from_ohlcv(ohlcv)
        regime = str(metrics.get("regime") or info.get("regime") or "sideways")
        side = str(info.get("side") or "LONG")
        strategy = str(info.get("strategy") or "unknown")
        weight = float(get_symbol_weight(symbol, side, strategy, regime) or 1.0)

        sl, tp = _apply_regime_tp_sl(side, float(price or 0.0), float(info.get("sl") or 0.0), float(info.get("tp") or 0.0), float(info.get("atr") or 0.0), regime)
        score = float(info.get("score", 0.0) or 0.0)
        score = score + ((weight - 1.0) * 8.0)

        info.update({
            "regime": regime,
            "regime_metrics": metrics,
            "symbol_weight": round(weight, 4),
            "sl": sl,
            "tp": tp,
            "score": round(score, 4),
        })
        self.state["last_regime_metrics"] = {"symbol": symbol, **metrics}
        self.state["last_symbol_weight"] = {"symbol": symbol, "weight": round(weight, 4)}
        return info
    Trader._score_symbol = _patched_score_symbol


_orig_enter = getattr(Trader, "_enter", None)
if callable(_orig_enter):
    def _patched_enter(self, symbol: str, side: str, price: float, reason: str, sl: float, tp: float, strategy: str = "", score: float = 0.0, atr: float = 0.0):
        regime = "sideways"
        try:
            regime = str((self.state.get("last_regime_metrics") or {}).get("regime") or "sideways")
        except Exception:
            pass
        ai = ai_position_profile(symbol, side, strategy or "unknown", regime, getattr(self, "consec_losses", 0), getattr(self, "day_profit", 0.0))
        weight = float(get_symbol_weight(symbol, side, strategy or "unknown", regime) or 1.0)
        wf = _load_walkforward_state().get("portfolio", {})

        orig_mp = getattr(self, "_mp", None)
        if callable(orig_mp):
            def _patched_mp_once():
                mp = dict(orig_mp())
                mp["order_usdt"] = round(float(mp.get("order_usdt", 0.0) or 0.0) * float(ai.get("size_mult", 1.0)) * weight * float(wf.get("order_usdt_mult", 1.0) or 1.0), 4)
                mp["lev"] = max(1, int(round(float(mp.get("lev", 1) or 1) * float(ai.get("lev_mult", 1.0)) * float(wf.get("lev_mult", 1.0) or 1.0))))
                return mp
            self._mp = _patched_mp_once
        try:
            self.state["ai_positioning"] = {
                "symbol": symbol,
                "ai": ai,
                "weight": round(weight, 4),
                "walkforward": wf,
            }
            return _orig_enter(self, symbol, side, price, reason, sl, tp, strategy, score, atr)
        finally:
            if callable(orig_mp):
                self._mp = orig_mp
    Trader._enter = _patched_enter


_orig_tick = getattr(Trader, "tick", None)
if callable(_orig_tick):
    def _patched_tick(self, *args, **kwargs):
        try:
            _maybe_refresh_walkforward(self)
        except Exception as e:
            self.state["walkforward_lite_error"] = str(e)
        return _orig_tick(self, *args, **kwargs)
    Trader.tick = _patched_tick


_orig_status_text = getattr(Trader, "status_text", None)
if callable(_orig_status_text):
    def _patched_status_text(self, *args, **kwargs):
        base = _orig_status_text(self, *args, **kwargs)
        extra = []
        ap = self.state.get("ai_positioning") or {}
        if ap:
            extra.append(f"🧠 AI size={((ap.get('ai') or {}).get('size_mult', 1.0))} lev={((ap.get('ai') or {}).get('lev_mult', 1.0))} w={ap.get('weight', 1.0)}")
        rg = self.state.get("last_regime_metrics") or {}
        if rg:
            extra.append(f"🌊 regime={rg.get('regime', '-') } atr_pct={rg.get('atr_pct', 0)} adx={rg.get('adx', 0)}")
        wf = self.state.get("walkforward_lite") or {}
        if wf:
            extra.append(f"🪶 WFL usdt={wf.get('order_usdt_mult', 1.0)} lev={wf.get('lev_mult', 1.0)} scoreΔ={wf.get('enter_score_delta', 0)}")
        if extra:
            return str(base) + "\n" + "\n".join(extra)
        return base
    Trader.status_text = _patched_status_text


_orig_public_state = getattr(Trader, "public_state", None)
if callable(_orig_public_state):
    def _patched_public_state(self, *args, **kwargs):
        data = _orig_public_state(self, *args, **kwargs)
        if not isinstance(data, dict):
            return data
        data["ai_positioning"] = self.state.get("ai_positioning")
        data["last_regime_metrics"] = self.state.get("last_regime_metrics")
        data["last_symbol_weight"] = self.state.get("last_symbol_weight")
        data["walkforward_lite"] = self.state.get("walkforward_lite")
        return data
    Trader.public_state = _patched_public_state
