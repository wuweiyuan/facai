# Tail Pick Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add macOS automation scripts to run `tail-pick` at 14:44 on weekdays and store isolated logs.

**Architecture:** Use a Python helper to generate a tested user-level launchd plist. Shell scripts run, install, and uninstall the automation without touching existing recommendation reports.

**Tech Stack:** Python stdlib plistlib, shell scripts, pytest.

---

### Task 1: Launchd Plist Helper

**Files:**
- Create: `app/tail_pick/automation.py`
- Test: `tests/test_tail_pick.py`

- [ ] Write a failing test that verifies the plist runs at hour `14`, minute `44`, weekdays `1..5`, with the project runner script as the program.
- [ ] Implement `build_launchd_plist(project_root, python_bin="python3", hour=14, minute=44)`.
- [ ] Run `python3 -m pytest tests/test_tail_pick.py -v`.

### Task 2: Runner And Install Scripts

**Files:**
- Create: `scripts/run_tail_pick_auto.sh`
- Create: `scripts/install_tail_pick_launchd.sh`
- Create: `scripts/uninstall_tail_pick_launchd.sh`
- Modify: `README.md`

- [ ] Add runner script that writes daily and latest logs under `reports/tail_pick/`.
- [ ] Add install script that writes `~/Library/LaunchAgents/com.wayne.tail-pick.plist` and loads it.
- [ ] Add uninstall script that unloads/removes the plist.
- [ ] Document usage in README.
- [ ] Run `python3 -m pytest -q`.

