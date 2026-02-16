# storage_utils.py
import os, json, time, shutil

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def atomic_write_json(path: str, obj, backup: bool = True):
    """
    - tmp 파일로 먼저 쓰고 -> rename으로 원자적 교체
    - 기존 파일은 .bak로 백업(옵션)
    """
    d = os.path.dirname(path) or "."
    ensure_dir(d)

    tmp = f"{path}.tmp"
    bak = f"{path}.bak"

    # backup existing
    if backup and os.path.exists(path):
        try:
            shutil.copy2(path, bak)
        except Exception:
            pass

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

    os.replace(tmp, path)

def safe_read_json(path: str, default):
    """
    - 본 파일 손상 시 .bak로 복구 시도
    """
    def _read(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    if not os.path.exists(path):
        return default

    try:
        return _read(path)
    except Exception:
        bak = f"{path}.bak"
        if os.path.exists(bak):
            try:
                data = _read(bak)
                # 복구해두기
                try:
                    atomic_write_json(path, data, backup=False)
                except Exception:
                    pass
                return data
            except Exception:
                return default
        return default

def data_dir() -> str:
    # Render면 /var/data 같은 영구 디스크 경로를 DATA_DIR로 주는 걸 추천
    return (os.getenv("DATA_DIR") or "data").rstrip("/")

def data_path(filename: str) -> str:
    return os.path.join(data_dir(), filename)
