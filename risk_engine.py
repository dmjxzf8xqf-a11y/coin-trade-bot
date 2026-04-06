import os


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def calc_position_size(*args):
    """
    Supports both signatures used across repo:
    1) calc_position_size(balance, risk_pct, entry, stop, leverage)
    2) calc_position_size(symbol, entry, balance, leverage)
    Returns qty.
    """
    if len(args) == 5:
        balance, risk_pct, entry, stop, leverage = args
        balance = float(balance or 0)
        risk_pct = float(risk_pct or 0)
        entry = float(entry or 0)
        stop = float(stop or 0)
        leverage = float(leverage or 0)
        stop_distance = abs(entry - stop)
        if balance <= 0 or entry <= 0 or leverage <= 0 or stop_distance <= 0:
            return 0.0
        risk_amount = balance * (risk_pct / 100.0)
        qty = (risk_amount / stop_distance) * leverage
        return max(0.0, qty)

    if len(args) == 4:
        _symbol, entry, balance, leverage = args
        entry = float(entry or 0)
        balance = float(balance or 0)
        leverage = float(leverage or 0)
        if balance <= 0 or entry <= 0 or leverage <= 0:
            return 0.0
        risk_pct = float(os.getenv("RISK_PCT", "1.0"))
        stop_atr_mult = float(os.getenv("RISK_STOP_PCT", "0.02"))
        risk_amount = balance * (risk_pct / 100.0)
        stop_distance = entry * stop_atr_mult
        if stop_distance <= 0:
            return 0.0
        qty = (risk_amount / stop_distance) * leverage
        return max(0.0, qty)

    raise TypeError(f"unsupported calc_position_size args: {len(args)}")
# ===== ADVANCED RISK CONTROL PATCH =====
try:
    import time

    class AdvancedRiskManager:
        def __init__(self):
            self.loss_count = 0
            self.last_loss = 0

        def update(self, pnl):
            if pnl < 0:
                self.loss_count += 1
                self.last_loss = time.time()
            else:
                self.loss_count = 0

        def risk_mult(self):
            if self.loss_count >= 5:
                return 0.3
            elif self.loss_count >= 3:
                return 0.6
            else:
                return 1.0

    _adv_risk = AdvancedRiskManager()

    def risk_adjust(pnl):
        try:
            _adv_risk.update(pnl)
            return _adv_risk.risk_mult()
        except:
            return 1.0

except Exception:
    pass
