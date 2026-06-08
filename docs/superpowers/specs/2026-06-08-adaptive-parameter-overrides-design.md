# Adaptive Parameter Overrides Design

## Goal

Add balanced market-state-aware parameter overrides for the formal daily adaptive strategy flow.

The first version only applies to:

- `recommend-adaptive`
- `backtest-adaptive`
- `backtest-adaptive-rules`

The first version does not apply to:

- Standalone `recommend`
- Standalone `recommend-pullback`
- Standalone `recommend-oversold`
- Standalone `recommend-bull`
- Standalone `recommend-relative`
- `recommend-opportunity`
- `tail-pick`
- `auction-pick`

This keeps manual comparison commands stable while allowing the formal daily flow to adapt parameters by market regime.

## Current Behavior

The current adaptive flow already detects market state as `bull`, `neutral`, `bear`, or `unknown`.

Market state currently controls which strategy commands are attempted:

- `bull`: `recommend-pullback`, then `recommend`
- `neutral`: `cash`
- `bear`: `recommend-oversold`, then `cash`
- `unknown`: `cash`

After a strategy command is selected, its profile parameters are fixed. For example, `recommend-pullback` always uses the `pullback_confirm` profile values unless the user passes a command-line override such as `--count`.

## Proposed Behavior

After market state detection and strategy profile application, apply an additional adaptive parameter override layer:

1. Detect market state.
2. Resolve the adaptive strategy order for that market state.
3. Apply the selected strategy profile.
4. Apply market-state-specific parameter overrides for the selected adaptive command.
5. Run recommendation or backtest with the resulting config.

The override layer should be a plain deep merge over the already-profiled config. It should not use formula-based dynamic values in the first version.

## Configuration

Add `adaptive_strategy.parameter_overrides` to `config/default.yaml`.

Example shape:

```yaml
adaptive_strategy:
  parameter_overrides:
    bull:
      recommend-pullback:
        strategy:
          pick_count: 2
        risk_filter:
          pullback:
            max_close_above_ma20_pct: 0.07
            max_mom20: 0.22
    bear:
      recommend-oversold:
        strategy:
          pick_count: 2
        risk_filter:
          oversold:
            max_mom5: -0.10
            max_ret_1d: -0.025
```

The first default set should stay deliberately small:

- `bull` plus `recommend-pullback`: slightly loosen pullback distance and trend strength caps, and allow a small increase in default pick count.
- `bear` plus `recommend-oversold`: slightly loosen oversold thresholds, but keep the strategy selective.
- `neutral` and `unknown`: no parameter overrides because the current default remains `cash`.

Command-line `--count` should keep taking precedence over configured `strategy.pick_count`.

## Architecture

Add a small config helper, preferably in `app/config.py`:

```python
def apply_adaptive_parameter_overrides(
    cfg: dict[str, Any],
    market_label: str,
    command_name: str,
) -> dict[str, Any]:
    ...
```

Responsibilities:

- Read `cfg["adaptive_strategy"]["parameter_overrides"]`.
- Select overrides by `market_label` and `command_name`.
- Return a copied config with overrides deep-merged.
- Return an equivalent copied config when no override exists.
- Avoid mutating the input config.

This helper keeps the adaptive override mechanism isolated and gives future formula-based logic a single replacement point.

The existing `apply_strategy_profile` behavior should remain unchanged.

## Data Flow

For `recommend-adaptive`:

1. Load base config.
2. Detect market state.
3. Resolve adaptive run specs.
4. For each strategy command:
   - Apply strategy profile.
   - Apply adaptive parameter overrides using market state and command name.
   - Resolve pick count with command-line override precedence.
   - Run recommender.

For `backtest-adaptive` and `backtest-adaptive-rules`:

1. Detect market state for each signal date.
2. Resolve adaptive strategy order.
3. Apply strategy profile.
4. Apply adaptive parameter overrides using that date's market state and command name.
5. Score candidates using the resulting config.

Recommendation and backtest paths must use the same helper so results remain comparable.

## Error Handling

Invalid or missing `parameter_overrides` should not break existing runs.

Rules:

- Missing `parameter_overrides`: no override.
- Missing market key: no override.
- Missing command key: no override.
- Non-dict override blocks: ignore that block or treat it as no override.

The helper should not validate trading semantics, such as whether a threshold is financially sensible. Existing tests and backtests should catch bad tuning choices.

## Testing

Add focused unit tests for the config helper and adaptive flow:

- Applying `bull` plus `recommend-pullback` changes only the intended fields.
- Applying `bear` plus `recommend-oversold` changes only the intended fields.
- Missing overrides leave the profiled config unchanged.
- The helper does not mutate its input config.
- Standalone profile application remains unchanged.
- Adaptive recommendation uses the override helper before running the recommender.
- Adaptive backtest uses the same override helper.
- Command-line `--count` still overrides configured `strategy.pick_count`.

## Rollout

Keep the first rollout conservative:

- Add the helper and tests.
- Add small default overrides to `config/default.yaml`.
- Run unit tests.
- Run a short adaptive backtest comparison against the current behavior.

If the first comparison increases drawdown or produces noisy weak-market trades, reduce the `bear` overrides first. The `neutral` and `unknown` regimes should remain cash until there is evidence that adding trades there improves results.

## Future Extension

After the daily adaptive flow is stable, the same concept can be extended to `tail-pick` and `auction-pick`.

Future formula support can replace or extend the helper with continuous adjustments based on:

- Index `mom20`
- Index distance above or below MA20
- Market volatility
- Breadth or sector strength data, if later added

The first version intentionally avoids formula-based tuning to keep behavior explainable and easy to backtest.
