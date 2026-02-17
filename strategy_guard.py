class StrategyGuard:
    def __init__(self):
        self.stats = {}

    def record(self, name, pnl):
        s = self.stats.setdefault(name, {"pnl": 0, "trades": 0})
        s["pnl"] += pnl
        s["trades"] += 1

    def allow(self, name):
        s = self.stats.get(name)
        if not s or s["trades"] < 10:
            return True
        return s["pnl"] > 0
