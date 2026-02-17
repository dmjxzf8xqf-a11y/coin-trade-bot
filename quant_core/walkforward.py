import time
from typing import Callable, Optional, Dict, Any

from storage_utils import data_path, safe_read_json, atomic_write_json

_WF_FILE = data_path("walkforward_state.json")

class WalkForwardScheduler:
    """
    Walk-forward 자동 재최적화 스케줄러 (기본 OFF)
    - 너무 무거울 수 있으니 "스케줄만" 관리하고,
      실제 최적화 함수(optimizer/backtest)를 외부에서 주입해서 실행.
    """
    def __init__(self, interval_hours: float = 24.0):
        self.interval_sec = float(interval_hours) * 3600.0
        self.st = safe_read_json(_WF_FILE, default={"last_run_ts": 0, "last_result": None})

    def due(self) -> bool:
        last = float(self.st.get("last_run_ts", 0) or 0)
        return (time.time() - last) >= self.interval_sec

    def run_if_due(self, optimize_fn: Callable[[], Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not self.due():
            return None
        try:
            res = optimize_fn()
        except Exception as e:
            res = {"ok": False, "err": str(e)}
        self.st["last_run_ts"] = int(time.time())
        self.st["last_result"] = res
        atomic_write_json(_WF_FILE, self.st)
        return res
