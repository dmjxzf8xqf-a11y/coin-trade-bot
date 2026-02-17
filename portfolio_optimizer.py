import time
from typing import Dict, Any, Optional

from storage_utils import data_path, safe_read_json, atomic_write_json

_FILE = data_path("portfolio_state.json")

class PortfolioOptimizer:
    """
    포트폴리오 자산 배분 (최소 구현)
    - 심볼별 score/승률 기반으로 order_usdt multiplier를 추천
    - 공격적인 마코프최적화/리스크패리티는 이후 확장 포인트
    """
    def __init__(self, base_mult: float = 1.0, max_mult: float = 1.8, min_mult: float = 0.6, smooth: float = 0.35):
        self.base = float(base_mult)
        self.max = float(max_mult)
        self.min = float(min_mult)
        self.smooth = float(smooth)  # 0~1 (높을수록 천천히 변함)
        self.st = safe_read_json(_FILE, default={"by_symbol": {}, "ts": int(time.time())})

    def _save(self):
        self.st["ts"] = int(time.time())
        atomic_write_json(_FILE, self.st)

    def recommend_multiplier(self, symbol: str, score: float, winrate_pct: Optional[float] = None) -> float:
        # score 0~100 가정
        s = max(0.0, min(100.0, float(score)))
        wr = float(winrate_pct) if winrate_pct is not None else 50.0
        wr = max(0.0, min(100.0, wr))

        # 간단 규칙:
        # - score가 높을수록 +
        # - winrate가 50보다 높으면 +
        raw = self.base
        raw *= (0.85 + (s / 100.0) * 0.5)          # 0.85~1.35
        raw *= (0.85 + ((wr - 50.0) / 50.0) * 0.3 + 1.0)  # 대략 0.85~1.45

        raw = max(self.min, min(self.max, raw))

        prev = float((self.st.get("by_symbol") or {}).get(symbol, {}).get("mult", self.base))
        mult = prev + (raw - prev) * (1.0 - self.smooth)

        self.st.setdefault("by_symbol", {}).setdefault(symbol, {})["mult"] = mult
        self._save()
        return mult

    def get_multiplier(self, symbol: str) -> float:
        return float((self.st.get("by_symbol") or {}).get(symbol, {}).get("mult", self.base))
