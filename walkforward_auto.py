import json
from pathlib import Path
from ai_learn import evaluate_walkforward


def export_walkforward_report(symbols, out_path="data/walkforward_report.json"):
    rows = []
    for sym in symbols:
        try:
            rows.append(evaluate_walkforward(sym))
        except Exception as e:
            rows.append({"ok": False, "symbol": sym, "reason": str(e)})
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    return rows


if __name__ == "__main__":
    import sys
    syms = sys.argv[1:] or ["ADAUSDT", "XRPUSDT", "SOLUSDT"]
    rows = export_walkforward_report(syms)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
