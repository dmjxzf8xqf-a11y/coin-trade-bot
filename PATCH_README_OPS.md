# OPS Intelligence Patch v1

추가 목적: 기능을 더 키우는 패치가 아니라, **위험 기능이 켜졌을 때 계좌가 먼저 터지는 걸 막고** 왜 막혔는지 텔레그램에서 바로 확인하는 패치입니다.

## GitHub에 올릴 파일

```text
main.py
ops_reality_check_v1.py
ops_safety_overlay_v1.py
ops_intelligence_patch_v1.py
```

기존 패치 파일들은 그대로 유지하세요.

## 새 텔레그램 명령

```text
/selftest   현재 env/런타임 위험 점검
/ops        운영 안전퓨즈 상태
/journal    decision_log 최근 요약
/explain    최근 차단 사유 설명
/safeenv    안정 우선 추천 env 출력
```

## 추천 env

```env
OPS_PATCH_ON=true
OPS_SAFETY_ON=true
OPS_LEVERAGE_CAP=8
OPS_ORDER_USDT_CAP=30
OPS_MAX_DAILY_LOSS_USDT=0
OPS_MAX_CONSEC_LOSSES=3
OPS_NO_TRADE_AFTER_LOSS_SEC=900
OPS_BLOCK_RISKY_UNTIL_PROVEN=true
OPS_MIN_TRADES_FOR_RISKY=50
OPS_MIN_WR_FOR_RISKY=60
```

`OPS_MAX_DAILY_LOSS_USDT=0`은 비활성입니다. 켜려면 예: `OPS_MAX_DAILY_LOSS_USDT=5`.
