# position_reconciler.py
# Institutional Upgrade V2 - position reconciliation helper
# 목적:
# - Bybit 실제 포지션을 최종 진실(source of truth)로 삼기 위한 보조 모듈
# - trader.py 구조가 버전마다 달라도 최대한 안전하게 no-op 또는 경고만 수행
# - 기본값은 내부 상태를 강제로 바꾸지 않고, 불일치 경고/기록만 함

from __future__ import annotations

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


def _sym(s: Any) -> str:
    return str(s or "").strip().upper()


class PositionReconciler:
    def __init__(self) -> None:
        self.path = _data_path("position_reconcile.json")
        self.last: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {"version": "position_reconciler_v2", "updated_ts": 0, "real": [], "internal": [], "warnings": []}

    def _save(self) -> None:
        self.last["updated_ts"] = _now()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.last, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def read_internal_positions(self, trader: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            # common attributes/states from multiple bot versions
            candidates = []
            for name in ("positions", "open_positions", "pos", "position"):
                if hasattr(trader, name):
                    candidates.append(getattr(trader, name))
            st = getattr(trader, "state", {}) or {}
            if isinstance(st, dict):
                for name in ("positions", "open_positions", "pos", "position", "positions_internal"):
                    if name in st:
                        candidates.append(st.get(name))
            for obj in candidates:
                out.extend(self._normalize_positions(obj, source="internal"))
        except Exception as e:
            self.last["internal_error"] = str(e)[:300]
        # de-dup
        seen = set()
        dedup = []
        for p in out:
            key = (p.get("symbol"), p.get("side"), p.get("qty"))
            if key not in seen:
                seen.add(key)
                dedup.append(p)
        return dedup

    def read_real_positions(self, trader: Any) -> List[Dict[str, Any]]:
        """실제 거래소 포지션 읽기. 어댑터를 찾지 못하면 빈 배열과 경고를 남긴다."""
        if not _bool("RECONCILE_REAL_FETCH_ON", True):
            return []
        # 1) trader에 이미 실포지션 조회 메서드가 있으면 우선 사용
        for name in ("get_real_positions", "real_positions", "fetch_positions", "_fetch_positions", "_positions_real", "_get_positions"):
            fn = getattr(trader, name, None)
            if callable(fn):
                try:
                    return self._normalize_positions(fn(), source=name)
                except TypeError:
                    try:
                        return self._normalize_positions(fn(symbol=None), source=name)
                    except Exception as e:
                        self.last[f"adapter_error_{name}"] = str(e)[:300]
                except Exception as e:
                    self.last[f"adapter_error_{name}"] = str(e)[:300]
        # 2) pybit session 직접 접근 시도
        sess = None
        for name in ("session", "client", "bybit", "http", "_session", "_client"):
            if hasattr(trader, name):
                sess = getattr(trader, name)
                if sess is not None:
                    break
        if sess is not None and hasattr(sess, "get_positions"):
            try:
                res = sess.get_positions(category=_env("CATEGORY", "linear"), settleCoin=_env("SETTLE_COIN", "USDT"))
                return self._normalize_bybit_response(res)
            except Exception as e:
                self.last["adapter_error_pybit_get_positions"] = str(e)[:300]
        self.last["adapter"] = "none"
        return []

    def _normalize_bybit_response(self, res: Any) -> List[Dict[str, Any]]:
        try:
            rows = (((res or {}).get("result") or {}).get("list") or []) if isinstance(res, dict) else []
            return self._normalize_positions(rows, source="bybit")
        except Exception:
            return []

    def _normalize_positions(self, obj: Any, source: str = "") -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if obj is None:
            return out
        if isinstance(obj, dict):
            # dict of symbol->position or single position
            if any(k in obj for k in ("symbol", "side", "qty", "size")):
                objs = [obj]
            else:
                objs = list(obj.values())
        elif isinstance(obj, (list, tuple)):
            objs = list(obj)
        else:
            return out
        for p in objs:
            if not isinstance(p, dict):
                continue
            sym = _sym(p.get("symbol") or p.get("sym"))
            side = str(p.get("side") or p.get("positionSide") or p.get("direction") or "").upper()
            qty_raw = p.get("size", p.get("qty", p.get("positionAmt", p.get("amount", 0))))
            try:
                qty = abs(float(qty_raw or 0))
            except Exception:
                qty = 0.0
            if not sym or qty <= 0:
                continue
            if side in ("BUY",):
                side = "LONG"
            elif side in ("SELL",):
                side = "SHORT"
            out.append({
                "symbol": sym,
                "side": side or "UNKNOWN",
                "qty": qty,
                "entry": p.get("entryPrice", p.get("entry", p.get("avgPrice"))),
                "source": source,
            })
        return out

    def reconcile(self, trader: Any, force: bool = False) -> Dict[str, Any]:
        if not _bool("POSITION_RECONCILE_ON", True):
            return self.last
        now = _now()
        every = _int("POSITION_RECONCILE_INTERVAL_SEC", 60)
        if not force and now - float(self.last.get("updated_ts", 0) or 0) < every:
            return self.last
        internal = self.read_internal_positions(trader)
        real = self.read_real_positions(trader)
        warnings: List[str] = []
        i_keys = {(p.get("symbol"), p.get("side")) for p in internal}
        r_keys = {(p.get("symbol"), p.get("side")) for p in real}
        for k in sorted(r_keys - i_keys):
            warnings.append(f"REAL_NOT_INTERNAL:{k[0]}:{k[1]}")
        for k in sorted(i_keys - r_keys):
            warnings.append(f"INTERNAL_NOT_REAL:{k[0]}:{k[1]}")
        self.last.update({"real": real, "internal": internal, "warnings": warnings, "adapter": self.last.get("adapter", "")})
        # 안전하게 trader.state에 요약만 저장
        try:
            st = getattr(trader, "state", None)
            if isinstance(st, dict):
                st["reconcile_real_positions"] = real
                st["reconcile_warnings"] = warnings
                st["reconcile_ts"] = now
        except Exception:
            pass
        self._save()
        return self.last

    def status_lines(self) -> List[str]:
        real = self.last.get("real") or []
        internal = self.last.get("internal") or []
        warnings = self.last.get("warnings") or []
        lines = [f"🔁 RECON real={len(real)} internal={len(internal)} warn={len(warnings)}"]
        if warnings:
            lines.append("⚠️ " + " | ".join(warnings[:3]))
        return lines


_DEFAULT_RECONCILER: Optional[PositionReconciler] = None


def get_reconciler() -> PositionReconciler:
    global _DEFAULT_RECONCILER
    if _DEFAULT_RECONCILER is None:
        _DEFAULT_RECONCILER = PositionReconciler()
    return _DEFAULT_RECONCILER
