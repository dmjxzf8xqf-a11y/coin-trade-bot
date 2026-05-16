"""adaptive_tuner_v1.py

Data-driven tuning recommendations based on research_db_v1.
This module does NOT trade by itself. It summarizes which knobs should be changed.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import research_db_v1 as db
except Exception as e:  # pragma: no cover
    db = None  # type: ignore
    print(f"[ADAPTIVE_TUNER] import failed: {e}", flush=True)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(str(os.getenv(name, str(default))).strip()))
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except Exception:
        return float(default)


def _fmt(v: float) -> str:
    return f"{v:+.4f}"


def score_adjustment(symbol: str, side: str, regime: str = "unknown", hours: int = 168) -> tuple[int, str]:
    """Return small score adjustment from recent DB data.

    Negative adjustments are safer than positive ones. This is intentionally capped.
    """
    if db is None:
        return 0, "db_off"
    min_n = _env_int("ADAPTIVE_MIN_GROUP_TRADES", 8)
    max_penalty = abs(_env_int("ADAPTIVE_MAX_SCORE_PENALTY", 12))
    max_boost = abs(_env_int("ADAPTIVE_MAX_SCORE_BOOST", 4))
    sym = str(symbol or "").upper()
    sd = str(side or "").upper()
    rg = str(regime or "unknown").upper()

    adj = 0
    reasons: list[str] = []
    try:
        side_rows = {x["key"].upper(): x for x in db.group_stats("side", hours=hours, limit=10)}
        sr = side_rows.get(sd)
        if sr and sr["n"] >= min_n:
            if sr["pnl"] < 0 and sr["wr"] < 40:
                adj -= 5
                reasons.append(f"{sd}_weak")
            elif sr["pnl"] > 0 and sr["pf"] >= 1.2 and sr["wr"] >= 50:
                adj += 1
                reasons.append(f"{sd}_ok")
    except Exception:
        pass

    try:
        symbol_rows = {x["key"].upper(): x for x in db.group_stats("symbol", hours=hours, limit=50)}
        xr = symbol_rows.get(sym)
        if xr and xr["n"] >= min_n:
            if xr["pnl"] < 0 and xr["wr"] < 35:
                adj -= 5
                reasons.append(f"{sym}_weak")
            elif xr["pnl"] > 0 and xr["pf"] >= 1.3 and xr["wr"] >= 55:
                adj += 2
                reasons.append(f"{sym}_good")
    except Exception:
        pass

    try:
        regime_rows = {x["key"].upper(): x for x in db.group_stats("regime", hours=hours, limit=30)}
        rr = regime_rows.get(rg)
        if rr and rr["n"] >= min_n:
            if rr["pnl"] < 0 and rr["wr"] < 35:
                adj -= 4
                reasons.append(f"{rg}_weak")
            elif rr["pnl"] > 0 and rr["pf"] >= 1.2:
                adj += 1
                reasons.append(f"{rg}_ok")
    except Exception:
        pass

    adj = max(-max_penalty, min(max_boost, int(adj)))
    return adj, "+".join(reasons) if reasons else "neutral"


def build_tune_report(hours: int = 168) -> str:
    if db is None:
        return "❌ research DB 로드 실패"
    st = db.overall_stats(hours)
    lines = [
        f"🛠 ADAPTIVE TUNE 제안 ({hours}h)",
        f"- trades={st['n']} WR={st['wr']:.1f}% PnL={_fmt(st['pnl'])} PF={st['pf']:.2f}",
    ]
    if st["n"] < 30:
        lines.append("- 표본 부족: 아직 자동 결론 금지. DRY_RUN 종료거래 30회 이상 모아라")

    # Exit reason diagnostics
    exit_rows = {x["key"].upper(): x for x in db.group_stats("exit_reason", hours=hours, limit=20)}
    score_drop_n = sum(x["n"] for k, x in exit_rows.items() if "SCORE DROP" in k or "SCORE" in k)
    score_drop_pnl = sum(x["pnl"] for k, x in exit_rows.items() if "SCORE DROP" in k or "SCORE" in k)
    if score_drop_n >= 3 and score_drop_pnl < 0:
        cur_hold = _env_int("SCORE_DROP_MIN_HOLD_SEC", 180)
        cur_confirm = _env_int("SCORE_DROP_CONFIRM_TICKS", 2)
        lines.append(f"- SCORE_DROP 손실 많음: SCORE_DROP_MIN_HOLD_SEC {cur_hold}→{max(cur_hold,300)}, CONFIRM {cur_confirm}→{max(cur_confirm,3)}")

    # Direction diagnostics
    for side in db.group_stats("side", hours=hours, limit=10):
        if side["n"] >= 5 and side["pnl"] < 0 and side["wr"] < 35:
            lines.append(f"- {side['key']} 약함: 해당 방향 score penalty 또는 threshold +5 권장")
        if side["n"] >= 5 and side["pnl"] > 0 and side["pf"] >= 1.3:
            lines.append(f"- {side['key']} 양호: 해당 방향 유지/소폭 강화 가능")

    # Regime diagnostics
    for reg in db.group_stats("regime", hours=hours, limit=10):
        if reg["n"] >= 5 and reg["pnl"] < 0:
            lines.append(f"- {reg['key']} 장세 손실: 해당 regime threshold +5 또는 TP_R 재조정")

    if st["n"] >= 30:
        if st["pnl"] > 0 and st["pf"] >= 1.2:
            lines.append("- 전체 양호: 실거래 전 LEV≤3, MAX_POS=1로 3일 리허설")
        elif st["pnl"] < 0:
            lines.append("- 전체 마이너스: 실거래 금지. /weakness, /exitstats 기준으로 조건별 보정 필요")

    if len(lines) <= 2:
        lines.append("- 특별 제안 없음: 표본 더 필요")
    return "\n".join(lines)
