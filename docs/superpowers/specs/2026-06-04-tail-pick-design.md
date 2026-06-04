# Tail Pick Design

## Goal

Add an isolated end-of-day stock picker that can be run near the A-share close to produce one discretionary tail-session candidate without changing the existing T-1 daily strategy.

## Non-Goals

- Do not modify the `recommend-adaptive` strategy flow.
- Do not change the default scoring weights, adaptive regime rules, or existing strategy profiles.
- Do not write tail-pick results into the existing recommendation CSV/Markdown/TXT files.
- Do not promise returns or force a daily trade when market or stock conditions are poor.

## Architecture

The feature will live behind a new `tail-pick` CLI command and a new `app/tail_pick/` package. Existing recommendation commands keep using `Recommender` and daily bars as they do today. Tail picking will use a small independent engine that starts from a configurable candidate source, enriches candidates with intraday snapshot data, applies tail-session filters, ranks candidates, and returns at most one selected stock.

The data source boundary will be additive. `MarketDataSource` will remain compatible for existing daily strategies, while a new optional real-time snapshot protocol will describe the methods tail-pick needs. `AkshareDataSource` can implement this protocol with AkShare spot data, and tests can use fake in-memory sources.

## CLI Behavior

Command:

```bash
python3 -m app.main tail-pick --date YYYY-MM-DD --output table
```

Initial behavior:

- `--date` defaults to the current date.
- `--count` defaults to `1` and is capped at one for the table output because the desired operating mode is one daily main candidate.
- `--output table|json` mirrors existing CLI conventions.
- `--dry-run` is not needed because the command does not place orders.
- `--live` can be added after the first version. The first implementation should be a single-run command suitable for manual use at 14:30-14:55 or from cron.

## Candidate Source

The first version should keep candidate generation simple and isolated:

- Load the A-share universe through the existing data source.
- Apply existing universe filters so ST and unwanted markets remain excluded.
- Use recent daily bars to avoid weak long-term structures.
- Use intraday snapshot data only for tail-session ranking and entry risk checks.

This avoids coupling tail-pick to `recommend-adaptive` or `recommend-opportunity`. Later versions can add an option to seed from the opportunity pool, but that is outside the first implementation.

## Selection Rules

The first version should use transparent conservative rules:

- Reject stocks without a valid latest price, previous close, volume, or amount.
- Reject stocks whose intraday gain is too high, default above `7%`.
- Reject stocks whose intraday gain is negative, default below `0%`.
- Reject stocks whose latest price is below the current day's VWAP-like estimate when available.
- Reject stocks with weak recent daily trend, using close relative to MA20 and MA60.
- Prefer stronger turnover/amount, moderate positive intraday return, and recent daily trend strength.

These thresholds should live under a new `tail_pick` config key. If the key is absent, safe defaults are used in code so existing configs still load unchanged.

## Output

Table output should show:

- run date and snapshot time if available
- selected stock code and name
- latest price
- intraday return
- previous close
- score
- entry reference
- stop-loss reference
- reasons

JSON output should include a structured payload with `date`, `selected`, `candidates_scanned`, `candidates_passed`, and `filters`.

No existing recommendation report should be updated. If file persistence is added, it must write only to `reports/tail_pick.*`.

## Error Handling

- If the data source cannot provide intraday snapshots, show a clear error explaining that tail-pick needs real-time or near-real-time quote data.
- If no candidate passes filters, print an explicit no-trade message.
- If the command is run outside trading hours, still allow a single snapshot run because some providers publish the latest available quote; do not enforce time-of-day in the first version.

## Testing

Use TDD with fake data sources:

- Unit-test snapshot filtering and scoring.
- Unit-test no-candidate behavior.
- Unit-test CLI parser wiring.
- Unit-test that existing adaptive strategy resolver remains unchanged.

Avoid network tests. AkShare integration should be thin and manually verifiable.

