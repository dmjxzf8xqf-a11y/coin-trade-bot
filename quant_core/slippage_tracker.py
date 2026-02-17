import time
from typing import Optional, Dict, Any

from storage_utils import data_path, safe_read_json, atomic_write_json

_SLIP_FILE = data_path("slippage_stats.json")

class SlippageTracker:
    """
    슬리피지 추적기 (가벼운/안전한 버전)
    - Bybit 응답에서 체결가를 못 받는 경우가 많아서
      "주문 직전 기대가" vs "주문 직후 관측가" 기반으로 근사치 기록.
    - 정확한 체결가 기반으로 바꾸고 싶으면 execute_*에서 fillPrice를 받는 형태로 확장하면 됨.
    """
    def __init__(self):
        self._st = safe_read_json(_SLIP_FILE, default={
            "count": 0,
            "avg_bps": 0.0,
            "last": None,
            "by_symbol": {},  # sym -> {count, avg_bps, last}
            "ts": int(time.time())
        })

    def record(self, symbol: str, expected_price: float, observed_price: float):
        if expected_price <= 0 or observed_price <= 0:
            return
        bps = ((observed_price - expected_price) / expected_price) * 10000.0

        self._update_bucket(self._st, bps, symbol=None)
        sym = self._st["by_symbol"].setdefault(symbol, {"count": 0, "avg_bps": 0.0, "last": None})
        self._update_bucket(sym, bps, symbol=symbol)

        self._st["last"] = {"symbol": symbol, "bps": bps, "expected": expected_price, "observed": observed_price, "ts": int(time.time())}
        self._st["ts"] = int(time.time())
        atomic_write_json(_SLIP_FILE, self._st)

    def avg_bps(self, symbol: Optional[str] = None) -> float:
        if symbol:
            sym = (self._st.get("by_symbol") or {}).get(symbol)
            return float(sym.get("avg_bps", 0.0)) if sym else 0.0
        return float(self._st.get("avg_bps", 0.0))

    @staticmethod
    def _update_bucket(bucket: Dict[str, Any], bps: float, symbol: Optional[str]):
        c = int(bucket.get("count", 0) or 0) + 1
        prev = float(bucket.get("avg_bps", 0.0) or 0.0)
        # online mean
        avg = prev + (bps - prev) / c
        bucket["count"] = c
        bucket["avg_bps"] = avg
        bucket["last"] = {"bps": bps, "ts": int(time.time())}
