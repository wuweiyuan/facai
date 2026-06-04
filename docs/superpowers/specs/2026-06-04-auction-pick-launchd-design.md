# Auction Pick Launchd Design

## Goal

Add a macOS `launchd` automation wrapper for the existing `auction-pick` command so it can run once on weekday mornings after the opening auction.

## Non-Goals

- Do not change `auction-pick` selection rules.
- Do not change `tail-pick` automation, label, scripts, or report directory.
- Do not write auction results into existing recommendation reports.
- Do not place orders or run a continuous 9:20-9:25 watcher.

## Architecture

The feature mirrors the existing tail-pick automation with auction-specific names and paths:

- `app/auction_pick/automation.py` builds the `launchd` plist.
- `scripts/run_auction_pick_auto.sh` runs one `auction-pick` command and writes logs.
- `scripts/install_auction_pick_launchd.sh` writes and loads the plist.
- `scripts/uninstall_auction_pick_launchd.sh` unloads and removes the plist.

The launchd label is `com.wayne.auction-pick`. Logs live under `reports/auction_pick/`.

## Schedule

Default schedule is Monday-Friday `09:26`. This is after the A-share opening auction ends at `09:25`, while still early enough for manual review around the open.

## Runtime Behavior

The runner uses:

```bash
python3 -m app.main auction-pick --date "${RUN_DATE}" --count "${AUCTION_COUNT}" --output table
```

`AUCTION_COUNT` defaults to `5`. The script writes:

- `reports/auction_pick/YYYY-MM-DD.log`
- `reports/auction_pick/latest.log`
- `reports/auction_pick/launchd.out.log`
- `reports/auction_pick/launchd.err.log`

After each run it sends a macOS notification and opens `latest.log`, matching the existing tail-pick workflow.

## Testing

Use shell/script inspection and plist unit tests only:

- Verify plist label, schedule, working directory, runner path, and environment variables.
- Verify runner script calls `auction-pick`, writes auction-specific logs, notifies, and opens `latest.log`.
- Verify install/uninstall scripts use `com.wayne.auction-pick`.
- Run existing tail-pick tests to ensure its automation remains unchanged.
