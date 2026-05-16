# Next Research Patch v1

목적: **끄는 게 아니라 발전시키기**.

이번 패치는 Freqtrade식 연구 구조 다음 단계입니다.

## 추가 파일

- `opposite_recheck_patch_v1.py`
- `fee_target_optimizer_v1.py`
- `research_tools_v2.py`
- `freqstyle_next_patch_v1.py`
- `main.py` 업데이트

## 기능

### 1) 역방향 재평가

기존 구조는 `LONG score=91 BLOCK`이면, `SHORT`가 실제로 PASS 가능해도 놓칠 수 있습니다.  
이 패치는 `LONG/SHORT`를 다시 보고 **막힌 고점수 방향보다 실제 PASS 방향을 우선**합니다.

### 2) 수수료 기반 TP/SL 자동 보정

`SIGNAL_ENGINE result=PASS`인데 `FEE_PROFIT_BLOCK`이면 바로 포기하지 않고:

- stop_atr 후보
- tp_r 후보
- fee/slippage 이후 순수익
- RR

를 다시 계산해서 통과 가능한 TP/SL이면 DRY_RUN에서 진입 허용합니다.

### 3) 연구 명령 추가

텔레그램:

```text
/blocks
/signals
/quality
```

## 추천 env: DRY_RUN 연구용

```env
FREQSTYLE_RESEARCH_ON=true
EXIT_QUALITY_ON=true

OPPOSITE_RECHECK_ON=true
RECHECK_DRY_RUN_ONLY=true
RECHECK_MIN_SCORE=55

TARGET_OPTIMIZER_ON=true
TARGET_OPT_DRY_RUN_ONLY=true
TARGET_OPT_MIN_SCORE=70
TARGET_OPT_MAX_TP_MOVE_PCT=0.012
TARGET_OPT_MAX_STOP_MOVE_PCT=0.008
TARGET_OPT_TP_R_LEVELS=1.0,1.15,1.3,1.5,1.8,2.0
TARGET_OPT_STOP_ATR_LEVELS=0.55,0.7,0.85,1.0
```

## 실거래 전 주의

이 패치 기본값은 DRY_RUN 전용입니다. 실거래 전에 유지해도 즉시 위험하지 않게 만들었지만, 실거래 전에는 아래처럼 보수화하세요.

```env
RECHECK_DRY_RUN_ONLY=true
TARGET_OPT_DRY_RUN_ONLY=true
AI_AUTO_LEVERAGE=false
DCA_ON=false
EXPERIMENTAL_SCALP_MODE_ON=false
EXPERIMENTAL_MULTI_POS_ON=false
MAX_POS=1
DIVERSIFY=false
```

## 확인 명령

```bash
cd ~/coin-trade-bot && git pull && python3 -m py_compile opposite_recheck_patch_v1.py fee_target_optimizer_v1.py research_tools_v2.py freqstyle_next_patch_v1.py main.py trader.py && sudo systemctl restart coin-trade-bot && sleep 5 && tail -120 bot.log
```

정상 로그:

```text
[OPPOSITE_RECHECK] loaded
[TARGET_OPT] loaded
[NEXT_PATCH] loaded
```
