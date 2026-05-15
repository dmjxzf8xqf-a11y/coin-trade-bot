"""ops_reality_check_v1.py

Read-only bot configuration and runtime audit helpers.

This module does not place orders. It is intentionally boring: it checks the
.env/runtime shape and prints warnings before expensive or dangerous mistakes
happen.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


def _bool(name: str, default: bool = False) -> bool:
    raw = _env(name, str(default)).lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _float(name: str, default: float = 0.0) -> float:
    try:
        return float(_env(name, str(default)))
    except Exception:
        return float(default)


def _int(name: str, default: int = 0) -> int:
    try:
        return int(float(_env(name, str(default))))
    except Exception:
        return int(default)


def _data_dir() -> Path:
    try:
        import storage_utils  # type: ignore
        return Path(storage_utils.data_dir())
    except Exception:
        return Path(os.getenv("DATA_DIR", "data"))


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _tail_jsonl(path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def risky_flags() -> Dict[str, bool]:
    return {
        "AI_AUTO_LEVERAGE": _bool("AI_AUTO_LEVERAGE", False),
        "DL_LITE_ON": _bool("DL_LITE_ON", False),
        "DCA_ON": _bool("DCA_ON", False),
        "EXPERIMENTAL_MULTI_POS_ON": _bool("EXPERIMENTAL_MULTI_POS_ON", False),
        "EXPERIMENTAL_SCALP_MODE_ON": _bool("EXPERIMENTAL_SCALP_MODE_ON", False),
        "ALLOW_SHORT": _bool("ALLOW_SHORT", False),
        "DIVERSIFY": _bool("DIVERSIFY", False),
    }


def audit_config(trader_obj: Any = None) -> Dict[str, Any]:
    warnings: List[str] = []
    errors: List[str] = []
    info: List[str] = []

    bot_token = _env("BOT_TOKEN")
    chat_id = _env("CHAT_ID")
    dry_run = _bool("DRY_RUN", True)
    strict_chat = _bool("TG_STRICT_CHAT", True)
    health_verbose = _bool("HEALTH_VERBOSE", False)
    position_mode = _env("POSITION_MODE", "ONEWAY").upper()
    base_url = _env("BYBIT_BASE_URL", "")

    if not bot_token:
        warnings.append("BOT_TOKEN 비어 있음: 텔레그램 알림/명령 불가")
    if not chat_id:
        errors.append("CHAT_ID 비어 있음: TG_STRICT_CHAT=true면 명령 전부 차단됨")
    if not strict_chat:
        errors.append("TG_STRICT_CHAT=false: 모르는 채팅방이 봇을 잡을 수 있음")
    if health_verbose:
        warnings.append("HEALTH_VERBOSE=true: /health 상세 상태 노출 가능")
    if position_mode != "ONEWAY":
        warnings.append(f"POSITION_MODE={position_mode}: 현재 패치들은 ONEWAY가 더 안전")
    if not dry_run and ("testnet" in base_url.lower()):
        warnings.append("DRY_RUN=false인데 BYBIT_BASE_URL이 testnet으로 보임")

    enter_safe = _int("ENTER_SCORE_SAFE", 70)
    enter_aggro = _int("ENTER_SCORE_AGGRO", 65)
    if enter_aggro < 65:
        warnings.append(f"ENTER_SCORE_AGGRO={enter_aggro}: 낮음, 저품질 진입 증가 가능")
    if enter_safe < 70:
        warnings.append(f"ENTER_SCORE_SAFE={enter_safe}: 낮음, SAFE 모드 의미 약화")

    max_pos = _int("MAX_POSITIONS", 1)
    if max_pos > 1 and not _bool("DIVERSIFY", False):
        warnings.append("MAX_POSITIONS>1인데 DIVERSIFY=false: 의도 확인 필요")
    if max_pos > 3:
        warnings.append(f"MAX_POSITIONS={max_pos}: 소액 계좌에 과함")

    min_turn = _float("MIN_TURNOVER24H_USDT", 0.0)
    if min_turn and min_turn < 3_000_000:
        warnings.append(f"MIN_TURNOVER24H_USDT={min_turn:.0f}: 저유동성 체결/휩쏘 위험")
    spread = _float("MAX_SPREAD_BPS", 0.0)
    if spread and spread > 15:
        warnings.append(f"MAX_SPREAD_BPS={spread}: 스프레드 허용폭 큼")

    flags = risky_flags()
    risky_on = [k for k, v in flags.items() if v and k not in ("ALLOW_SHORT", "DIVERSIFY")]
    if risky_on:
        warnings.append("위험/실험 기능 ON: " + ", ".join(risky_on))
    if flags.get("ALLOW_SHORT"):
        warnings.append("ALLOW_SHORT=true: 숏 성능 데이터 충분할 때만 권장")

    # Runtime state hints
    if trader_obj is not None:
        try:
            day_profit = float(getattr(trader_obj, "day_profit", 0.0) or 0.0)
            consec_losses = int(getattr(trader_obj, "consec_losses", 0) or 0)
            positions = len(getattr(trader_obj, "positions", []) or [])
            mode = str(getattr(trader_obj, "mode", "?"))
            enabled = bool(getattr(trader_obj, "trading_enabled", False))
            info.append(f"runtime: mode={mode} enabled={enabled} pos={positions} day≈{day_profit:.2f} consec_losses={consec_losses}")
            if consec_losses >= 2:
                warnings.append(f"연속손실 {consec_losses}: 진입 기준 상향/쿨다운 권장")
            if day_profit < 0:
                info.append(f"오늘 손익 음수: {day_profit:.2f} USDT")
        except Exception:
            pass

    data_dir = _data_dir()
    runtime = _read_json(data_dir / "runtime_state.json", {})
    daily = _read_json(data_dir / "daily_pnl.json", {})
    info.append(f"data_dir={data_dir}")
    if runtime:
        info.append(f"runtime_state.ts={runtime.get('ts')}")
    if daily:
        info.append(f"daily_pnl≈{daily.get('pnl')}")

    severity = "OK"
    if errors:
        severity = "ERROR"
    elif warnings:
        severity = "WARN"

    return {
        "severity": severity,
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "risky_flags": flags,
        "ts": int(time.time()),
    }


def format_audit(trader_obj: Any = None) -> str:
    a = audit_config(trader_obj)
    icon = "✅" if a["severity"] == "OK" else ("🚨" if a["severity"] == "ERROR" else "⚠️")
    lines = [f"{icon} OPS SELFTEST: {a['severity']}"]
    if a["errors"]:
        lines.append("\n🚨 반드시 수정")
        lines.extend(f"- {x}" for x in a["errors"][:8])
    if a["warnings"]:
        lines.append("\n⚠️ 주의")
        lines.extend(f"- {x}" for x in a["warnings"][:12])
    if a["info"]:
        lines.append("\nℹ️ 정보")
        lines.extend(f"- {x}" for x in a["info"][:8])
    lines.append("\n추천: 실전 저승률 구간은 AI_AUTO_LEVERAGE/DCA/SCALP/MULTI_POS OFF 유지")
    return "\n".join(lines)


def explain_block(reason: str) -> str:
    r = str(reason or "").upper()
    mapping = [
        ("LIQ_BLOCK", "거래대금 부족. 유동성 낮은 코인은 슬리피지/휩쏘 위험이 큼."),
        ("SPREAD", "스프레드가 넓음. 진입 순간부터 비용 손실이 커짐."),
        ("ADX", "추세 강도 부족. 횡보장일 가능성이 커서 진입 차단."),
        ("ATR", "변동성이 너무 낮거나 높음. 먹을 폭 부족 또는 손절 위험."),
        ("CHASE", "이미 너무 오른/내린 자리 추격 진입 차단."),
        ("MTF", "상위 시간봉 방향과 반대라 차단."),
        ("FEE", "수수료/슬리피지 이후 기대수익 부족."),
        ("SYMBOL", "해당 심볼 최근 성과가 나빠 임시 차단."),
        ("OPS", "운영 안전 퓨즈가 진입을 막음. /selftest 와 /ops 확인."),
        ("COOLDOWN", "쿨다운 중. 연속 손실/최근 진입 후 대기."),
    ]
    hits = [txt for key, txt in mapping if key in r]
    if not hits:
        return "해당 차단 사유는 등록된 설명이 없음. /why, /selftest, bot.log를 같이 확인."
    return "\n".join(f"- {x}" for x in hits)


def recent_decision_summary(limit: int = 300) -> Dict[str, Any]:
    data_dir = _data_dir()
    rows = _tail_jsonl(data_dir / "decision_log.jsonl", limit=limit)
    total = len(rows)
    allowed = 0
    blocked = 0
    reasons: Dict[str, int] = {}
    symbols: Dict[str, int] = {}
    sides: Dict[str, int] = {}
    for row in rows:
        ok = bool(row.get("allowed") or row.get("ok") or row.get("pass"))
        if ok:
            allowed += 1
        else:
            blocked += 1
        reason = str(row.get("reason") or row.get("why") or row.get("block") or "UNKNOWN")
        reason = reason.split("\n", 1)[0][:60]
        reasons[reason] = reasons.get(reason, 0) + 1
        sym = str(row.get("symbol") or "?").upper()
        symbols[sym] = symbols.get(sym, 0) + 1
        side = str(row.get("side") or "?").upper()
        sides[side] = sides.get(side, 0) + 1
    return {
        "total": total,
        "allowed": allowed,
        "blocked": blocked,
        "top_reasons": sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:8],
        "top_symbols": sorted(symbols.items(), key=lambda kv: kv[1], reverse=True)[:8],
        "sides": sorted(sides.items(), key=lambda kv: kv[1], reverse=True)[:5],
    }


def format_decision_summary(limit: int = 300) -> str:
    s = recent_decision_summary(limit)
    if not s["total"]:
        return "📒 decision_log 기록 없음. DECISION_LOG_ON=true인지 확인."
    lines = [f"📒 최근 판단 {s['total']}개 요약", f"- 통과: {s['allowed']} / 차단: {s['blocked']}"]
    if s["top_reasons"]:
        lines.append("\n차단/판단 사유 TOP")
        lines.extend(f"- {k}: {v}" for k, v in s["top_reasons"])
    if s["top_symbols"]:
        lines.append("\n심볼 TOP")
        lines.extend(f"- {k}: {v}" for k, v in s["top_symbols"])
    return "\n".join(lines)
