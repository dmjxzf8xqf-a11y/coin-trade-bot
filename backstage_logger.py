# backstage_logger.py
# ✅ 백스테이지(상세 로그) 저장/조회 모듈
# - logs/trades.jsonl 에 한 줄=한 이벤트(JSON)로 누적
# - entry/exit/signal/error 등 원하는 이벤트를 자유롭게 기록
#
# 사용:
#   from backstage_logger import log_entry, log_exit, log_event, tail_events
#   log_entry({...})
#   log_exit({...})
#   tail_events(20)  # 최근 20개

import os
import json
import time
from typing import Any, Dict, List, Optional

LOG_DIR = os.getenv("BACKSTAGE_DIR", "logs")
LOG_FILE = os.getenv("BACKSTAGE_FILE", os.path.join(LOG_DIR, "trades.jsonl"))
MAX_BYTES = int(os.getenv("BACKSTAGE_MAX_BYTES", "10485760"))  # 10MB

def _ensure_dir():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass

def _rotate_if_needed():
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_BYTES:
            # 간단 로테이션: 기존 -> .1 (덮어쓰기)
            rotated = LOG_FILE + ".1"
            try:
                if os.path.exists(rotated):
                    os.remove(rotated)
            except Exception:
                pass
            try:
                os.rename(LOG_FILE, rotated)
            except Exception:
                pass
    except Exception:
        pass

def log_event(event: str, payload: Optional[Dict[str, Any]] = None):
    """
    event: "ENTRY" | "EXIT" | "SIGNAL" | "ERROR" | ...
    payload: dict
    """
    _ensure_dir()
    _rotate_if_needed()

    rec = {
        "ts": time.time(),
        "event": str(event),
        "payload": payload or {},
    }
    try:
        line = json.dumps(rec, ensure_ascii=False)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # 로깅 실패해도 매매는 계속
        pass

def log_entry(payload: Dict[str, Any]):
    log_event("ENTRY", payload)

def log_exit(payload: Dict[str, Any]):
    log_event("EXIT", payload)

def tail_events(n: int = 20) -> List[Dict[str, Any]]:
    """
    최근 n개 이벤트를 리스트로 반환(최신 -> 과거)
    """
    if n <= 0:
        return []
    try:
        if not os.path.exists(LOG_FILE):
            return []
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        out = []
        for ln in reversed(lines):
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out
    except Exception:
        return []
