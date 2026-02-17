import time
from typing import Callable, Optional, Dict, Any

from quant_core.slippage_tracker import SlippageTracker

class ExecutionEngine:
    """
    실행 품질(Execution) 레이어 - 안전한 최소 구현
    - 시장가 주문을 "그대로" 내되,
      주문 전/후 관측가를 기반으로 슬리피지를 기록
    - 이후 개선 포인트:
      1) Bybit에서 fillPrice/avgPrice 추출해 정확 기록
      2) 유동성/스프레드 기반 분할주문(TWAP/IOC split)
      3) 실패 시 재시도/대체 라우팅
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

    def market(self, symbol: str, side: str, qty: float, reduce_only: bool = False) -> Dict[str, Any]:
        """
        side: "Buy" / "Sell"
        """
        expected = 0.0
        observed = 0.0
        try:
            expected = float(self.get_price(symbol) or 0.0)
        except Exception:
            expected = 0.0

        resp = self.place_market(symbol, side, qty, reduce_only)

        # 관측가 (근사치)
        try:
            time.sleep(max(0.0, self.post_delay))
            observed = float(self.get_price(symbol) or 0.0)
        except Exception:
            observed = 0.0

        try:
            if expected > 0 and observed > 0:
                self.tracker.record(symbol, expected, observed)
        except Exception:
            pass

        return resp
