# health_monitor.py
# Institutional Upgrade V2 - VPS/bot health checks

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name, str(default)).lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except Exception:
        return int(default)


def _data_path(name: str) -> Path:
    root = Path(_env("DATA_DIR", "data") or "data")
    root.mkdir(parents=True, exist_ok=True)
    return root / name


def _now() -> float:
    return time.time()


class HealthMonitor:
    def __init__(self) -> None:
        self.path = _data_path("health_latest.json")
        self.last: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {"version": "health_monitor_v2", "updated_ts": 0, "checks": {}}

    def _save(self) -> None:
        self.last["updated_ts"] = _now()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.last, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def run(self, force: bool = False) -> Dict[str, Any]:
        if not _bool("HEALTH_MONITOR_ON", True):
            return self.last
        every = _int("HEALTH_CHECK_INTERVAL_SEC", 120)
        if not force and _now() - float(self.last.get("updated_ts", 0) or 0) < every:
            return self.last
        checks: Dict[str, Any] = {}
        checks["disk"] = self._check_disk()
        checks["env"] = self._check_env()
        checks["files"] = self._check_files()
        checks["tmux"] = self._check_tmux()
        if _bool("HEALTH_CHECK_TELEGRAM", False):
            checks["telegram"] = self._check_telegram()
        self.last = {"version": "health_monitor_v2", "updated_ts": _now(), "checks": checks}
        self._save()
        return self.last

    def _check_disk(self) -> Dict[str, Any]:
        try:
            total, used, free = shutil.disk_usage(".")
            free_pct = free / total * 100 if total else 0
            return {"ok": free_pct >= float(_env("HEALTH_MIN_DISK_FREE_PCT", "10") or 10), "free_pct": round(free_pct, 2), "free_gb": round(free / 1024**3, 2)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _check_env(self) -> Dict[str, Any]:
        required = ["BYBIT_API_KEY", "BYBIT_API_SECRET", "BOT_TOKEN", "CHAT_ID"]
        missing = [k for k in required if not _env(k)]
        return {"ok": not missing, "missing": missing}

    def _check_files(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"ok": True, "files": {}}
        for name in ("decisions.jsonl", "execution_events.jsonl", "protection_state.json"):
            p = _data_path(name)
            if p.exists():
                age = _now() - p.stat().st_mtime
                out["files"][name] = {"exists": True, "age_sec": int(age), "size": p.stat().st_size}
            else:
                out["files"][name] = {"exists": False}
        return out

    def _check_tmux(self) -> Dict[str, Any]:
        try:
            cp = subprocess.run(["tmux", "ls"], capture_output=True, text=True, timeout=3)
            txt = (cp.stdout or cp.stderr or "").strip()
            return {"ok": cp.returncode == 0, "sessions": txt.splitlines()[:10]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _check_telegram(self) -> Dict[str, Any]:
        token = _env("BOT_TOKEN") or _env("TELEGRAM_BOT_TOKEN")
        if not token:
            return {"ok": False, "error": "token_missing"}
        try:
            url = f"https://api.telegram.org/bot{urllib.parse.quote(token)}/getMe"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            return {"ok": bool(data.get("ok")), "username": ((data.get("result") or {}).get("username"))}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    def status_lines(self) -> List[str]:
        checks = self.last.get("checks") or {}
        bad = []
        for k, v in checks.items():
            if isinstance(v, dict) and not v.get("ok", True):
                bad.append(k)
        disk = checks.get("disk", {}) if isinstance(checks.get("disk"), dict) else {}
        lines = [f"🩺 HEALTH bad={','.join(bad) if bad else '-'} disk_free={disk.get('free_pct','?')}%"]
        env = checks.get("env", {}) if isinstance(checks.get("env"), dict) else {}
        if env.get("missing"):
            lines.append("⚠️ env_missing=" + ",".join(env.get("missing") or []))
        return lines


_DEFAULT_MONITOR: Optional[HealthMonitor] = None


def get_monitor() -> HealthMonitor:
    global _DEFAULT_MONITOR
    if _DEFAULT_MONITOR is None:
        _DEFAULT_MONITOR = HealthMonitor()
    return _DEFAULT_MONITOR
