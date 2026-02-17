import os
import time
from typing import Callable, Optional, Dict, Any, Tuple

from quant_core.slippage_tracker import SlippageTracker

def _extract_fill_price(resp: Dict[str, Any]) -> float:
    """Best-effort fill/avg price extraction from Bybit response.
    Bybit's /v5/order/create often doesn't include fill price; this is a best-effort parser.
    """
    try:
        # Some wrappers put result/orderId only.
        r = resp.get("result") or {}
        for k in ("avgPrice", "orderPrice", "price", "fillPrice"):
            v = r.get(k)
            if v is not None and float(v) > 0:
                return float(v)
    except Exception:
        pass
    return 0.0

class ExecutionEngine:
    """
    Execution quality layer (institutional-lite):
    - Records slippage using expected vs observed vs (if available) fill price.
    - Optional TWAP splitting for large notionals.
    Environment toggles (safe defaults):
      EXEC_TWAP_ON=true|false (default false)
      EXEC_TWAP_SLICES=4
      EXEC_TWAP_TOTAL_SEC=2.0
      EXEC_TWAP_MIN_NOTIONAL=50 (USDT)
    """
    def __init__(
        self,
        get_price_fn: Callable[[str], float],
        place_market_fn: Callable[[str, str, float, bool], Dict[str, Any]],
        tracker: Optional[SlippageTracker] = None,
        post_observe_delay_sec: float = 0.15,
    ):
        self.get_price = get_price_fn
        self.place_market = place_market_fn
        self.tracker = tracker or SlippageTracker()
        self.post_delay = float(post_observe_delay_sec)

        self.twap_on = str(os.getenv("EXEC_TWAP_ON", "false")).lower() in ("1","true","yes","y","on")
        self.twap_slices = int(os.getenv("EXEC_TWAP_SLICES", "4"))
        self.twap_total_sec = float(os.getenv("EXEC_TWAP_TOTAL_SEC", "2.0"))
        self.twap_min_notional = float(os.getenv("EXEC_TWAP_MIN_NOTIONAL", "50"))

    def _observe(self, symbol: str) -> float:
        try:
            return float(self.get_price(symbol) or 0.0)
        except Exception:
            return 0.0

    def _record(self, symbol: str, expected: float, observed: float, fill: float = 0.0):
        try:
            # prefer fill price if it exists
            if expected > 0 and fill > 0:
                self.tracker.record(symbol, expected, fill)
            elif expected > 0 and observed > 0:
                self.tracker.record(symbol, expected, observed)
        except Exception:
            pass

    def market(self, symbol: str, side: str, qty: float, reduce_only: bool = False) -> Dict[str, Any]:
        """side: "Buy" / "Sell" """
        expected = self._observe(symbol)

        # TWAP decision (best-effort notional check)
        notional = 0.0
        if expected > 0 and qty > 0:
            notional = expected * qty
        if self.twap_on and (notional >= self.twap_min_notional) and self.twap_slices >= 2:
            return self.market_twap(symbol, side, qty, reduce_only)

        resp = self.place_market(symbol, side, qty, reduce_only)

        fill = _extract_fill_price(resp)
        time.sleep(max(0.0, self.post_delay))
        observed = self._observe(symbol)
        self._record(symbol, expected, observed, fill)
        return resp

    def market_twap(self, symbol: str, side: str, qty: float, reduce_only: bool = False) -> Dict[str, Any]:
        slices = max(2, int(self.twap_slices))
        total = max(0.0, float(self.twap_total_sec))
        gap = total / max(1, (slices - 1))

        # split qty safely
        per = qty / float(slices)
        rem = qty - per * slices

        last_resp: Dict[str, Any] = {}
        for i in range(slices):
            q = per + (rem if i == slices - 1 else 0.0)
            if q <= 0:
                continue
            expected = self._observe(symbol)
            r = self.place_market(symbol, side, q, reduce_only)
            fill = _extract_fill_price(r)
            time.sleep(max(0.0, self.post_delay))
            observed = self._observe(symbol)
            self._record(symbol, expected, observed, fill)
            last_resp = r
            if i < slices - 1 and gap > 0:
                time.sleep(gap)
        return last_resp
