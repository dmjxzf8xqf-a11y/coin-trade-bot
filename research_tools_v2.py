"""research_tools_v2.py

Extra Freqtrade-style research commands:
- /blocks: top blocked decision reasons
- /signals: pass/block by side and symbol
- /quality: quick read of whether the bot is collecting useful samples
"""

from __future__ import annotations

import re
import time
from typing import Any

try:
    import research_db_v1 as db
except Exception as e:  # pragma: no cover
    db = None  # type: ignore
    print(f"[RESEARCH_TOOLS_V2] import failed: {e}", flush=True)


def _safe_tag(reason: str) -> str:
    s = str(reason or "")
    if "FEE_PROFIT_BLOCK" in s:
        m = re.search(r"FEE_PROFIT_BLOCK ([^\n]+)", s)
        return ("FEE_PROFIT_BLOCK " + (m.group(1) if m else "")).split(";")[0][:90]
    for token in [
        "STRATEGY_BLOCK", "RANGE_REGIME", "SCORE_LOW", "AVOID_LOW_RSI", "TREND_FAIL",
        "ADX_LOW", "ATR_LOW", "EMA_GAP_LOW", "RSI_RANGE_FAIL", "CHASE_BLOCK", "LIQ_BLOCK",
        "SPREAD", "OPPOSITE_RECHECK", "TARGET_OPT", "DL_LITE", "SYMBOL_BLOCK",
    ]:
        if token in s:
            if token == "SCORE_LOW":
                m = re.search(r"SCORE_LOW [0-9.]+<[0-9.]+", s)
                return m.group(0) if m else token
            if token == "RANGE_REGIME":
                m = re.search(r"RANGE_REGIME[^\n|]+", s)
                return m.group(0)[:90] if m else token
            return token
    first = s.splitlines()[0].strip() if s else "unknown"
    return first[:90] or "unknown"


def _rows(sql: str, args: tuple[Any, ...] = ()):
    if db is None:
        return []
    try:
        return db._rows(sql, args)  # type: ignore[attr-defined]
    except Exception:
        return []


def blocks(hours: int = 24) -> str:
    if db is None:
        return "❌ research_db_v1 로드 실패"
    since = time.time() - hours * 3600
    rows = _rows("SELECT reason, COUNT(*) AS n FROM decisions WHERE allowed=0 AND ts>=? GROUP BY reason ORDER BY n DESC LIMIT 500", (since,))
    buckets: dict[str, int] = {}
    for r in rows:
        tag = _safe_tag(str(r["reason"] or ""))
        buckets[tag] = buckets.get(tag, 0) + int(r["n"] or 0)
    if not buckets:
        return f"🧱 BLOCKS ({hours}h)\n- 기록 없음"
    top = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)[:12]
    lines = [f"🧱 BLOCKS TOP ({hours}h)"]
    for k, n in top:
        lines.append(f"- {k}: {n}")
    return "\n".join(lines)


def signals(hours: int = 24) -> str:
    if db is None:
        return "❌ research_db_v1 로드 실패"
    since = time.time() - hours * 3600
    rows = _rows(
        """
        SELECT COALESCE(side,'-') AS side,
               COUNT(*) AS n,
               SUM(CASE WHEN allowed=1 THEN 1 ELSE 0 END) AS pass_n,
               AVG(score) AS avg_score
        FROM decisions WHERE ts>=? GROUP BY side ORDER BY n DESC
        """,
        (since,),
    )
    lines = [f"📡 SIGNALS ({hours}h)"]
    if not rows:
        lines.append("- 기록 없음")
    for r in rows:
        n = int(r["n"] or 0)
        p = int(r["pass_n"] or 0)
        rate = 100.0 * p / max(1, n)
        lines.append(f"- {r['side']}: pass {p}/{n} ({rate:.1f}%) avg_score={float(r['avg_score'] or 0):.1f}")

    sym = _rows(
        """
        SELECT symbol,
               COUNT(*) AS n,
               SUM(CASE WHEN allowed=1 THEN 1 ELSE 0 END) AS pass_n
        FROM decisions WHERE ts>=? GROUP BY symbol HAVING n>=5 ORDER BY pass_n DESC, n DESC LIMIT 8
        """,
        (since,),
    )
    if sym:
        lines.append("[심볼 pass]")
        for r in sym:
            n = int(r["n"] or 0)
            p = int(r["pass_n"] or 0)
            lines.append(f"- {r['symbol']}: {p}/{n}")
    return "\n".join(lines)


def quality(hours: int = 24) -> str:
    if db is None:
        return "❌ research_db_v1 로드 실패"
    st = db.overall_stats(hours)
    ds = db.decision_stats(hours)
    pass_rate = 100.0 * ds.get("pass", 0) / max(1, ds.get("n", 0))
    lines = [
        f"🧪 SAMPLE QUALITY ({hours}h)",
        f"- decisions={ds.get('n',0)} pass={ds.get('pass',0)} block={ds.get('block',0)} pass_rate={pass_rate:.1f}%",
        f"- closed_trades={st['n']} WR={st['wr']:.1f}% PnL={st['pnl']:+.4f} PF={st['pf']:.2f}",
    ]
    if st["n"] < 30:
        lines.append("- 판정: 아직 샘플 부족. 종료거래 30회 이상 필요")
    elif st["pnl"] > 0 and st["pf"] >= 1.2:
        lines.append("- 판정: 실험군 양호. 보수 세팅 리허설 후보")
    elif st["pnl"] < 0:
        lines.append("- 판정: 마이너스. /blocks /exitstats /weakness 기준으로 조정 필요")
    else:
        lines.append("- 판정: 애매함. 손익비/exit 분해 필요")
    return "\n".join(lines)
