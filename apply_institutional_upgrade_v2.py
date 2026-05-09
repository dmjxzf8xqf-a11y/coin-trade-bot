# apply_institutional_upgrade_v2.py
# main.py에 institutional_upgrade_runtime_patch_v2 import를 자동 추가하는 설치 스크립트.

from __future__ import annotations

import argparse
from pathlib import Path

IMPORT_LINE = "import institutional_upgrade_runtime_patch_v2  # noqa: F401"


def patch_main(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    if IMPORT_LINE in text:
        return "already_installed"
    lines = text.splitlines()
    insert_at = None
    # v1 import가 있으면 바로 다음 줄에 넣는다.
    for i, line in enumerate(lines):
        if "institutional_upgrade_runtime_patch_v1" in line:
            insert_at = i + 1
    # 아니면 trader import 다음에 넣는다.
    if insert_at is None:
        for i, line in enumerate(lines):
            if line.strip().startswith("import trader") or line.strip().startswith("from trader import"):
                insert_at = i + 1
                break
    # 그래도 못 찾으면 일반 import 블록 끝에 넣는다.
    if insert_at is None:
        insert_at = 0
        for i, line in enumerate(lines[:80]):
            if line.startswith("import ") or line.startswith("from "):
                insert_at = i + 1
    backup = path.with_suffix(path.suffix + ".bak_inst_v2")
    backup.write_text(text, encoding="utf-8")
    lines.insert(insert_at, IMPORT_LINE)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"installed backup={backup} line={insert_at+1}"


def append_env(path: Path) -> str:
    block = """

# === Institutional Upgrade V2 ===
INSTITUTIONAL_V2_ON=true
INSTITUTIONAL_V2_STATUS_LINES=true
PROTECTION_GUARD_ON=true
PROTECTION_MAX_DAILY_LOSS_USDT=0
PROTECTION_SYMBOL_CONSEC_LOSSES=2
PROTECTION_SYMBOL_LOCK_SEC=43200
PROTECTION_STRATEGY_CONSEC_LOSSES=3
PROTECTION_STRATEGY_LOCK_SEC=86400
PROTECTION_GLOBAL_CONSEC_LOSSES=3
PROTECTION_GLOBAL_LOCK_SEC=3600
POSITION_RECONCILE_ON=true
POSITION_RECONCILE_INTERVAL_SEC=60
RECONCILE_REAL_FETCH_ON=true
EXEC_IDEMPOTENCY_TTL_SEC=300
HEALTH_MONITOR_ON=true
HEALTH_CHECK_INTERVAL_SEC=120
HEALTH_CHECK_TELEGRAM=false
TUNE_SYMBOLS=ONDOUSDT,ZECUSDT,BTCUSDT,ETHUSDT,SOLUSDT
TUNE_WINDOWS=30,90,180,365
TUNE_MIN_BAL=110
TUNE_MIN_WINRATE=50
TUNE_MIN_TRADES=40
TUNE_MIN_POSITIVE_WINDOWS=2
TUNE_AUTO_APPLY=false
""".lstrip()
    if path.exists():
        txt = path.read_text(encoding="utf-8")
        if "Institutional Upgrade V2" in txt or "INSTITUTIONAL_V2_ON" in txt:
            return "env_already_has_v2"
    with path.open("a", encoding="utf-8") as f:
        f.write("\n" + block)
    return "env_appended"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default="main.py")
    ap.add_argument("--env", action="store_true", help="append v2 env block to .env")
    args = ap.parse_args()
    print("main.py:", patch_main(Path(args.main)))
    if args.env:
        print(".env:", append_env(Path(".env")))
    print("✅ Institutional Upgrade V2 install step done")


if __name__ == "__main__":
    main()
