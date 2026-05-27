# Defensive Pullback Design

## Goal

Reduce recent losses from the adaptive strategy by adding a conservative pullback candidate path without replacing the current production strategy.

## Diagnosis

Recent recommendations are mostly from `recommend-pullback`. A light replay of selected records since `2026-04-28` showed weak 3-day and 5-day follow-through, especially after `2026-05-15`. The pattern is not a single-stock outlier: several picks held up for one day and then faded over the next 3 to 5 trading days.

Two filters improved the already-selected sample:

- Avoid pullback names with unusually high signal-day volume pressure.
- Avoid names whose signal day dropped more than about 4%.

## Design

Add support for `min_ret_1d` in the pullback risk filter, mirroring the existing oversold `max_ret_1d` support. This lets a conservative profile reject signal days that are already falling too hard.

Add a new `pullback_defensive` strategy profile. It keeps the existing pullback shape, but tightens:

- `max_volume_zscore20`
- `max_vol20_std`
- `min_ret_1d`
- `max_close_above_ma20_pct`

Add a separate config file, `config/default.defensive.yaml`, that points adaptive bull/neutral/unknown regimes to `recommend-pullback-defensive` first. This file is for backtest comparison and manual trial only; it does not replace `config/default.yaml`.

Add tests for the new filter behavior and profile resolution.

## Non-Goals

- Do not replace the main adaptive strategy yet.
- Do not add new market data sources.
- Do not rewrite scoring.
- Do not change existing reports other than generating comparison output if needed.

## Validation

Run focused unit tests for risk filtering, config/profile resolution, dashboard/reporting paths already touched by recent work, and recommender behavior.

Then run a lightweight selected-record replay and, if local cache performance is acceptable, a local adaptive comparison between `config/default.yaml` and `config/default.defensive.yaml`.

## Validation Result

The recent selected-record replay supports the defensive filter as a short-term risk screen: since `2026-04-28`, the filtered records had weak next-open 3-day performance, while the kept records were positive on average.

The long-window adaptive A/B does not support replacing the main strategy with the defensive config. Over `2025-03-24 -> 2026-05-27`, the defensive candidate reduced trades but lowered average returns and increased drawdown. Keep it as an explicit experimental/tactical config only.
