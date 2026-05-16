"""research_db_v1.py

SQLite research database for the Bybit/Telegram trading bot.

Purpose:
- Store every entry/exit and selected decision in a queryable DB.
- Analyze winrate AND net PnL together, split by symbol/side/regime/exit reason.
- Keep this lightweight: stdlib sqlite3 only, no external dependency.

DB path: data/research_trades.db by default.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

try:
    from storage_utils import data_path
except Exception:  # pragma: no cover
    def data_path(name: str) -> str:
        Path("data").mkdir(exist_ok=True)
        return str(Path("data") / name)

DB_PATH = os.getenv("RESEARCH_DB_PATH") or data_path("research_trades.db")


def _connect() -> sqlite3.Connection:
    p = Path(DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return con


def init_db() -> None:
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                symbol TEXT,
                side TEXT,
                allowed INTEGER,
                score REAL,
                threshold REAL,
                reason TEXT,
                strategy TEXT,
                regime TEXT,
                price REAL,
                raw_json TEXT
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_ts REAL NOT NULL,
                exit_ts REAL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                entry_price REAL,
                exit_price REAL,
                qty REAL,
                notional REAL,
                lev REAL,
                order_usdt REAL,
                entry_score REAL,
                exit_score REAL,
                pnl_usdt REAL,
                pnl_pct REAL,
                hold_sec REAL,
                strategy TEXT,
                regime TEXT,
                exit_reason TEXT,
                loss_bucket TEXT,
                raw_entry TEXT,
                raw_exit TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
            CREATE INDEX IF NOT EXISTS idx_decisions_symbol_side ON decisions(symbol, side);
            CREATE INDEX IF NOT EXISTS idx_trades_entry_ts ON trades(entry_ts);
            CREATE INDEX IF NOT EXISTS idx_trades_exit_ts ON trades(exit_ts);
            CREATE INDEX IF NOT EXISTS idx_trades_symbol_side ON trades(symbol, side);
            CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
            """
        )


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except Exception:
        return default


def _dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "{}"


def extract_regime(reason: str | None, fallback: str = "unknown") -> str:
    text = str(reason or "")
    # signal_engine reason format: "... | regime=LOW_VOL"
    m = re.search(r"regime=([A-Za-z0-9_\-]+)", text)
    if m:
        return m.group(1).upper()
    # older strings
    m = re.search(r"RANGE_REGIME|LOW_VOL|HIGH_VOL|TREND_UP|TREND_DOWN|CHOP|RANGE", text, re.I)
    if m:
        return m.group(0).upper()
    return fallback


def extract_threshold(reason: str | None, default: float = 0.0) -> float:
    text = str(reason or "")
    m = re.search(r"threshold=([0-9]+(?:\.[0-9]+)?)", text)
    if m:
        return _safe_float(m.group(1), default)
    return default


