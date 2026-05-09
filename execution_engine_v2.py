# execution_engine_v2.py
# Institutional Upgrade V2 - lightweight execution journal/state machine
# 목적:
# - 신호 발생과 실제 주문/체결/실패를 분리 기록
# - 중복 진입 방지 idempotency key 제공
# - retCode/오류를 data/execution_events.jsonl에 남김

from __future__ import annotations

import hashlib
import json
import os
import time
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


def _int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except Exception:
        return int(default)


def _data_path(name: str) -> Path:
    root = Path(_env("DATA_DIR", "data") or "data")
    root.mkdir(parents=True, exist_ok=True)
    return root / name


def _now() -> float:
    return time.time()


class ExecutionJournal:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or _data_path("execution_events.jsonl")
        self.state_path = _data_path("execution_state.json")
        self.state: Dict[str, Any] = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {"version": "execution_engine_v2", "recent_keys": {}, "orders": {}, "updated_ts": _now()}

    def _save_state(self) -> None:
        self.state["updated_ts"] = _now()
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state_path)

    def event(self, event_type: str, **fields: Any) -> Dict[str, Any]:
        rec = {"ts": _now(), "event": event_type, **fields}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass
        return rec

    def make_key(self, symbol: str, side: str, strategy: str = "", bucket_sec: Optional[int] = None) -> str:
        bucket_sec = bucket_sec or _int("EXEC_IDEMPOTENCY_BUCKET_SEC", 300)
        b = int(_now() // max(1, bucket_sec))
        raw = f"{symbol.upper()}|{side.upper()}|{strategy}|{b}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def is_duplicate(self, key: str) -> bool:
        ttl = _int("EXEC_IDEMPOTENCY_TTL_SEC", 300)
        now = _now()
        recent = self.state.setdefault("recent_keys", {})
        # cleanup
        for k, ts in list(recent.items()):
            try:
                if now - float(ts) > ttl:
                    recent.pop(k, None)
            except Exception:
                recent.pop(k, None)
        if key in recent:
            self._save_state()
            return True
        recent[key] = now
        self._save_state()
        return False

    def record_signal(self, symbol: str, side: str, score: float = 0.0, strategy: str = "", reason: str = "") -> str:
        key = self.make_key(symbol, side, strategy)
        dup = self.is_duplicate(key)
        self.event("signal_duplicate" if dup else "signal", key=key, symbol=symbol, side=side, score=score, strategy=strategy, reason=reason)
        return key

    def record_order_attempt(self, key: str, symbol: str, side: str, qty: Any = None, price: Any = None, **fields: Any) -> None:
        self.event("order_attempt", key=key, symbol=symbol, side=side, qty=qty, price=price, **fields)
        self.state.setdefault("orders", {})[key] = {"status": "attempted", "symbol": symbol, "side": side, "qty": qty, "price": price, "updated_ts": _now(), **fields}
        self._save_state()

    def record_order_result(self, key: str, ok: bool, result: Any = None, error: Any = None, **fields: Any) -> None:
        status = "done_or_submitted" if ok else "failed"
        self.event("order_result", key=key, ok=ok, result=str(result)[:500], error=str(error)[:500] if error else "", **fields)
        order = self.state.setdefault("orders", {}).setdefault(key, {})
        order.update({"status": status, "ok": ok, "result": str(result)[:500], "error": str(error)[:500] if error else "", "updated_ts": _now(), **fields})
        self._save_state()

    def last_events(self, n: int = 10) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
            out = []
            for line in lines:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
            return out
        except Exception:
            return []

    def status_lines(self) -> List[str]:
        orders = self.state.get("orders") or {}
        recent = self.last_events(3)
        fail_n = 0
        try:
            fail_n = sum(1 for row in orders.values() if isinstance(row, dict) and row.get("status") == "failed")
        except Exception:
            pass
        lines = [f"🧾 EXEC orders={len(orders)} fail={fail_n}"]
        for r in recent[-2:]:
            lines.append(f"- {r.get('event')} {r.get('symbol','')} {r.get('side','')} {r.get('key','')}")
        return lines


_DEFAULT_JOURNAL: Optional[ExecutionJournal] = None


def get_journal() -> ExecutionJournal:
    global _DEFAULT_JOURNAL
    if _DEFAULT_JOURNAL is None:
        _DEFAULT_JOURNAL = ExecutionJournal()
    return _DEFAULT_JOURNAL
