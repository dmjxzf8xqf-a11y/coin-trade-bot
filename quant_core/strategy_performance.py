import time
from typing import Dict, Any, Optional, Tuple

from storage_utils import data_path, safe_read_json, atomic_write_json

_FILE = data_path("strategy_perf.json")

class StrategyPerformance:
    """
    전략 성능 기반 자동 ON/OFF (안전한 최소 구현)
    - 전략별 최근 N회 winrate가 기준 미만이면 잠시 비활성화
    - cooldown이 지나면 자동으로 다시 평가
    """
    def __init__(
        self,
        window: int = 30,
        min_trades: int = 10,
        disable_below_winrate: float = 0.43,
        disable_for_min: int = 60,
    ):
        self.window = int(window)
        self.min_trades = int(min_trades)
        self.disable_below = float(disable_below_winrate)
        self.disable_for = int(disable_for_min) * 60

        self.st = safe_read_json(_FILE, default={"by_strategy": {}, "ts": int(time.time())})

    def _save(self):
        self.st["ts"] = int(time.time())
        atomic_write_json(_FILE, self.st)

    def record_trade(self, strategy: str, pnl_usdt: float):
        if not strategy:
            return
        b = (self.st.get("by_strategy") or {}).setdefault(strategy, {
            "wins": 0, "losses": 0, "hist": [], "disabled_until": 0
        })
        b["hist"].append(1 if pnl_usdt > 0 else 0)
        # window trim
        if len(b["hist"]) > self.window:
            b["hist"] = b["hist"][-self.window:]

        b["wins"] = int(sum(b["hist"]))
        b["losses"] = int(len(b["hist"]) - b["wins"])

        # auto disable rule
        if len(b["hist"]) >= self.min_trades:
            wr = b["wins"] / max(1, len(b["hist"]))
            if wr < self.disable_below:
                b["disabled_until"] = int(time.time()) + self.disable_for

        self._save()

    def allow(self, strategy: str) -> Tuple[bool, str]:
        if not strategy:
            return True, "PERF_OK: no strategy"
        b = (self.st.get("by_strategy") or {}).get(strategy)
        if not b:
            return True, "PERF_OK: new"
        until = int(b.get("disabled_until", 0) or 0)
        if until > int(time.time()):
            return False, f"PERF_BLOCK: {strategy} disabled"
        return True, "PERF_OK"

    def summary(self) -> Dict[str, Any]:
        out = {}
        for k, v in (self.st.get("by_strategy") or {}).items():
            total = int(v.get("wins", 0) or 0) + int(v.get("losses", 0) or 0)
            wr = (int(v.get("wins", 0) or 0) / max(1, total)) if total else 0.0
            out[k] = {
                "trades": total,
                "winrate": round(wr * 100, 2),
                "disabled_until": int(v.get("disabled_until", 0) or 0),
            }
        return out
