# Intraday Pick Optimization Design

## Goal

Improve the isolated `auction-pick` and `tail-pick` discretionary commands with a balanced profile:

- `auction-pick` should be more selective and return only two candidates by default.
- `tail-pick` should keep one candidate at most, but require stronger executable tail-session behavior.
- Both commands remain independent from `recommend-adaptive` and existing recommendation reports.

This is a risk-control and signal-quality improvement. It does not guarantee profit.

## Scope

In scope:

- Update `auction_pick` configuration defaults.
- Add stricter auction filters for trend quality, overheat control, and post-open fade control.
- Add stricter tail filters for intraday strength, close-near-high behavior, fade control, and trend quality.
- Adjust scoring so moderate, executable strength ranks above overheated or fading strength.
- Add focused tests for the new defaults and filters.

Out of scope:

- Minute-bar backtesting.
- Sector or index real-time confirmation.
- Changes to `recommend`, `recommend-pullback`, `recommend-oversold`, or `recommend-adaptive`.
- Writing intraday results into existing recommendation CSV/Markdown/TXT reports.

## Auction Strategy

The auction strategy should favor stocks that are strong enough to confirm demand, but not already too extended.

Default configuration:

- `count`: `2`
- `min_opening_gap`: `0.012`
- `max_opening_gap`: `0.04`
- `min_current_return`: `0.012`
- `max_current_return`: `0.055`
- `min_amount`: `20000000`
- `min_latest_vs_open`: `1.0`
- `max_snapshot_candidates`: `80`
- `limit_up_return`: `0.09`
- `max_close_above_ma20_pct`: `0.08`
- `max_rsi14`: `75`
- `min_ma20_slope5`: `0.0`

Hard filters:

- Reject invalid or illiquid quote rows.
- Reject candidates outside the configured opening gap and current return ranges.
- Reject candidates near limit-up, based on `limit_up_return`.
- Reject candidates with current price below open.
- Reject candidates whose completed daily trend is not aligned: `close >= ma20` and `ma20 >= ma60`.
- Reject candidates too far above MA20.
- Reject candidates with RSI14 above the configured cap.
- Reject candidates with non-positive MA20 five-day slope.

Scoring:

- Reward centered opening gap, not maximum opening gap.
- Reward current strength, but keep the configured cap as a hard overheat boundary.
- Reward liquidity up to a saturation point.
- Reward healthy daily trend through MA20/MA60 alignment and MA20 slope.
- Penalize any fade from open to latest price.

Execution notes remain conservative: wait for 9:30-9:35 confirmation, avoid broken open price, require board/sector confirmation manually, and start with partial position only.

## Tail Strategy

The tail strategy should avoid weak bounces and late-session exhaustion. It still returns at most one candidate.

Default filters remain code defaults unless a `tail_pick` config block exists:

- `min_intraday_return`: `0.01`
- `max_intraday_return`: `0.06`
- `min_amount`: `20000000`
- `stop_loss_pct`: `0.04`
- `max_snapshot_candidates`: `60`
- `min_latest_vs_open`: `1.0`
- `min_close_position`: `0.65`
- `max_fade_from_high`: `0.025`
- `max_close_above_ma20_pct`: `0.10`
- `max_rsi14`: `78`
- `min_ma20_slope5`: `0.0`

Hard filters:

- Reject invalid or illiquid quote rows.
- Reject candidates outside the configured intraday return range.
- Reject candidates with current price not above open.
- Reject candidates whose current price is not in the upper part of the intraday range.
- Reject candidates that have faded too far from the intraday high.
- Reject candidates whose completed daily trend is not aligned: `close >= ma20` and `ma20 >= ma60`.
- Reject candidates too far above MA20.
- Reject candidates with RSI14 above the configured cap.
- Reject candidates with non-positive MA20 five-day slope.

Scoring:

- Reward amount up to a saturation point.
- Reward moderate intraday return, not simply the highest return.
- Reward close position near the high.
- Reward healthy distance above MA20 without overextension.
- Penalize fade from intraday high.

Sell rules remain strict: use the printed stop loss, require next-morning strength, and default to exiting by 10:30 if strength does not persist.

## Data Flow

Both commands keep the existing flow:

1. Load the stock universe.
2. Apply shared universe filters.
3. Read one current intraday quote snapshot.
4. Pre-filter snapshot candidates.
5. Fetch completed daily bars only for pre-filtered symbols.
6. Add daily indicators.
7. Apply hard daily filters.
8. Score, sort, and print table or JSON output.

The completed daily date remains the latest trade date before the requested trade date.

## Testing

Add or update tests to cover:

- Auction default config returns `count == 2`.
- Auction rejects candidates below open after auction.
- Auction rejects weak daily trend, overextended MA20 distance, and high RSI.
- Auction ranking still prefers the best executable candidate.
- Tail rejects candidates below open, far from high, or faded too much.
- Tail rejects weak daily trend, overextended MA20 distance, and high RSI.
- Existing parser isolation remains unchanged for `tail-pick`, `auction-pick`, and `recommend-adaptive`.

Run at minimum:

```bash
python3 -m pytest tests/test_auction_pick.py tests/test_tail_pick.py -q
```

## Self Review

- No placeholders remain.
- Scope is limited to isolated intraday commands and tests.
- The design explicitly avoids guaranteed-profit claims.
- Configuration values and hard filters are concrete and testable.