def record_decision(
    symbol: str,
    side: str,
    allowed: bool,
    score: float | int | None = None,
    threshold: float | int | None = None,
    reason: str | None = None,
    strategy: str | None = None,
    price: float | int | None = None,
    raw: dict[str, Any] | None = None,
) -> int:
    init_db()
    reason_s = str(reason or "")
    raw = raw or {}
    regime = str(raw.get("regime") or extract_regime(reason_s))
    if threshold is None:
        threshold = extract_threshold(reason_s, 0.0)
    with _connect() as con:
        cur = con.execute(
            """INSERT INTO decisions
               (ts, symbol, side, allowed, score, threshold, reason, strategy, regime, price, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                time.time(),
                str(symbol or "").upper(),
                str(side or "").upper(),
                1 if allowed else 0,
                _safe_float(score, 0.0),
                _safe_float(threshold, 0.0),
                reason_s[:4000],
                str(strategy or ""),
                regime,
                _safe_float(price, 0.0),
                _dumps(raw),
            ),
        )
        return int(cur.lastrowid)


def record_entry(pos: dict[str, Any]) -> int:
    init_db()
    symbol = str(pos.get("symbol") or "").upper()
    side = str(pos.get("side") or "").upper()
    entry_ts = _safe_float(pos.get("entry_ts"), time.time()) or time.time()
    entry = _safe_float(pos.get("entry_price"), 0.0)
    order_usdt = _safe_float(pos.get("last_order_usdt"), _safe_float(pos.get("order_usdt"), 0.0))
    lev = _safe_float(pos.get("last_lev"), _safe_float(pos.get("lev"), 0.0))
    notional = order_usdt * lev if order_usdt and lev else 0.0
    qty = _safe_float(pos.get("qty"), 0.0)
    score = _safe_float(pos.get("entry_score"), _safe_float(pos.get("score"), 0.0))
    raw_reason = str(pos.get("reason") or pos.get("entry_reason") or "")
    regime = str(pos.get("regime") or extract_regime(raw_reason))
    with _connect() as con:
        cur = con.execute(
            """INSERT INTO trades
               (entry_ts, symbol, side, status, entry_price, qty, notional, lev, order_usdt,
                entry_score, strategy, regime, raw_entry)
               VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_ts,
                symbol,
                side,
                entry,
                qty,
                notional,
                lev,
                order_usdt,
                score,
                str(pos.get("strategy") or ""),
                regime,
                _dumps(pos),
            ),
        )
        trade_id = int(cur.lastrowid)
    return trade_id


def _find_open_trade_id(con: sqlite3.Connection, symbol: str, side: str, entry_ts: float | None = None) -> int | None:
    symbol = str(symbol or "").upper()
    side = str(side or "").upper()
    if entry_ts:
        row = con.execute(
            """SELECT id FROM trades
               WHERE status='OPEN' AND symbol=? AND side=? AND ABS(entry_ts - ?) < 5
               ORDER BY id DESC LIMIT 1""",
            (symbol, side, float(entry_ts)),
        ).fetchone()
        if row:
            return int(row["id"])
    row = con.execute(
        "SELECT id FROM trades WHERE status='OPEN' AND symbol=? AND side=? ORDER BY id DESC LIMIT 1",
        (symbol, side),
    ).fetchone()
    return int(row["id"]) if row else None


