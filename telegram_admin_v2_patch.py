# telegram_admin_v2_patch.py
# Institutional Upgrade V2 - command dispatcher helpers
# runtime_patch_v2가 Trader.handle_command/handle_telegram_command에 연결해서 사용한다.

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


def _send(trader: Any, msg: str) -> None:
    for name in ("notify", "tg_send", "_notify", "send_message"):
        fn = getattr(trader, name, None)
        if callable(fn):
            try:
                fn(msg)
                return
            except Exception:
                pass
    print(msg, flush=True)


def _read_json(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def handle_v2_command(trader: Any, text: str) -> bool:
    raw = str(text or "").strip()
    parts = raw.split()
    if not parts:
        return False
    cmd = parts[0].lower()

    if cmd in ("/guard", "/locks", "/protect"):
        try:
            from protection_guard import get_guard
            g = get_guard()
            if len(parts) >= 2 and parts[1].lower() == "unlock":
                g.unlock(parts[2] if len(parts) >= 3 else "all")
                _send(trader, "✅ guard unlock 완료")
                return True
            _send(trader, "\n".join(g.status_lines()))
            return True
        except Exception as e:
            _send(trader, f"❌ guard error: {e}")
            return True

    if cmd in ("/health", "/헬스"):
        try:
            from health_monitor import get_monitor
            m = get_monitor()
            m.run(force=True)
            _send(trader, "\n".join(m.status_lines()))
            return True
        except Exception as e:
            _send(trader, f"❌ health error: {e}")
            return True

    if cmd in ("/reconcile", "/syncpos"):
        try:
            from position_reconciler import get_reconciler
            r = get_reconciler()
            r.reconcile(trader, force=True)
            _send(trader, "\n".join(r.status_lines()))
            return True
        except Exception as e:
            _send(trader, f"❌ reconcile error: {e}")
            return True

    if cmd in ("/orders", "/exec"):
        try:
            from execution_engine_v2 import get_journal
            j = get_journal()
            _send(trader, "\n".join(j.status_lines()))
            return True
        except Exception as e:
            _send(trader, f"❌ exec error: {e}")
            return True

    if cmd in ("/report", "/btreport"):
        try:
            from backtest_reporter import write_report
            files = sorted(Path("data/backtests").glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not files:
                _send(trader, "❌ data/backtests/*.log 없음")
                return True
            res = write_report(files[0], Path("data/backtests"))
            port = (res.get("summary") or {}).get("portfolio") or {}
            _send(trader, f"📊 report written\nsource={files[0]}\navg_return={port.get('avg_return_pct')}% pos={port.get('positive_symbols')}/{port.get('n')}\n{res['json']}")
            return True
        except Exception as e:
            _send(trader, f"❌ report error: {e}")
            return True

    if cmd in ("/candidate", "/tuneinfo"):
        try:
            data = _read_json(os.getenv("TUNE_CANDIDATE_FILE", "symbol_profiles_candidate.json"))
            if not data:
                _send(trader, "❌ candidate 없음. 먼저 auto_param_tuner_v2.py 실행")
                return True
            lines = ["🧪 Candidate Profiles"]
            for k, v in data.items():
                if str(k).startswith("_") or not isinstance(v, dict):
                    continue
                lines.append(f"{k}: {v.get('grade')} pass={v.get('tune_pass')} score>={v.get('enter_score')} sl={v.get('stop_atr')} tp={v.get('tp_r')}")
            _send(trader, "\n".join(lines[:30]))
            return True
        except Exception as e:
            _send(trader, f"❌ candidate error: {e}")
            return True

    if cmd in ("/applycandidate", "/applyprofile2"):
        try:
            cand = Path(os.getenv("TUNE_CANDIDATE_FILE", "symbol_profiles_candidate.json"))
            dst = Path(os.getenv("SYMBOL_PROFILE_FILE", "symbol_profiles.json"))
            if not cand.exists():
                _send(trader, "❌ candidate 파일 없음")
                return True
            if dst.exists():
                backup = dst.with_suffix(dst.suffix + ".bak_apply")
                backup.write_text(dst.read_text(encoding="utf-8"), encoding="utf-8")
            dst.write_text(cand.read_text(encoding="utf-8"), encoding="utf-8")
            _send(trader, f"✅ candidate 적용 완료: {dst}\n재시작 또는 /profile reload 필요")
            return True
        except Exception as e:
            _send(trader, f"❌ applycandidate error: {e}")
            return True

    if cmd in ("/v2help", "/inst2"):
        _send(trader, "🏛 INST V2 commands\n/guard | /guard unlock all\n/health\n/reconcile\n/orders\n/report\n/candidate\n/applycandidate")
        return True

    return False
