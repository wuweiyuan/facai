# Auction Pick Design

## Goal

Add an isolated A-share opening auction stock picker that can be run manually after 9:25 to produce a discretionary candidate pool from the current quote snapshot without changing any existing daily recommendation or tail-session strategy.

## Non-Goals

- Do not modify `recommend-adaptive`, `recommend`, `recommend-pullback`, `recommend-oversold`, or existing adaptive regime selection.
- Do not modify `tail-pick` behavior.
- Do not write auction results into existing recommendation CSV/Markdown/TXT files.
- Do not implement continuous 9:20-9:25 monitoring, order book analysis, or automatic order placement in the first version.
- Do not promise returns or force a trade when auction conditions are weak.

## Architecture

The feature will live behind a new `auction-pick` CLI command and a new `app/auction_pick/` package. Existing recommendation commands keep using `Recommender`; `tail-pick` keeps using `TailPickEngine`. Auction picking uses its own engine, models, and tests.

The engine will use the current intraday quote snapshot already exposed by `AkshareDataSource.get_intraday_quotes()`. It will combine that snapshot with recent daily bars for trend filtering. This keeps the first version additive: the existing `IntradayQuote` model can be reused, while auction-specific scoring and output stay separate.

## CLI Behavior

Command:

```bash
python3 -m app.main auction-pick --date YYYY-MM-DD --output table
```

Initial behavior:

- `--date` defaults to the current date.
- `--count` defaults to `5` and returns a ranked candidate pool rather than forcing one stock.
- `--output table|json` follows the existing CLI style.
- The command performs one snapshot run only. It is intended for manual use after 9:25, especially around 9:25-9:35.
- The command does not place orders and does not update existing recommendation reports.

## Candidate Source

The first version should keep candidate generation simple and isolated:

- Load the A-share universe through the existing data source.
- Apply existing universe filters so ST stocks, unwanted markets, and configured exclusions remain excluded.
- Fetch current quote snapshots and keep only symbols in the filtered universe.
- Fetch recent daily bars for shortlisted symbols to check MA20/MA60 trend and prior-day context.
- Do not seed from `recommend-adaptive`, `recommend-opportunity`, or `tail-pick`.

## Selection Rules

The first version should use transparent defaults under a new `auction_pick` config key:

- Reject quotes without valid latest price, previous close, open price, volume, or amount.
- Require opening gap from previous close between `1%` and `5%`.
- Require current return from previous close between `0.8%` and `6%`.
- Require amount at least `10,000,000`.
- Require latest price to be at least `99.5%` of open price, so obvious open fades are filtered out.
- Require the latest completed daily close to be above MA20, or MA20 to be above MA60.
- Reject one-price limit-up style snapshots in the first version because they are hard to enter reliably.

These thresholds are defaults in code if config is absent. Adding `auction_pick` to `config/default.yaml` must not change any existing config key semantics.

## Scoring

Rank candidates with an interpretable score:

- Moderate positive opening gap contributes to the score, with excessive gaps capped.
- Current return contributes when it remains strong but not overheated.
- Amount contributes as a liquidity/attention signal.
- Daily trend contributes when close is above MA20 and MA20 is above MA60.
- Open fade penalty applies when latest price slips below open.

Tie-break by higher score, then higher amount, then symbol for deterministic output.

## Output

Table output should show:

- run date and snapshot time if available
- candidates scanned and candidates passed
- rank, code, name, score
- opening gap
- current return
- amount
- latest price and open price
- reasons
- discretionary execution notes

JSON output should include a structured payload with `trade_date`, `selected`, `candidates_scanned`, `candidates_passed`, `filters`, and ranked candidate records.

If file persistence is added later, it must write only to a separate path such as `reports/auction_pick/`. The first implementation can print only.

## Trading Notes

The command outputs a candidate pool, not a buy signal. The intended manual confirmation after output is:

- Prefer candidates whose sector also has multiple high-open names.
- Wait for 9:30-9:35 confirmation when possible.
- Avoid buying if price breaks below open or the intraday average line.
- Use small trial position sizing for first entries.
- Treat a failed opening auction candidate as no-trade rather than averaging down.

These notes belong in output text or reasons; they should not affect existing strategies.

## Error Handling

- If the data source cannot provide intraday snapshots, show a clear error explaining that `auction-pick` needs current quote data.
- If no candidate passes filters, print an explicit no-trade message.
- If daily bars are unavailable for one stock, skip that stock and continue.
- If the command is run outside the intended time window, still allow a snapshot run because data providers may publish the latest available quote.

## Testing

Use fake data sources and no network calls:

- Unit-test quote prefiltering for gap, current return, amount, and open-fade filters.
- Unit-test daily trend filtering.
- Unit-test scoring order and deterministic tie-breaks.
- Unit-test no-candidate behavior.
- Unit-test CLI parser wiring for `auction-pick`.
- Unit-test that existing adaptive and tail-pick entry points remain unchanged.
