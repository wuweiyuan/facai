# Adaptive Parameter Optimization Design

## Goal

Optimize `recommend-adaptive` parameters for a balanced profile: increase trade count and improve net return while keeping drawdown near or below the current adaptive balanced baseline.

This is an optimization of the formal daily adaptive strategy only. It does not change standalone `recommend-pullback`, `recommend-oversold`, `tail-pick`, or `auction-pick` behavior.

## Baseline

The current merged adaptive balanced configuration is the baseline.

Backtest command:

```bash
python3 -m app.main backtest-adaptive --start 2024-01-01 --end 2026-03-23 --entry-price next-open --output table --no-save-report
```

Baseline metrics:

- Total trades: 61
- Average 1 day net return: 0.3994%
- Average 3 day net return: 0.9931%
- Average 5 day net return: 0.7431%
- Max drawdown proxy: 16.28%

The fixed-parameter pre-override baseline had higher net returns but higher drawdown. The optimization should not blindly revert to that more aggressive behavior.

## Success Criteria

Primary acceptance target:

- Total trades must be greater than 61, with a target near or above 75 trades.
- Max drawdown proxy should be less than or equal to 16.28%.
- Average 3 day net return and 5 day net return should improve over the current adaptive balanced baseline.

Fallback acceptance target:

- If no candidate satisfies all primary conditions, accept no default config change.
- Report the top Pareto candidates that trade off return, trade count, and drawdown.
- Only change defaults if the selected candidate has a defensible improvement profile.

## Optimization Scope

Only `adaptive_strategy.parameter_overrides` in `config/default.yaml` is in scope.

Bull-market `recommend-pullback` search dimensions:

- `strategy.pick_count`
- `risk_filter.pullback.max_close_above_ma20_pct`
- `risk_filter.pullback.max_mom20`
- `risk_filter.pullback.max_mom5`
- `risk_filter.pullback.max_rsi14`
- `risk_filter.pullback.max_volume_zscore20`

Bear-market `recommend-oversold` search dimensions:

- `strategy.pick_count`
- `risk_filter.oversold.max_mom5`
- `risk_filter.oversold.max_ret_1d`
- `risk_filter.oversold.max_rsi14`
- `risk_filter.oversold.min_volume_ratio_1_20`

The first implementation can search a conservative grid. If runtime is too high, it should split the search into phases:

1. Optimize bull pullback parameters, because prior backtests show most trades are `recommend-pullback`.
2. Optimize bear oversold parameters only if it has enough trades to matter.
3. Combine the best candidates and validate across all windows.

## Validation Windows

All optimization and validation uses `next-open` entry because it is closer to executable trading than signal-day close entry.

Primary window:

- `2024-01-01 -> 2026-03-23`

Robustness windows:

- `2024-01-01 -> 2024-12-31`
- `2025-01-01 -> 2025-12-31`
- `2025-09-01 -> 2026-03-23`

The selected candidate should not rely on a single short window. If it improves the full window but badly degrades a robustness window, it should not become the default.

## Search Output

The search should produce a ranked summary with at least:

- Parameter set identifier
- Total trades
- Average 1 day, 3 day, and 5 day net return
- Max drawdown proxy
- Adaptive strategy distribution
- Validation-window metrics for the selected candidates

The ranking score should favor:

1. Lower max drawdown
2. Higher 3 day and 5 day net return
3. More trades
4. Avoiding candidates with fragile performance concentrated in one period

## Implementation Shape

Add a local optimization script or helper that:

1. Loads `config/default.yaml`.
2. Builds candidate override dictionaries.
3. Deep-merges each candidate into the config in memory.
4. Calls `run_local_adaptive_backtest` directly rather than shelling out for every candidate.
5. Ranks candidates and prints a compact table.

The script should not save reports or mutate production config during search. Only the final selected configuration should be applied to `config/default.yaml`.

## Testing

Add focused tests for any reusable optimization scoring or candidate-generation helper. Configuration-only updates should still update existing config tests that assert default adaptive overrides.

Before completion, run:

```bash
python3 -m unittest discover -s tests
```

Also rerun the selected candidate on all validation windows with `next-open` and record the metrics in the final response.

## Non-Goals

- No real-money guarantee.
- No intraday strategy changes.
- No changes to `tail-pick` or `auction-pick`.
- No automatic live trading.
- No broad scoring-model rewrite in this iteration.
