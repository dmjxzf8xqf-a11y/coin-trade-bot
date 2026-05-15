# Winrate Intelligence Patch

Upload these files to the GitHub repo root:

- `main.py`
- `stability_winrate_patch_v1.py`
- `allin_guard_experimental_patch_v1.py`
- `signal_engine.py`
- `signal_engine_runtime_patch_v1.py`
- `fee_profit_guard_v1.py`
- `loss_reason_analyzer_v1.py`
- `symbol_adaptive_filter_v1.py`
- `daily_reporter_v1.py`
- `winrate_intelligence_patch_v1.py`

Do not upload `.env`.

## What changed

- Unified live signal scoring through `signal_engine.py`
- LONG/SHORT scoring stays symmetric
- Fee/slippage guard blocks trades with poor net target after costs
- Loss exits are classified into buckets
- Per-symbol stats block weak symbols and slightly boost strong symbols
- `/report`, `/daily`, `/symbolscores`, `/blocked`, `/losses` commands added
- Daily KST report can be sent automatically

## Confirm after restart

Expected log lines:

```text
[STAB_PATCH] loaded
[SIGNAL_ENGINE_PATCH] loaded
[ALLIN_PATCH] loaded
[WINRATE_PATCH] loaded
```

## Safe recommended env

See `ENV_RECOMMENDED_WINRATE.txt`.