def record_exit(pos: dict[str, Any], exit_reason: str, pnl_usdt: float, exit_price: float | None = None, loss_bucket: str | None = None) -> int:
    init_db()
    symbol = str(pos.get("symbol") or "").upper()
    side = str(pos.get("side") or "").upper()
    now = time.time()
    entry_ts = _safe_float(pos.get("entry_ts"), 0.0)
    entry = _safe_float(pos.get("entry_price"), 0.0)
    exit_p = _safe_float(exit_price, 0.0)
    hold_sec = max(0.0, now - entry_ts) if entry_ts else 0.0
    notional = _safe_float(pos.get("last_order_usdt"), 0.0) * _safe_float(pos.get("last_lev"), 0.0)
    if notional <= 0:
        notional = _safe_float(pos.get("notional"), 0.0)
    pnl_pct = _safe_float(pnl_usdt, 0.0) / max(notional, 1e-9) if notional > 0 else 0.0
    raw = dict(pos)
    raw.update({"exit_reason": exit_reason, "pnl_usdt": pnl_usdt, "exit_price": exit_p, "loss_bucket": loss_bucket})
    with _connect() as con:
        trade_id = _find_open_trade_id(con, symbol, side, entry_ts)
        if trade_id is None:
            cur = con.execute(
                """INSERT INTO trades
                   (entry_ts, exit_ts, symbol, side, status, entry_price, exit_price, pnl_usdt, pnl_pct,
                    hold_sec, strategy, regime, exit_reason, loss_bucket, raw_exit)
                   VALUES (?, ?, ?, ?, 'CLOSED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_ts or now,
                    now,
                    symbol,
                    side,
                    entry,
                    exit_p,
                    _safe_float(pnl_usdt, 0.0),
                    pnl_pct,
                    hold_sec,
                    str(pos.get("strategy") or ""),
                    str(pos.get("regime") or extract_regime(str(pos.get("reason") or ""))),
                    str(exit_reason or ""),
                    str(loss_bucket or ""),
                    _dumps(raw),
                ),
            )
            return int(cur.lastrowid)
        con.execute(
            """UPDATE trades
               SET exit_ts=?, status='CLOSED', exit_price=?, pnl_usdt=?, pnl_pct=?, hold_sec=?,
                   exit_reason=?, loss_bucket=?, raw_exit=?
               WHERE id=?""",
            (
                now,
                exit_p,
                _safe_float(pnl_usdt, 0.0),
                pnl_pct,
                hold_sec,
                str(exit_reason or ""),
                str(loss_bucket or ""),
                _dumps(raw),
                trade_id,
            ),
        )
        return int(trade_id)


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    init_db()
    with _connect() as con:
        return list(con.execute(sql, params).fetchall())


def _fmt_usdt(v: float) -> str:
    return f"{v:+.4f}"


def _profit_factor(gross_win: float, gross_loss_abs: float) -> float:
    if gross_loss_abs <= 1e-12:
        return 999.0 if gross_win > 0 else 0.0
    return gross_win / gross_loss_abs


def group_stats(group_by: str, hours: int = 168, limit: int = 20) -> list[dict[str, Any]]:
    allowed = {"symbol", "side", "regime", "strategy", "exit_reason", "loss_bucket"}
    if group_by not in allowed:
        raise ValueError(f"bad group_by {group_by}")
    since = time.time() - hours * 3600
    sql = f"""
        SELECT COALESCE(NULLIF({group_by}, ''), 'UNKNOWN') AS k,
               COUNT(*) AS n,
               SUM(CASE WHEN pnl_usdt >= 0 THEN 1 ELSE 0 END) AS wins,
               SUM(pnl_usdt) AS pnl,
               SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END) AS gross_win,
               SUM(CASE WHEN pnl_usdt < 0 THEN -pnl_usdt ELSE 0 END) AS gross_loss,
               AVG(hold_sec) AS avg_hold,
               AVG(pnl_usdt) AS avg_pnl
        FROM trades
        WHERE status='CLOSED' AND exit_ts >= ?
        GROUP BY k
        ORDER BY pnl ASC
        LIMIT ?
    """
    out = []
    for r in _rows(sql, (since, int(limit))):
        n = int(r["n"] or 0)
        wins = int(r["wins"] or 0)
        pnl = float(r["pnl"] or 0.0)
        gw = float(r["gross_win"] or 0.0)
        gl = float(r["gross_loss"] or 0.0)
        out.append({
            "key": str(r["k"]),
            "n": n,
            "wins": wins,
            "wr": 100.0 * wins / max(1, n),
            "pnl": pnl,
            "pf": _profit_factor(gw, gl),
            "avg_hold": float(r["avg_hold"] or 0.0),
            "avg_pnl": float(r["avg_pnl"] or 0.0),
        })
    return out


def overall_stats(hours: int = 168) -> dict[str, Any]:
    since = time.time() - hours * 3600
    rows = _rows(
        """
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN pnl_usdt >= 0 THEN 1 ELSE 0 END) AS wins,
               SUM(pnl_usdt) AS pnl,
               SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END) AS gross_win,
               SUM(CASE WHEN pnl_usdt < 0 THEN -pnl_usdt ELSE 0 END) AS gross_loss,
               AVG(hold_sec) AS avg_hold,
               MIN(pnl_usdt) AS worst,
               MAX(pnl_usdt) AS best
        FROM trades
        WHERE status='CLOSED' AND exit_ts >= ?
        """,
        (since,),
    )
    r = rows[0] if rows else {}
    n = int(r["n"] or 0) if r else 0
    wins = int(r["wins"] or 0) if r else 0
    gw = float(r["gross_win"] or 0.0) if r else 0.0
    gl = float(r["gross_loss"] or 0.0) if r else 0.0
    return {
        "n": n,
        "wins": wins,
        "losses": max(0, n - wins),
        "wr": 100.0 * wins / max(1, n),
        "pnl": float(r["pnl"] or 0.0) if r else 0.0,
        "pf": _profit_factor(gw, gl),
        "gross_win": gw,
        "gross_loss": gl,
        "avg_hold": float(r["avg_hold"] or 0.0) if r else 0.0,
        "worst": float(r["worst"] or 0.0) if r else 0.0,
        "best": float(r["best"] or 0.0) if r else 0.0,
    }


def decision_stats(hours: int = 24) -> dict[str, Any]:
    since = time.time() - hours * 3600
    rows = _rows(
        """
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN allowed=1 THEN 1 ELSE 0 END) AS pass_n,
               SUM(CASE WHEN allowed=0 THEN 1 ELSE 0 END) AS block_n
        FROM decisions WHERE ts >= ?
        """,
        (since,),
    )
    r = rows[0] if rows else {}
    return {"n": int(r["n"] or 0), "pass": int(r["pass_n"] or 0), "block": int(r["block_n"] or 0)} if r else {"n": 0, "pass": 0, "block": 0}


def build_research_report(hours: int = 168) -> str:
    st = overall_stats(hours)
    ds = decision_stats(min(24, hours))
    lines = [
        f"🧪 RESEARCH DB 리포트 ({hours}h)",
        f"- 종료거래: {st['n']}회 | 승률 {st['wr']:.1f}% ({st['wins']}W/{st['losses']}L)",
        f"- 순손익: {_fmt_usdt(st['pnl'])} USDT | PF {st['pf']:.2f} | best {st['best']:+.4f} / worst {st['worst']:+.4f}",
        f"- 평균 보유: {st['avg_hold']:.0f}s | 24h PASS/BLOCK: {ds['pass']}/{ds['block']}",
    ]
    if st["n"] < 30:
        lines.append("- 판단: 표본 부족. 최소 30회, 가능하면 100회 이상 필요")
    elif st["pnl"] > 0 and st["pf"] >= 1.2 and st["wr"] >= 50:
        lines.append("- 판단: 실험군 양호. 실거래 전 보수 세팅으로 재검증 필요")
    elif st["pnl"] < 0:
        lines.append("- 판단: 아직 실거래 불가. /weakness, /exitstats 확인")
    else:
        lines.append("- 판단: 애매함. 손익비/exit_reason 분해 필요")
    return "\n".join(lines)


def build_group_report(group_by: str, hours: int = 168, limit: int = 10, title: str | None = None) -> str:
    rows = group_stats(group_by, hours=hours, limit=limit)
    title = title or group_by
    if not rows:
        return f"📊 {title}: 기록 없음"
    lines = [f"📊 {title} 성과 ({hours}h)"]
    for x in rows:
        lines.append(f"- {x['key']}: n{x['n']} WR{x['wr']:.0f}% PnL{_fmt_usdt(x['pnl'])} PF{x['pf']:.2f} avg{x['avg_pnl']:+.4f}")
    return "\n".join(lines)


def build_exit_report(hours: int = 168) -> str:
    return build_group_report("exit_reason", hours=hours, limit=12, title="EXIT_REASON")


def build_weakness_report(hours: int = 168) -> str:
    parts = ["🧯 약점 TOP"]
    for gb, name in [("side", "방향"), ("symbol", "심볼"), ("regime", "장세"), ("exit_reason", "청산")]:
        rows = [x for x in group_stats(gb, hours=hours, limit=20) if x["n"] >= 2]
        bad = sorted(rows, key=lambda x: (x["pnl"], x["wr"]))[:3]
        if bad:
            parts.append(f"[{name}] " + " | ".join(f"{x['key']} n{x['n']} WR{x['wr']:.0f}% PnL{_fmt_usdt(x['pnl'])}" for x in bad))
    if len(parts) == 1:
        parts.append("기록 부족. 종료거래 30회 이상 필요")
    return "\n".join(parts)


# initialize on import; failure should not kill bot
try:
    init_db()
except Exception as _e:  # pragma: no cover
    print(f"[RESEARCH_DB] init failed: {_e}", flush=True)
