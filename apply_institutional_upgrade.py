# apply_institutional_upgrade.py
# 목적: main.py에 institutional_upgrade_runtime_patch_v1 import 1줄을 안전하게 추가한다.
# 실행: python apply_institutional_upgrade.py

from pathlib import Path

IMPORT_LINE = "import institutional_upgrade_runtime_patch_v1  # noqa: F401"


def main() -> int:
    p = Path("main.py")
    if not p.exists():
        print("❌ main.py 없음. 레포 루트에서 실행해야 함.")
        return 1
    raw = p.read_text(encoding="utf-8")
    if IMPORT_LINE in raw:
        print("✅ 이미 main.py에 import 있음")
        return 0

    lines = raw.splitlines()
    insert_at = None
    markers = [
        "import filter_upgrade_runtime_patch_v1",
        "import ai_score_runtime_patch",
        "import trader as trader_module",
    ]
    for marker in markers:
        for i, line in enumerate(lines):
            if marker in line:
                insert_at = i + 1
        if insert_at is not None:
            break
    if insert_at is None:
        for i, line in enumerate(lines):
            if line.startswith("from trader import Trader"):
                insert_at = i
                break
    if insert_at is None:
        print("❌ import 위치를 못 찾음. main.py에서 Trader 생성 전 직접 추가해야 함:")
        print(IMPORT_LINE)
        return 1

    lines.insert(insert_at, IMPORT_LINE)
    backup = Path("main.py.bak_institutional_v1")
    backup.write_text(raw, encoding="utf-8")
    p.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"✅ main.py import 추가 완료. backup={backup}")
    print("확인: grep -n 'institutional_upgrade' main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
