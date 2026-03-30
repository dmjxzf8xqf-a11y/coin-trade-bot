import json
from pathlib import Path
from walkforward_lite import evaluate_portfolio, evaluate_symbol


def export_walkforward_report(symbols, out_path="data/walkforward_report.json"):
    rows = [evaluate_symbol(sym) for sym in symbols]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def export_walkforward_portfolio(symbols, out_path="data/walkforward_portfolio.json"):
    report = evaluate_portfolio(symbols)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    import sys
    syms = sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    rows = export_walkforward_report(syms)
    pf = export_walkforward_portfolio(syms)
    print(json.dumps({"symbols": rows, "portfolio": pf.get("portfolio", {})}, ensure_ascii=False, indent=2))
