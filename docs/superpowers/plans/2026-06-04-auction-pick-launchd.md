# Auction Pick Launchd Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add macOS launchd automation scripts for the existing `auction-pick` command.

**Architecture:** Mirror tail-pick automation with auction-specific labels, paths, and tests. Keep all files under `app/auction_pick/`, `scripts/`, and `reports/auction_pick/` so existing tail-pick automation is unchanged.

**Tech Stack:** Python `plistlib`, shell scripts, pytest script inspection tests.

---

### Task 1: Tests

**Files:**
- Modify: `tests/test_auction_pick.py`

- [ ] Add tests that import `build_launchd_plist` from `app.auction_pick.automation`, verify label `com.wayne.auction-pick`, weekday schedule at 09:26, runner path `scripts/run_auction_pick_auto.sh`, and `AUCTION_COUNT`.
- [ ] Add script-inspection tests for `scripts/run_auction_pick_auto.sh`, `scripts/install_auction_pick_launchd.sh`, and `scripts/uninstall_auction_pick_launchd.sh`.
- [ ] Run `python3 -m pytest tests/test_auction_pick.py -q` and verify the tests fail because automation files do not exist yet.

### Task 2: Automation Module

**Files:**
- Create: `app/auction_pick/automation.py`

- [ ] Implement `build_launchd_plist(project_root, python_bin="python3", hour=9, minute=26, count=5)`.
- [ ] Implement `write_launchd_plist(...)`.
- [ ] Implement CLI args: `--project-root`, `--output`, `--python-bin`, `--hour`, `--minute`, `--count`.
- [ ] Run `python3 -m pytest tests/test_auction_pick.py -q` and verify plist tests pass.

### Task 3: Shell Scripts

**Files:**
- Create: `scripts/run_auction_pick_auto.sh`
- Create: `scripts/install_auction_pick_launchd.sh`
- Create: `scripts/uninstall_auction_pick_launchd.sh`

- [ ] Implement runner script with `REPORT_DIR=reports/auction_pick`, `AUCTION_STATUS`, `AUCTION_COUNT`, `auction-pick`, notification, and opening `latest.log`.
- [ ] Implement installer with label `com.wayne.auction-pick`, schedule `09:26`, report directory creation, unload/load.
- [ ] Implement uninstaller with label `com.wayne.auction-pick`.
- [ ] Run `python3 -m pytest tests/test_auction_pick.py tests/test_tail_pick.py -q`.

### Task 4: Verification

**Files:**
- Read/check only after implementation.

- [ ] Run `python3 -m pytest -q`.
- [ ] Run `bash -n scripts/run_auction_pick_auto.sh scripts/install_auction_pick_launchd.sh scripts/uninstall_auction_pick_launchd.sh`.
- [ ] Run `python3 -m app.auction_pick.automation --project-root /tmp/project --output /tmp/auction-pick.plist --python-bin /usr/bin/python3 --hour 9 --minute 26 --count 5`.
- [ ] Check `git diff -- app/tail_pick scripts/run_tail_pick_auto.sh scripts/install_tail_pick_launchd.sh scripts/uninstall_tail_pick_launchd.sh` is empty.

## Self-Review

The plan covers plist generation, install/uninstall scripts, runner logs, notifications, default 09:26 schedule, `AUCTION_COUNT`, and isolation from tail-pick automation. No placeholders remain.
