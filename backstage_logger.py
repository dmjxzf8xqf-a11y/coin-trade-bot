# backstage_logger.py
import json, os, time
from datetime import datetime

def _utc():
    return datetime.utcnow().isoformat()

class BackstageLogger:
    """
    JSONL로 상세로그 쌓기 (한 줄 = 한 이벤트)
    - 나중에 학습/분석용으로 그대로 재사용 가능
    """
    def __init__(self, path="backstage_log.jsonl", enabled=True):
        self.path = path
        self.enabled = enabled
        if self.enabled:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def log(self, event: str, payload: dict):
        if not self.enabled:
            return
        rec = {
            "ts": _utc(),
            "t": time.time(),
            "event": event,
            "data": payload or {},
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
