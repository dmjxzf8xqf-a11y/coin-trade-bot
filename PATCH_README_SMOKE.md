# Runtime Smoke Test Patch

이 패치는 매매 로직을 바꾸지 않습니다. GitHub에 올릴 때마다 코드가 최소한 부팅 가능한지 검증하는 안전장치입니다.

## 추가 파일

- `runtime_smoke_test.py`
- `.github/workflows/smoke.yml`
- `PATCH_README_SMOKE.md`

## 확인하는 것

- 전체 `.py` 문법 컴파일
- `main.py`와 런타임 패치 import 가능 여부
- `compute_signal_and_exits()`가 기존 API처럼 6개 값을 반환하는지
- `Trader.handle_command`, `Trader.tick`, `Trader._mp`, `public_state()`가 살아있는지

## 로컬/Termius 실행

```bash
cd ~/coin-trade-bot && python runtime_smoke_test.py
```

정상 끝:

```text
[SMOKE] OK
```

실패하면 GitHub에 올린 파일 중 import/문법/패치 충돌이 있다는 뜻입니다.
