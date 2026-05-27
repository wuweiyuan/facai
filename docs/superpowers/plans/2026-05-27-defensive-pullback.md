# Defensive Pullback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a conservative pullback candidate path that can be compared against the current adaptive strategy without replacing production defaults.

**Architecture:** Extend the existing `risk_filter.pullback` contract with `min_ret_1d`, add a new strategy profile, and wire a defensive adaptive config to prefer that profile. Keep the change isolated so current `config/default.yaml` remains the production baseline.

**Tech Stack:** Python 3, unittest, YAML config, existing local Akshare cache.

---

### Task 1: Pullback Risk Filter

**Files:**
- Modify: `app/strategy/regime_risk.py`
- Test: `tests/test_regime_risk.py`

- [ ] **Step 1: Write failing tests**

Add tests showing `risk_filter.pullback.min_ret_1d` rejects a hard down signal day and accepts a mild down signal day.

- [ ] **Step 2: Run test to verify failure**

Run: `python3 -m unittest tests.test_regime_risk`

Expected: failure because `min_ret_1d` is ignored.

- [ ] **Step 3: Implement the filter**

Inside the existing pullback block, read `min_ret_1d` and reject when `ret_1d < min_ret_1d`.

- [ ] **Step 4: Verify**

Run: `python3 -m unittest tests.test_regime_risk`

Expected: tests pass.

### Task 2: Defensive Profile And Config

**Files:**
- Modify: `config/default.yaml`
- Create: `config/default.defensive.yaml`
- Test: `tests/test_recommender.py`

- [ ] **Step 1: Write failing tests**

Add a test that `apply_strategy_profile(base_cfg, "pullback_defensive")` enables pullback filtering and contains the defensive thresholds.

- [ ] **Step 2: Run test to verify failure**

Run: `python3 -m unittest tests.test_recommender`

Expected: failure because the profile does not exist.

- [ ] **Step 3: Add config**

Add `pullback_defensive` to `strategy_profiles`, and add `config/default.defensive.yaml` as an overlay-style full config copy that prefers `recommend-pullback-defensive` in adaptive regimes.

- [ ] **Step 4: Verify**

Run: `python3 -m unittest tests.test_recommender`

Expected: tests pass.

### Task 3: Comparison

**Files:**
- No production code changes.

- [ ] **Step 1: Run focused tests**

Run: `python3 -m unittest tests.test_regime_risk tests.test_recommender tests.test_holding_period tests.test_dashboard tests.test_reporting`

Expected: all pass.

- [ ] **Step 2: Replay recent selected records**

Run a lightweight local replay for `2026-04-28` onward to compare simple post-filter behavior.

Expected: defensive filters reduce the recent weak records.

- [ ] **Step 3: Run full test suite if feasible**

Run: `python3 -m unittest`

Expected: note any pre-existing unrelated failures separately.
