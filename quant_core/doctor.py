import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

checks = []

def add(name, ok, detail=""):
    checks.append({"check": name, "ok": bool(ok), "detail": str(detail)})

for fn in ["main.py", "trader.py", "config.py", "storage_utils.py"]:
    add(f"file:{fn}", (ROOT / fn).exists(), "required")

for mod in ["main", "trader", "config"]:
    try:
        importlib.import_module(mod)
        add(f"import:{mod}", True, "ok")
    except Exception as e:
        add(f"import:{mod}", False, e)

for env_key in ["BOT_TOKEN", "BYBIT_API_KEY", "BYBIT_API_SECRET"]:
    add(f"env:{env_key}", bool(os.getenv(env_key)), "set" if os.getenv(env_key) else "missing")

print(json.dumps({"ok": all(x["ok"] for x in checks), "checks": checks}, ensure_ascii=False, indent=2))
if not all(x["ok"] for x in checks if not x["check"].startswith('env:')):
    sys.exit(1)
