import math
from typing import Dict, List, Optional, Tuple

from quant_core.institutional_risk_model import _pct_returns, stdev, InstitutionalRiskModel

class PortfolioEngine:
    """Multi-strategy / multi-symbol allocation engine (lightweight).
    Inputs:
      - price series per symbol (closes)
      - optional strategy performance winrates
    Output:
      - target weights per symbol (sum<=1)
      - per-symbol order multiplier vs base order_usdt

    This is designed to be safe-by-default, not overfit.
    """
    def __init__(
        self,
        risk: Optional[InstitutionalRiskModel] = None,
        min_symbols: int = 1,
        max_symbols: int = 3,
        base_mult: float = 1.0,
        max_mult: float = 1.8,
        min_mult: float = 0.4,
    ):
        self.risk = risk or InstitutionalRiskModel()
        self.min_symbols = int(min_symbols)
        self.max_symbols = int(max_symbols)
        self.base_mult = float(base_mult)
        self.max_mult = float(max_mult)
        self.min_mult = float(min_mult)

    def _inv_vol_weights(self, returns_map: Dict[str, List[float]]) -> Dict[str, float]:
        inv = {}
        for sym, rets in returns_map.items():
            vol = stdev(rets)
            if vol is None or vol <= 0:
                continue
            inv[sym] = 1.0 / vol
        s = sum(inv.values())
        if s <= 0:
            return {}
        return {k: v/s for k,v in inv.items()}

    def allocate(
        self,
        closes_map: Dict[str, List[float]],
        winrate_map: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        # build returns
        returns_map = {}
        for sym, closes in closes_map.items():
            closes = [float(x) for x in closes if float(x or 0.0) > 0]
            rets = _pct_returns(closes)
            if len(rets) >= 10:
                returns_map[sym] = rets

        if not returns_map:
            return {}

        w = self._inv_vol_weights(returns_map)
        if not w:
            return {}

        # apply correlation penalty
        for sym in list(w.keys()):
            pen = self.risk.correlation_penalty(sym, returns_map)
            w[sym] = w[sym] * pen

        # apply performance tilt (small)
        if winrate_map:
            for sym in list(w.keys()):
                wr = float(winrate_map.get(sym) or 0.0)
                # 50% baseline, tilt up to +/- 20%
                tilt = 1.0 + max(-0.2, min(0.2, (wr - 50.0) / 100.0))
                w[sym] = w[sym] * tilt

        # renorm + caps
        s = sum(w.values())
        if s > 0:
            w = {k: v/s for k,v in w.items()}
        w = self.risk.cap_weights(w)

        # pick top N
        items = sorted(w.items(), key=lambda kv: kv[1], reverse=True)
        items = items[: max(self.min_symbols, min(self.max_symbols, len(items)))]
        w = dict(items)

        # renorm again to <=1
        s = sum(w.values())
        if s > 0:
            w = {k: v/s for k,v in w.items()}
        return w

    def multiplier_for_symbol(self, weights: Dict[str, float], symbol: str) -> float:
        if not weights or symbol not in weights:
            return self.min_mult
        w = float(weights.get(symbol) or 0.0)
        # map weight -> multiplier around base
        mult = self.base_mult * (0.5 + 1.5*w)  # weight 0.33 -> ~1.0
        return max(self.min_mult, min(self.max_mult, mult))
