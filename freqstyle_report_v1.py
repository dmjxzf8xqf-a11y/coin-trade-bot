"""freqstyle_report_v1.py

Freqtrade-style reporting wrapper using research_db_v1.
"""

from __future__ import annotations

try:
    import research_db_v1 as db
    import adaptive_tuner_v1 as tuner
except Exception as e:  # pragma: no cover
    db = None  # type: ignore
    tuner = None  # type: ignore
    print(f"[FREQSTYLE_REPORT] import failed: {e}", flush=True)


def research(hours: int = 168) -> str:
    if db is None:
        return "❌ research_db_v1 로드 실패"
    return "\n".join([
        db.build_research_report(hours),
        "",
        db.build_group_report("side", hours=hours, limit=10, title="LONG/SHORT"),
        "",
        db.build_group_report("regime", hours=hours, limit=10, title="REGIME"),
    ])


def weakness(hours: int = 168) -> str:
    if db is None:
        return "❌ research_db_v1 로드 실패"
    return db.build_weakness_report(hours)


def exitstats(hours: int = 168) -> str:
    if db is None:
        return "❌ research_db_v1 로드 실패"
    return db.build_exit_report(hours)


def dbreport(hours: int = 168) -> str:
    if db is None:
        return "❌ research_db_v1 로드 실패"
    parts = [db.build_research_report(hours)]
    for gb, title in [("symbol", "SYMBOL"), ("side", "SIDE"), ("regime", "REGIME"), ("strategy", "STRATEGY"), ("exit_reason", "EXIT")]:
        parts.append("")
        parts.append(db.build_group_report(gb, hours=hours, limit=8, title=title))
    return "\n".join(parts)


def tune(hours: int = 168) -> str:
    if tuner is None:
        return "❌ adaptive_tuner_v1 로드 실패"
    return tuner.build_tune_report(hours)
