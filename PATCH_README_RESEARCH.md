# Freqstyle Research / Adaptive Tuning Patch

## GitHub에 올릴 파일

루트에 아래 파일들을 올리세요.

```text
research_db_v1.py
adaptive_tuner_v1.py
freqstyle_report_v1.py
exit_quality_patch_v1.py
freqstyle_research_patch_v1.py
main.py
```

## 기능

- SQLite DB 저장: `data/research_trades.db`
- 진입/청산/차단 기록
- LONG/SHORT, 심볼, 장세, 전략, 청산사유별 승률 + 순손익 + PF 분리
- SCORE DROP 조기청산 완화
- `/research`, `/weakness`, `/exitstats`, `/tune`, `/dbreport` 명령 추가

## 추천 DRY_RUN 실험 env

```env
FREQSTYLE_RESEARCH_ON=true
EXIT_QUALITY_ON=true
SCORE_DROP_MIN_HOLD_SEC=180
SCORE_DROP_CONFIRM_TICKS=3
DRY_RUN_SCORE_DROP_IGNORE_WHILE_LOSS=true
ADAPTIVE_SCORE_ON=false
ADAPTIVE_MIN_GROUP_TRADES=8
ADAPTIVE_MAX_SCORE_PENALTY=12
ADAPTIVE_MAX_SCORE_BOOST=4
```

`ADAPTIVE_SCORE_ON=true`는 DB 표본이 30~100건 쌓인 뒤 켜는 것을 권장합니다.

## 확인 명령어

```bash
cd ~/coin-trade-bot && git pull && python3 -m py_compile research_db_v1.py adaptive_tuner_v1.py freqstyle_report_v1.py exit_quality_patch_v1.py freqstyle_research_patch_v1.py main.py trader.py && sudo systemctl restart coin-trade-bot && sleep 5 && tail -100 bot.log
```

정상 로그:

```text
[EXIT_QUALITY] loaded
[FREQSTYLE_PATCH] loaded
```

텔레그램 확인:

```text
/research
/weakness
/exitstats
/tune
/dbreport
```

## 주의

이 패치는 수익 보장 패치가 아닙니다. 승률과 순손익을 분리해서 약점을 찾는 연구/튜닝 패치입니다. 실거래 전에는 반드시 보수 세팅으로 되돌려야 합니다.
