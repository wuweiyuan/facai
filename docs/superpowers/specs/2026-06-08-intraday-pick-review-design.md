# Intraday Pick Review Design

## Goal

Add a lightweight performance review for real `auction-pick` and `tail-pick` signals. The review evaluates the actual saved signals against the next trading day's open and close prices. It does not try to reconstruct historical intraday snapshots.

## Scope

In scope:

- Save machine-readable JSONL signal rows whenever `auction-pick` or `tail-pick` runs.
- Include no-trade rows so empty signal days remain visible in statistics.
- Add an `analyze-intraday-picks` command that reads the JSONL file and reports next-day open and close performance.
- Treat next-day open return as the primary metric.

Out of scope:

- Full historical intraday backtesting.
- Minute-bar exit simulation such as next-day 10:30.
- Parsing existing table-only `.log` files.

## Data Format

The signal file defaults to `reports/intraday_pick_signals.jsonl`. Each row contains:

- `run_time`
- `strategy`
- `trade_date`
- `rank`
- `symbol`
- `name`
- `entry_price`
- `score`
- `selected`
- `source`

Rows with no selected candidate use `selected: false`, empty symbol/name, and `entry_price: null`.

## Review Semantics

The analyzer reads saved selected rows, fetches daily bars for each symbol around the signal date, finds the next trading date, and calculates:

- `ret_next_open = next_open / entry_price - 1`
- `ret_next_close = next_close / entry_price - 1`

The summary reports signal days, no-trade days, selected signals, completed trades, skipped signals, next-open win rate, average next-open return, median next-open return, worst next-open return, and average next-close return.

## Testing

Tests cover JSONL append behavior, analyzer calculations, no-trade accounting, and CLI parser support.
