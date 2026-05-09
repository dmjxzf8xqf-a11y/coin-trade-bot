# protection_guard.py
# Institutional Upgrade V2 - live risk protections
# 목적:
# - 일일 손실 / 연속 손실 / 심볼별 손실 / 전략별 손실 기준으로 신규 진입 차단
# - Freqtrade protections식 아이디어를 네 봇 구조에 맞춰 가볍게 구현
# - 기본값은 보수적이며, 현재 보유 포지션은 건드리지 않고 신규 진입만 막음

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name, str(default)).lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except Exception:
        return float(default)


def _int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except Exception:
        return int(default)


def _now() -> float:
    return time.time()


def _day_key(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or _now()))


def _data_path(name: str) -> Path:
    root = Path(_env("DATA_DIR", "data") or "data")
    root.mkdir(parents=True, exist_ok=True)
    return root / name


def _atomic_write(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


@dataclass
class GuardDecision:
    ok: bool
    reason: str = "OK"
    severity: str = "INFO"
    until: float = 0.0


class ProtectionGuard:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or _data_path("protection_state.json")
        self.state: Dict[str, Any] = self._load()

    def _default_state(self) -> Dict[str, Any]:
        return {
            "version": "protection_guard_v2",
            "created_ts": _now(),
            "updated_ts": _now(),
            "day": _day_key(),
            "daily_pnl": 0.0,
            "daily_trades": 0,
            "global_lock_until": 0.0,
            "global_lock_reason": "",
            "symbol_locks": {},
            "strategy_locks": {},
            "symbol_stats": {},
            "strategy_stats": {},
            "recent_trades": [],
            "last_decision": {},
        }

    def _load(self) -> Dict[str, Any]:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {**self._default_state(), **data}
        except Exception:
            pass
        return self._default_state()

    def save(self) -> None:
        self.state["updated_ts"] = _now()
        _atomic_write(self.path, self.state)

    def rollover_day(self) -> None:
        cur = _day_key()
        if self.state.get("day") != cur:
            self.state["day"] = cur
            self.state["daily_pnl"] = 0.0
            self.state["daily_trades"] = 0
            self.state["global_lock_until"] = 0.0
            self.state["global_lock_reason"] = ""
            # 심볼/전략 lock은 TTL 방식이라 그대로 두되 만료된 것은 정리
            self.cleanup_expired()
            self.save()

    def cleanup_expired(self) -> None:
        now = _now()
        for key in ("symbol_locks", "strategy_locks"):
            locks = self.state.get(key) or {}
            if isinstance(locks, dict):
                self.state[key] = {k: v for k, v in locks.items() if float((v or {}).get("until", 0) or 0) > now}
        if float(self.state.get("global_lock_until", 0) or 0) <= now:
            self.state["global_lock_until"] = 0.0
            self.state["global_lock_reason"] = ""

    def lock_global(self, seconds: int, reason: str) -> None:
        self.state["global_lock_until"] = max(float(self.state.get("global_lock_until", 0) or 0), _now() + int(seconds))
        self.state["global_lock_reason"] = reason
        self.save()

    def lock_symbol(self, symbol: str, seconds: int, reason: str) -> None:
        symbol = str(symbol or "").upper()
        if not symbol:
            return
        locks = self.state.setdefault("symbol_locks", {})
        locks[symbol] = {"until": _now() + int(seconds), "reason": reason, "ts": _now()}
        self.save()

    def lock_strategy(self, strategy: str, seconds: int, reason: str) -> None:
        strategy = str(strategy or "unknown")
        locks = self.state.setdefault("strategy_locks", {})
        locks[strategy] = {"until": _now() + int(seconds), "reason": reason, "ts": _now()}
        self.save()

    def unlock(self, target: str = "all") -> None:
        t = str(target or "all").upper()
        if t == "ALL":
            self.state["global_lock_until"] = 0.0
            self.state["global_lock_reason"] = ""
            self.state["symbol_locks"] = {}
            self.state["strategy_locks"] = {}
        else:
            self.state.get("symbol_locks", {}).pop(t, None)
            self.state.get("strategy_locks", {}).pop(str(target), None)
        self.save()

    def update_from_trader_state(self, trader: Any) -> None:
        """기존 trader.state 값만으로 추가 보호장치 판단. 실패해도 절대 봇을 죽이지 않음."""
        if not _bool("PROTECTION_GUARD_ON", True):
            return
        self.rollover_day()
        try:
            st = getattr(trader, "state", {}) or {}
            # 기존 봇 day_profit을 가능한 경우 daily_pnl에 동기화.
            for key in ("day_profit", "daily_pnl", "pnl_today"):
                if key in st:
                    try:
                        self.state["daily_pnl"] = float(st.get(key) or 0.0)
                        break
                    except Exception:
                        pass
            # 연속 손실이 위험하면 global lock 또는 safe 모드 유도.
            consec = int(float(st.get("consec_losses", 0) or 0))
            max_consec = _int("PROTECTION_GLOBAL_CONSEC_LOSSES", 3)
            if consec >= max_consec and max_consec > 0:
                self.lock_global(_int("PROTECTION_GLOBAL_LOCK_SEC", 3600), f"GLOBAL_CONSEC_LOSSES:{consec}>={max_consec}")
            # 일일 손실 제한
            max_daily_loss = abs(_float("PROTECTION_MAX_DAILY_LOSS_USDT", 0.0))
            daily = float(self.state.get("daily_pnl", 0.0) or 0.0)
            if max_daily_loss > 0 and daily <= -max_daily_loss:
                self.lock_global(_int("PROTECTION_DAILY_LOCK_SEC", 12 * 3600), f"DAILY_LOSS:{daily:.4f}<=-{max_daily_loss:.4f}")
        except Exception as e:
            self.state["last_update_error"] = str(e)[:300]
        self.save()

    def can_enter(self, symbol: str, strategy: str = "", score: float = 0.0) -> GuardDecision:
        if not _bool("PROTECTION_GUARD_ON", True):
            return GuardDecision(True, "PROTECTION_OFF")
        self.rollover_day()
        self.cleanup_expired()
        now = _now()
        symbol = str(symbol or "").upper()
        strategy = str(strategy or "unknown")

        gl_until = float(self.state.get("global_lock_until", 0) or 0)
        if gl_until > now:
            return self._remember(False, f"GLOBAL_LOCK:{self.state.get('global_lock_reason','')} left={int(gl_until-now)}s", "HIGH", gl_until)

        s_lock = (self.state.get("symbol_locks") or {}).get(symbol)
        if isinstance(s_lock, dict) and float(s_lock.get("until", 0) or 0) > now:
            return self._remember(False, f"SYMBOL_LOCK:{symbol}:{s_lock.get('reason','')} left={int(float(s_lock.get('until'))-now)}s", "MED", float(s_lock.get("until")))

        t_lock = (self.state.get("strategy_locks") or {}).get(strategy)
        if isinstance(t_lock, dict) and float(t_lock.get("until", 0) or 0) > now:
            return self._remember(False, f"STRATEGY_LOCK:{strategy}:{t_lock.get('reason','')} left={int(float(t_lock.get('until'))-now)}s", "MED", float(t_lock.get("until")))

        max_daily_trades = _int("PROTECTION_MAX_DAILY_TRADES", 0)
        if max_daily_trades > 0 and int(self.state.get("daily_trades", 0) or 0) >= max_daily_trades:
            return self._remember(False, f"DAILY_TRADE_LIMIT:{self.state.get('daily_trades')}/{max_daily_trades}", "MED", 0.0)

        return self._remember(True, "OK", "INFO", 0.0)

    def _remember(self, ok: bool, reason: str, severity: str, until: float) -> GuardDecision:
        d = GuardDecision(ok=ok, reason=reason, severity=severity, until=until)
        self.state["last_decision"] = {"ok": ok, "reason": reason, "severity": severity, "until": until, "ts": _now()}
        self.save()
        return d

    def record_trade(self, symbol: str, strategy: str, pnl: float, side: str = "", reason: str = "") -> None:
        self.rollover_day()
        symbol = str(symbol or "").upper()
        strategy = str(strategy or "unknown")
        pnl = float(pnl or 0.0)
        self.state["daily_pnl"] = float(self.state.get("daily_pnl", 0.0) or 0.0) + pnl
        self.state["daily_trades"] = int(self.state.get("daily_trades", 0) or 0) + 1
        trade = {"ts": _now(), "symbol": symbol, "strategy": strategy, "side": side, "pnl": pnl, "reason": reason}
        recent = list(self.state.get("recent_trades") or [])
        recent.append(trade)
        self.state["recent_trades"] = recent[-200:]

        self._update_stats("symbol_stats", symbol, pnl)
        self._update_stats("strategy_stats", strategy, pnl)
        self._auto_lock_after_loss(symbol, strategy)
        self.save()

    def _update_stats(self, bucket: str, key: str, pnl: float) -> None:
        stats = self.state.setdefault(bucket, {})
        row = stats.setdefault(key, {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0, "consec_losses": 0})
        row["n"] = int(row.get("n", 0) or 0) + 1
        row["pnl"] = float(row.get("pnl", 0.0) or 0.0) + pnl
        if pnl > 0:
            row["wins"] = int(row.get("wins", 0) or 0) + 1
            row["consec_losses"] = 0
        elif pnl < 0:
            row["losses"] = int(row.get("losses", 0) or 0) + 1
            row["consec_losses"] = int(row.get("consec_losses", 0) or 0) + 1
        stats[key] = row

    def _auto_lock_after_loss(self, symbol: str, strategy: str) -> None:
        srow = (self.state.get("symbol_stats") or {}).get(symbol, {})
        trow = (self.state.get("strategy_stats") or {}).get(strategy, {})
        s_loss_n = _int("PROTECTION_SYMBOL_CONSEC_LOSSES", 2)
        t_loss_n = _int("PROTECTION_STRATEGY_CONSEC_LOSSES", 3)
        if s_loss_n > 0 and int(srow.get("consec_losses", 0) or 0) >= s_loss_n:
            self.lock_symbol(symbol, _int("PROTECTION_SYMBOL_LOCK_SEC", 12 * 3600), f"symbol_consec_losses={srow.get('consec_losses')}")
        if t_loss_n > 0 and int(trow.get("consec_losses", 0) or 0) >= t_loss_n:
            self.lock_strategy(strategy, _int("PROTECTION_STRATEGY_LOCK_SEC", 24 * 3600), f"strategy_consec_losses={trow.get('consec_losses')}")

    def status_lines(self) -> List[str]:
        self.cleanup_expired()
        now = _now()
        lines = []
        lines.append(f"🛡️ PROTECT daily={float(self.state.get('daily_pnl',0) or 0):.2f} trades={self.state.get('daily_trades',0)}")
        gl = float(self.state.get("global_lock_until", 0) or 0)
        if gl > now:
            lines.append(f"⛔ global_lock {int(gl-now)}s {self.state.get('global_lock_reason','')}")
        sl = self.state.get("symbol_locks") or {}
        tl = self.state.get("strategy_locks") or {}
        if sl:
            lines.append("🔒 symbol_locks=" + ",".join(f"{k}:{int(float(v.get('until',0))-now)}s" for k, v in sl.items() if isinstance(v, dict)))
        if tl:
            lines.append("🔒 strategy_locks=" + ",".join(f"{k}:{int(float(v.get('until',0))-now)}s" for k, v in tl.items() if isinstance(v, dict)))
        ld = self.state.get("last_decision") or {}
        if ld:
            lines.append(f"🧯 last_guard={'OK' if ld.get('ok') else 'BLOCK'} {ld.get('reason','')}")
        return lines


_DEFAULT_GUARD: Optional[ProtectionGuard] = None


def get_guard() -> ProtectionGuard:
    global _DEFAULT_GUARD
    if _DEFAULT_GUARD is None:
        _DEFAULT_GUARD = ProtectionGuard()
    return _DEFAULT_GUARD
