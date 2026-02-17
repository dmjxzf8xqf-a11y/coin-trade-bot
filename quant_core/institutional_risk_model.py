import math
import statistics
from typing import Dict, List, Tuple, Optional

def _pct_returns(prices: List[float]) -> List[float]:
    rets = []
    for i in range(1, len(prices)):
        p0 = float(prices[i-1] or 0.0)
        p1 = float(prices[i] or 0.0)
        if p0 <= 0 or p1 <= 0:
            continue
        rets.append((p1 / p0) - 1.0)
    return rets

def hist_var_es(returns: List[float], alpha: float = 0.05) -> Tuple[Optional[float], Optional[float]]:
    """Historical simulation VaR/ES.
    returns: list of arithmetic returns.
    alpha: tail probability (0.05 -> 95% VaR)
    Returns negative numbers for loss thresholds (e.g. -0.03).
    """
    if not returns:
        return None, None
    xs = sorted(returns)
    k = max(0, min(len(xs)-1, int(math.floor(alpha * len(xs))) - 1))
    # VaR is alpha-quantile (loss side) -> typically negative
    var = xs[k]
    tail = xs[:k+1]
    es = sum(tail) / len(tail) if tail else var
    return var, es

def stdev(returns: List[float]) -> Optional[float]:
    if len(returns) < 2:
        return None
    try:
        return float(statistics.pstdev(returns))
    except Exception:
        return None

def corr(a: List[float], b: List[float]) -> Optional[float]:
    n = min(len(a), len(b))
    if n < 10:
        return None
    a = a[-n:]
    b = b[-n:]
    ma = sum(a)/n
    mb = sum(b)/n
    va = sum((x-ma)**2 for x in a)
    vb = sum((x-mb)**2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((a[i]-ma)*(b[i]-mb) for i in range(n))
    return float(cov / math.sqrt(va*vb))

class InstitutionalRiskModel:
    """Lightweight institutional-style risk controls.
    - Per-symbol VaR/ES and volatility from recent klines.
    - Portfolio concentration / correlation penalty.
    This intentionally avoids external deps (numpy/pandas) to keep deploy simple.
    """
    def __init__(
        self,
        alpha: float = 0.05,
        lookback: int = 240,
        max_symbol_weight: float = 0.35,
        max_gross_exposure: float = 1.0,
        corr_penalty_threshold: float = 0.6,
        corr_penalty_strength: float = 0.35,
    ):
        self.alpha = float(alpha)
        self.lookback = int(lookback)
        self.max_symbol_weight = float(max_symbol_weight)
        self.max_gross_exposure = float(max_gross_exposure)
        self.corr_th = float(corr_penalty_threshold)
        self.corr_strength = float(corr_penalty_strength)

    def summarize_symbol(self, closes: List[float]) -> Dict[str, Optional[float]]:
        closes = [float(x) for x in closes if float(x or 0.0) > 0]
        closes = closes[-self.lookback:]
        rets = _pct_returns(closes)
        var, es = hist_var_es(rets, self.alpha)
        vol = stdev(rets)
        return {"var": var, "es": es, "vol": vol, "n": len(rets)}

    def correlation_penalty(self, sym: str, returns_map: Dict[str, List[float]]) -> float:
        # penalty in [0.6, 1.0] (multiply weight)
        base = 1.0
        a = returns_map.get(sym) or []
        if len(a) < 10:
            return base
        for other, b in returns_map.items():
            if other == sym:
                continue
            c = corr(a, b)
            if c is None:
                continue
            if c >= self.corr_th:
                base *= max(0.6, 1.0 - (c - self.corr_th) * self.corr_strength)
        return max(0.6, min(1.0, base))

    def cap_weights(self, weights: Dict[str, float]) -> Dict[str, float]:
        if not weights:
            return weights
        # cap per-symbol
        capped = {}
        for k,v in weights.items():
            capped[k] = max(0.0, min(self.max_symbol_weight, float(v)))
        s = sum(capped.values())
        if s <= 0:
            return weights
        # normalize to max gross exposure (<=1.0 typically)
        scale = min(self.max_gross_exposure, 1.0) / s if s > 0 else 1.0
        return {k: v*scale for k,v in capped.items()}
