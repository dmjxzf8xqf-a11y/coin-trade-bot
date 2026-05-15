"""daily_reporter_v1.py

Korean daily report from local JSON/JSONL data.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

try:
    from storage_utils import data_path, atomic_write_json, safe_read_json
except Exception:  # pragma: no cover
    def data_path(name: str) -> str:
        Path("data").mkdir(exist_ok=True)
        return str(Path("data") / name)

    def atomic_write_json(path: str, obj: Any, backup: bool = True) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def safe_read_json(path: str, default: Any) -> Any:
        try:
            p = Path(path)
            if not p.exists():
                return default
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(str(os.getenv(name, str(default))).strip()))
    except Exception:
        return int(default)


DECISION_LOG_PATH = data_path("decision_log.jsonl")
LOSS_EVENTS_PATH = data_path("loss_events.jsonl")
LOSS_STATS_PATH = data_path("loss_reason_stats.json")
SYMBOL_STATS_PATH = data_path("symbol_stats.json")
DAILY_STATE_PATH = data_path("daily_report_state.json")
DAILY_REPORT_ON = _env_bool("DAILY_REPORT_ON", True)
DAILY_REPORT_HOUR_KST = _env_int("DAILY_REPORT_HOUR_KST", 9)


def _read_jsonl_recent(path: str, since_ts: float, limit: int = 5000) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
        for line in lines:
            try:
                obj = json.loads(line)
                if float(obj.get("ts", 0) or 0) >= since_ts:
                    out.append(obj)
            except Exception:
                continue
    except Exception:
        return []
    return out


def _top_counts(vals: list[str], limit: int = 5) -> list[tuple[str, int]]:
    d: dict[str, int] = {}
    for v in vals:
        if not v:
            continue
        d[v] = d.get(v, 0) + 1
    return sorted(d.items(), key=lambda x: x[1], reverse=True)[:limit]


def build_daily_report(now: float | None = None) -> str:
    now = time.time() if now is None else float(now)
    since = now - 24 * 3600
    decisions = _read_jsonl_recent(DECISION_LOG_PATH, since)
    losses = _read_jsonl_recent(LOSS_EVENTS_PATH, since)
    symbol_stats = safe_read_json(SYMBOL_STATS_PATH, {"symbols": {}})

    pass_cnt = 0
    block_cnt = 0
    block_reasons: list[str] = []
    for d in decisions:
        allowed = bool(d.get("allowed", d.get("ok", False)))
        if allowed:
            pass_cnt += 1
        else:
            block_cnt += 1
            reason = str(d.get("reason") or d.get("block") or "")
            if not reason and isinstance(d.get("filters"), dict):
                blocks = d["filters"].get("blocks")
                if blocks:
                    reason = str(blocks[0])
            block_reasons.append(reason[:70])

    pnl_sum = sum(float(x.get("pnl", 0) or 0) for x in losses)
    wins = sum(1 for x in losses if float(x.get("pnl", 0) or 0) >= 0)
    trade_n = len(losses)
    wr = (100.0 * wins / trade_n) if trade_n else 0.0
    loss_buckets = _top_counts([str(x.get("bucket") or "") for x in losses if float(x.get("pnl", 0) or 0) < 0])
    block_top = _top_counts(block_reasons)

    syms = []
    for sym, s in (symbol_stats.get("symbols", {}) or {}).items():
        recent = list(s.get("recent", []) or [])
        if not recent:
            continue
        wr_s = 100.0 * sum(int(x) for x in recent) / max(1, len(recent))
        syms.append((wr_s, len(recent), sym, int(s.get("consec_losses", 0) or 0), float(s.get("blocked_until", 0) or 0)))
    good = sorted(syms, key=lambda x: (x[0], x[1]), reverse=True)[:3]
    bad = sorted(syms, key=lambda x: (x[4] > now, x[3], -x[0]), reverse=True)[:3]

    lines = [
        "📊 24시간 봇 리포트",
        f"- 종료거래: {trade_n}회 | 승률: {wr:.1f}% | PnL≈{pnl_sum:.4f} USDT",
        f"- 신호 PASS/BLOCK: {pass_cnt}/{block_cnt}",
    ]
    if loss_buckets:
        lines.append("- 손실 원인 TOP: " + ", ".join(f"{k}({v})" for k, v in loss_buckets))
    else:
        lines.append("- 손실 원인 TOP: 기록 없음")
    if block_top:
        lines.append("- 차단 필터 TOP: " + ", ".join(f"{k}({v})" for k, v in block_top[:3]))
    if good:
        lines.append("- 잘 맞는 심볼: " + ", ".join(f"{sym} {wr_s:.0f}%/{n}" for wr_s, n, sym, cl, bu in good))
    if bad:
        lines.append("- 조심할 심볼: " + ", ".join(f"{sym} {wr_s:.0f}% L{cl}" for wr_s, n, sym, cl, bu in bad))

    suggestion = "유지"
    if trade_n >= 3 and wr < 40:
        suggestion = "ENTER_SCORE +3, MAX_POSITIONS=1, DCA_OFF 권장"
    elif block_cnt > pass_cnt * 5 and pass_cnt == 0:
        suggestion = "필터 과강함: SIGNAL_MIN_ADX 또는 SIGNAL_MIN_ATR_PCT 확인"
    elif trade_n == 0 and pass_cnt == 0:
        suggestion = "거래 없음: /why, /doctor로 last_skip 확인"
    lines.append(f"- 내일 추천: {suggestion}")
    return "\n".join(lines)


def _kst_day_hour(now: float) -> tuple[str, int]:
    t = time.gmtime(now + 9 * 3600)
    return time.strftime("%Y-%m-%d", t), int(time.strftime("%H", t))


def maybe_send_daily(trader_obj: Any) -> bool:
    if not DAILY_REPORT_ON:
        return False
    now = time.time()
    day, hour = _kst_day_hour(now)
    if hour != DAILY_REPORT_HOUR_KST:
        return False
    state = safe_read_json(DAILY_STATE_PATH, {})
    if isinstance(state, dict) and state.get("sent_day") == day:
        return False
    msg = build_daily_report(now)
    try:
        if hasattr(trader_obj, "notify"):
            trader_obj.notify(msg)
        elif hasattr(trader_obj, "notify_throttled"):
            trader_obj.notify_throttled(msg, 60)
        else:
            print(msg, flush=True)
        atomic_write_json(DAILY_STATE_PATH, {"sent_day": day, "sent_ts": now})
        return True
    except Exception:
        return False
