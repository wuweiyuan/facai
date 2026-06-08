# Adaptive Parameter Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add balanced market-state-aware parameter overrides to the formal daily adaptive recommendation and backtest flows.

**Architecture:** Keep the override mechanism as a config-layer helper that deep-merges market-specific settings after strategy profile application. `recommend-adaptive`, `backtest-adaptive`, and `backtest-adaptive-rules` will call the same helper so recommendation and backtest behavior stay comparable. Standalone recommendation commands, opportunity pool, `tail-pick`, and `auction-pick` remain unchanged.

**Tech Stack:** Python 3, PyYAML config loading, `unittest`, local adaptive backtest modules, existing `merge_config` deep-merge helper.

---

## File Structure

- Modify `app/config.py`
  - Add `apply_adaptive_parameter_overrides`.
  - Preserve existing `load_config`, `merge_config`, and `apply_strategy_profile` behavior.
- Modify `app/main.py`
  - Import the new helper.
  - Add `_run_recommend_config` so adaptive flows can run an already-profiled and already-overridden config.
  - Apply adaptive overrides inside `_choose_adaptive_recommendations` and the CLI `recommend-adaptive` branch.
- Modify `app/backtest/local_adaptive.py`
  - Import the new helper.
  - Build adaptive profile configs per market regime.
  - Score each date with the config for that date's regime and command.
- Modify `app/backtest/local_rule_adaptive.py`
  - Mirror the `local_adaptive.py` override handling for rule-based exits.
- Modify `config/default.yaml`
  - Add conservative `adaptive_strategy.parameter_overrides`.
- Modify `tests/test_recommender.py`
  - Add config-helper tests.
  - Add adaptive recommendation wiring tests.
  - Add default-config override tests.
- Modify `tests/test_backtest.py`
  - Add focused tests that both adaptive backtest profile builders apply market-specific overrides.

---

### Task 1: Add Config Helper

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_recommender.py`

- [ ] **Step 1: Write failing tests for adaptive parameter overrides**

In `tests/test_recommender.py`, update the config import:

```python
from app.config import apply_adaptive_parameter_overrides, apply_strategy_profile, load_config
```

Add these tests inside `class TestRecommender(TestCase):` near `test_apply_strategy_profile_does_not_mutate_base_config`:

```python
    def test_apply_adaptive_parameter_overrides_applies_market_command_override(self):
        cfg = {
            "strategy": {"pick_count": 1, "weights": {"trend": 0.20}},
            "risk_filter": {"pullback": {"max_close_above_ma20_pct": 0.05}},
            "adaptive_strategy": {
                "strategy_pick_counts": {"recommend-pullback": 1},
                "parameter_overrides": {
                    "bull": {
                        "recommend-pullback": {
                            "strategy": {"pick_count": 2, "weights": {"momentum": 0.15}},
                            "risk_filter": {"pullback": {"max_close_above_ma20_pct": 0.07}},
                        }
                    }
                },
            },
        }

        merged = apply_adaptive_parameter_overrides(cfg, "bull", "recommend-pullback")

        self.assertEqual(cfg["strategy"]["pick_count"], 1)
        self.assertNotIn("momentum", cfg["strategy"]["weights"])
        self.assertEqual(merged["strategy"]["pick_count"], 2)
        self.assertEqual(merged["strategy"]["weights"]["trend"], 0.20)
        self.assertEqual(merged["strategy"]["weights"]["momentum"], 0.15)
        self.assertEqual(merged["risk_filter"]["pullback"]["max_close_above_ma20_pct"], 0.07)
        self.assertEqual(merged["adaptive_strategy"]["strategy_pick_counts"]["recommend-pullback"], 2)

    def test_apply_adaptive_parameter_overrides_returns_copy_when_missing_or_invalid(self):
        cfg = {
            "strategy": {"pick_count": 1},
            "adaptive_strategy": {
                "parameter_overrides": {
                    "bull": {
                        "recommend-pullback": "invalid-block",
                    }
                }
            },
        }

        invalid = apply_adaptive_parameter_overrides(cfg, "bull", "recommend-pullback")
        missing = apply_adaptive_parameter_overrides(cfg, "bear", "recommend-oversold")

        self.assertEqual(invalid, cfg)
        self.assertEqual(missing, cfg)
        self.assertIsNot(invalid, cfg)
        self.assertIsNot(missing, cfg)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_recommender.TestRecommender.test_apply_adaptive_parameter_overrides_applies_market_command_override tests.test_recommender.TestRecommender.test_apply_adaptive_parameter_overrides_returns_copy_when_missing_or_invalid
```

Expected: FAIL with `ImportError` or `AttributeError` because `apply_adaptive_parameter_overrides` does not exist yet.

- [ ] **Step 3: Implement the config helper**

In `app/config.py`, add this function after `apply_strategy_profile`:

```python
def apply_adaptive_parameter_overrides(
    cfg: dict[str, Any],
    market_label: str,
    command_name: str,
) -> dict[str, Any]:
    adaptive_cfg = cfg.get("adaptive_strategy", {})
    if not isinstance(adaptive_cfg, dict):
        return merge_config(cfg, {})

    all_overrides = adaptive_cfg.get("parameter_overrides", {})
    if not isinstance(all_overrides, dict):
        return merge_config(cfg, {})

    market_overrides = all_overrides.get(market_label, {})
    if not isinstance(market_overrides, dict):
        return merge_config(cfg, {})

    command_overrides = market_overrides.get(command_name, {})
    if not isinstance(command_overrides, dict):
        return merge_config(cfg, {})

    merged = merge_config(cfg, command_overrides)
    strategy_override = command_overrides.get("strategy", {})
    if isinstance(strategy_override, dict) and "pick_count" in strategy_override:
        merged.setdefault("adaptive_strategy", {}).setdefault("strategy_pick_counts", {})[command_name] = strategy_override[
            "pick_count"
        ]
    return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m unittest tests.test_recommender.TestRecommender.test_apply_adaptive_parameter_overrides_applies_market_command_override tests.test_recommender.TestRecommender.test_apply_adaptive_parameter_overrides_returns_copy_when_missing_or_invalid
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_recommender.py
git commit -m "feat: add adaptive parameter override helper"
```

---

### Task 2: Wire Overrides Into `recommend-adaptive`

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_recommender.py`

- [ ] **Step 1: Write failing tests for adaptive recommendation wiring**

In `tests/test_recommender.py`, add this test inside `class TestRecommender(TestCase):` near the existing `recommend-adaptive` CLI tests:

```python
    def test_recommend_adaptive_applies_parameter_overrides_before_running_profile(self):
        cfg = {
            "reporting": {"enabled": False},
            "adaptive_strategy": {
                "strategy_pick_counts": {"recommend-pullback": 1},
                "parameter_overrides": {
                    "bull": {
                        "recommend-pullback": {
                            "strategy": {"pick_count": 2},
                            "risk_filter": {"pullback": {"max_close_above_ma20_pct": 0.07}},
                        }
                    }
                },
            },
            "strategy_profiles": {
                "pullback_confirm": {
                    "strategy": {"name": "pullback_confirm_v1", "pick_count": 1},
                    "risk_filter": {"pullback": {"max_close_above_ma20_pct": 0.05}},
                }
            },
        }
        fake_target_date = date(2025, 3, 20)
        fake_signal_date = date(2025, 3, 19)
        fake_market_state = SimpleNamespace(label="bull")
        fake_rec = Mock()
        fake_rec.as_dict.return_value = {"symbol": "000001", "name": "Alpha"}

        with (
            patch.object(sys, "argv", ["prog", "recommend-adaptive", "--output", "json"]),
            patch("app.main.load_config", return_value=cfg),
            patch("app.main._configure_network"),
            patch("app.main._build_data_source", return_value=Mock()),
            patch("app.main._resolve_recommend_target_date", return_value=fake_target_date),
            patch("app.main._resolve_adaptive_strategy_specs", return_value=[("recommend-pullback", "pullback_confirm", "x")]),
            patch("app.main._run_recommend_config", return_value=([fake_rec], False)) as run_config,
            patch("app.main._build_opportunity_pool") as build_pool,
            patch("app.main.Recommender") as recommender_cls,
        ):
            recommender_cls.return_value.resolve_signal_date.return_value = fake_signal_date
            recommender_cls.return_value._resolve_market_state.return_value = (fake_market_state, "test reason")
            buf = io.StringIO()
            with redirect_stdout(buf):
                main_module.main()

        payload = json.loads(buf.getvalue())
        runtime_cfg = run_config.call_args.kwargs["cfg"]
        self.assertEqual(payload["chosen_strategy"], "recommend-pullback")
        self.assertEqual(run_config.call_args.kwargs["count"], 2)
        self.assertEqual(runtime_cfg["strategy"]["pick_count"], 2)
        self.assertEqual(runtime_cfg["risk_filter"]["pullback"]["max_close_above_ma20_pct"], 0.07)
        build_pool.assert_not_called()
```

- [ ] **Step 2: Run the new test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_recommender.TestRecommender.test_recommend_adaptive_applies_parameter_overrides_before_running_profile
```

Expected: FAIL because `app.main._run_recommend_config` does not exist and `recommend-adaptive` still calls `_run_recommend_profile`.

- [ ] **Step 3: Update imports in `app/main.py`**

Replace:

```python
from app.config import apply_strategy_profile, load_config
```

With:

```python
from app.config import apply_adaptive_parameter_overrides, apply_strategy_profile, load_config
```

- [ ] **Step 4: Split configured-profile execution from raw-profile execution**

In `app/main.py`, replace the full `_run_recommend_profile` function with these two functions:

```python
def _run_recommend_config(
    cfg: dict,
    section_title: str,
    target_date: date,
    count: int | None,
    output: str,
) -> tuple[list, bool]:
    _configure_network(cfg)
    ds = _build_data_source(cfg)
    rec_engine = Recommender(ds, cfg)
    report_cfg = cfg.get("reporting", {})
    reporting_enabled = bool(report_cfg.get("enabled", True))
    saved_docs: list[str] = []
    log_path = None

    def _execute_body() -> list:
        if output != "json":
            print(f"\n=== {section_title} ===")
        recs = rec_engine.recommend_many(target_date, count=count)
        if output != "json":
            _print_recommendations(recs, output)
        if reporting_enabled:
            for rec in recs:
                saved_docs.append(
                    str(
                        append_recommendation_csv(
                            rec,
                            str(report_cfg.get("recommendation_csv", "reports/recommendations.csv")),
                        )
                    )
                )
                saved_docs.append(
                    str(
                        append_recommendation_md(
                            rec,
                            str(report_cfg.get("recommendation_md", "reports/recommendations.md")),
                        )
                    )
                )
                saved_docs.append(
                    str(
                        append_recommendation_txt(
                            rec,
                            str(report_cfg.get("recommendation_txt", "reports/recommendations.txt")),
                        )
                    )
                )
        return recs

    if reporting_enabled:
        signal_date = rec_engine.resolve_signal_date(target_date)
        log_path = resolve_recommendation_output_log_path(
            signal_date,
            str(report_cfg.get("recommendation_log", "reports/{signal_date}.log")),
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_file:
            runtime_stdout = log_file if output == "json" else _TeeStdout(sys.stdout, log_file)
            with contextlib.redirect_stdout(runtime_stdout):
                recs = _execute_body()
        if output != "json":
            for saved_doc in saved_docs:
                print(f"已写入文档: {saved_doc}")
            print(f"已写入文档: {log_path}")
    else:
        if output == "json":
            with contextlib.redirect_stdout(io.StringIO()):
                recs = _execute_body()
        else:
            recs = _execute_body()
    return recs, reporting_enabled
```

Then redefine `_run_recommend_profile` as a wrapper:

```python
def _run_recommend_profile(
    base_cfg: dict,
    profile_name: str | None,
    section_title: str,
    target_date: date,
    count: int | None,
    output: str,
) -> tuple[list, bool]:
    cfg = apply_strategy_profile(base_cfg, profile_name)
    return _run_recommend_config(
        cfg=cfg,
        section_title=section_title,
        target_date=target_date,
        count=count,
        output=output,
    )
```

- [ ] **Step 5: Apply overrides in `_choose_adaptive_recommendations`**

In `app/main.py`, inside `_choose_adaptive_recommendations`, replace:

```python
        resolved_count = _resolve_adaptive_pick_count(base_cfg, cmd_name, override_count)
        cfg = apply_strategy_profile(base_cfg, profile_name)
        engine = Recommender(ds, cfg)
```

With:

```python
        cfg = apply_strategy_profile(base_cfg, profile_name)
        cfg = apply_adaptive_parameter_overrides(cfg, market_state.label, cmd_name)
        resolved_count = _resolve_adaptive_pick_count(cfg, cmd_name, override_count)
        engine = Recommender(ds, cfg)
```

- [ ] **Step 6: Apply overrides in the CLI `recommend-adaptive` loop**

In `app/main.py`, inside the `if args.cmd == "recommend-adaptive":` loop, replace:

```python
            resolved_count = _resolve_adaptive_pick_count(base_cfg, cmd_name, args.count)
            try:
                recs, reporting_enabled = _run_recommend_profile(
                    base_cfg=base_cfg,
                    profile_name=profile_name,
                    section_title=f"自适应选择: {section_title}",
                    target_date=target_date,
                    count=resolved_count,
                    output=args.output,
                )
```

With:

```python
            cfg = apply_strategy_profile(base_cfg, profile_name)
            cfg = apply_adaptive_parameter_overrides(cfg, market_state.label, cmd_name)
            resolved_count = _resolve_adaptive_pick_count(cfg, cmd_name, args.count)
            try:
                recs, reporting_enabled = _run_recommend_config(
                    cfg=cfg,
                    section_title=f"自适应选择: {section_title}",
                    target_date=target_date,
                    count=resolved_count,
                    output=args.output,
                )
```

- [ ] **Step 7: Run focused recommendation tests**

Run:

```bash
python3 -m unittest tests.test_recommender.TestRecommender.test_recommend_adaptive_applies_parameter_overrides_before_running_profile tests.test_recommender.TestRecommender.test_recommend_adaptive_cash_skips_formal_recommend_and_builds_opportunity_pool tests.test_recommender.TestRecommender.test_recommend_adaptive_does_not_run_opportunity_pool_when_signal_exists
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/main.py tests/test_recommender.py
git commit -m "feat: apply adaptive overrides to recommendations"
```

---

### Task 3: Wire Overrides Into Adaptive Backtests

**Files:**
- Modify: `app/backtest/local_adaptive.py`
- Modify: `app/backtest/local_rule_adaptive.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: Write failing backtest profile-builder tests**

In `tests/test_backtest.py`, add these imports near the top:

```python
from app.backtest import local_adaptive, local_rule_adaptive
```

Add these tests inside the existing backtest test class:

```python
    def test_local_adaptive_profiles_apply_market_parameter_overrides(self):
        cfg = {
            "strategy": {"pick_count": 1},
            "strategy_profiles": {
                "pullback_confirm": {
                    "strategy": {"pick_count": 1},
                    "risk_filter": {"pullback": {"max_close_above_ma20_pct": 0.05}},
                }
            },
            "adaptive_strategy": {
                "parameter_overrides": {
                    "bull": {
                        "recommend-pullback": {
                            "strategy": {"pick_count": 2},
                            "risk_filter": {"pullback": {"max_close_above_ma20_pct": 0.07}},
                        }
                    }
                }
            },
        }

        profiles = local_adaptive._build_adaptive_profiles(cfg, "bull")

        self.assertEqual(profiles["recommend-pullback"]["strategy"]["pick_count"], 2)
        self.assertEqual(profiles["recommend-pullback"]["risk_filter"]["pullback"]["max_close_above_ma20_pct"], 0.07)

    def test_local_rule_adaptive_profiles_apply_market_parameter_overrides(self):
        cfg = {
            "strategy": {"pick_count": 1},
            "strategy_profiles": {
                "oversold_rebound": {
                    "strategy": {"pick_count": 3},
                    "risk_filter": {"oversold": {"max_mom5": -0.12}},
                }
            },
            "adaptive_strategy": {
                "parameter_overrides": {
                    "bear": {
                        "recommend-oversold": {
                            "strategy": {"pick_count": 2},
                            "risk_filter": {"oversold": {"max_mom5": -0.10}},
                        }
                    }
                }
            },
        }

        profiles = local_rule_adaptive._build_adaptive_profiles(cfg, "bear")

        self.assertEqual(profiles["recommend-oversold"]["strategy"]["pick_count"], 2)
        self.assertEqual(profiles["recommend-oversold"]["risk_filter"]["oversold"]["max_mom5"], -0.10)
```

- [ ] **Step 2: Run the new backtest tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_backtest.TestBacktest.test_local_adaptive_profiles_apply_market_parameter_overrides tests.test_backtest.TestBacktest.test_local_rule_adaptive_profiles_apply_market_parameter_overrides
```

Expected: FAIL because `_build_adaptive_profiles` does not accept `market_label`.

- [ ] **Step 3: Update imports in both adaptive backtest modules**

In both `app/backtest/local_adaptive.py` and `app/backtest/local_rule_adaptive.py`, replace:

```python
from app.config import apply_strategy_profile
```

With:

```python
from app.config import apply_adaptive_parameter_overrides, apply_strategy_profile
```

- [ ] **Step 4: Update `_build_adaptive_profiles` in `app/backtest/local_adaptive.py`**

Replace the existing function with:

```python
def _build_adaptive_profiles(base_cfg: dict, market_label: str | None = None) -> dict[str, dict]:
    profile_names = {
        "recommend": None,
        "recommend-pullback": "pullback_confirm",
        "recommend-oversold": "oversold_rebound",
        "recommend-bull": "bull_trend_research",
        "recommend-relative": "relative_strength",
    }
    overrides = base_cfg.get("adaptive_strategy", {}).get("profile_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
    profiles = {}
    for name, default_profile in profile_names.items():
        profile_name = overrides.get(name, default_profile)
        cfg = apply_strategy_profile(base_cfg, profile_name)
        if market_label is not None:
            cfg = apply_adaptive_parameter_overrides(cfg, market_label, name)
        profiles[name] = cfg
    return profiles
```

- [ ] **Step 5: Update `_build_adaptive_profiles` in `app/backtest/local_rule_adaptive.py`**

Replace the existing function with:

```python
def _build_adaptive_profiles(base_cfg: dict, market_label: str | None = None) -> dict[str, dict]:
    profile_names = {
        "recommend": None,
        "recommend-pullback": "pullback_confirm",
        "recommend-oversold": "oversold_rebound",
        "recommend-bull": "bull_trend_research",
        "recommend-relative": "relative_strength",
    }
    overrides = base_cfg.get("adaptive_strategy", {}).get("profile_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
    profiles = {}
    for name, default_profile in profile_names.items():
        profile_name = overrides.get(name, default_profile)
        cfg = apply_strategy_profile(base_cfg, profile_name)
        if market_label is not None:
            cfg = apply_adaptive_parameter_overrides(cfg, market_label, name)
        profiles[name] = cfg
    return profiles
```

- [ ] **Step 6: Use market-specific profiles in `run_local_adaptive_backtest`**

In `app/backtest/local_adaptive.py`, after `attempted_dates` is resolved and `index_closes` is loaded, compute base market states first:

```python
    index_closes = cache.load_index_closes(index_symbol)
    market_states = {dt: detect_market_state(index_closes, dt, base_cfg) for dt in attempted_dates}
    market_labels = {dt: state.label for dt, state in market_states.items()}
    market_profile_labels = sorted(set(market_labels.values()))
    profiles_by_market = {label: _build_adaptive_profiles(base_cfg, label) for label in market_profile_labels}
    base_profiles = _build_adaptive_profiles(base_cfg)
```

Use `base_profiles` for the initial `data_freshness` disabling and universe building. Use `profiles_by_market[market_labels[signal_date]][name]` inside the candidate scoring loop:

```python
                cfg = profiles_by_market[market_labels[signal_date]][name]
                latest_view = dict(latest_dict)
                latest_view["market_mom20"] = market_states[signal_date].mom20
                if not passes_threshold(latest_view, "normal", cfg):
                    continue
                if not passes_risk_filter(latest_view, market_states[signal_date], "normal", cfg):
                    continue
```

Remove the old `market_states_by_profile` calculation from this module.

- [ ] **Step 7: Use market-specific profiles in `run_local_rule_adaptive_backtest`**

In `app/backtest/local_rule_adaptive.py`, make the same structural change:

```python
    market_states = {dt: detect_market_state(index_closes, dt, base_cfg) for dt in attempted_dates}
    market_labels = {dt: state.label for dt, state in market_states.items()}
    market_profile_labels = sorted(set(market_labels.values()))
    profiles_by_market = {label: _build_adaptive_profiles(base_cfg, label) for label in market_profile_labels}
    base_profiles = _build_adaptive_profiles(base_cfg)
```

Use `base_profiles` for `data_freshness` disabling and universe building. Use the market-specific `cfg` and base market state inside the scoring loop:

```python
                cfg = profiles_by_market[market_labels[signal_date]][name]
                latest_view = dict(latest_dict)
                latest_view["market_mom20"] = market_states[signal_date].mom20
                accepted_mode = None
                for mode in _resolve_enabled_modes(cfg):
                    if mode != "force" and not passes_threshold(latest_view, mode, cfg):
                        continue
                    if not passes_risk_filter(latest_view, market_states[signal_date], mode, cfg):
                        continue
                    accepted_mode = mode
                    break
```

Remove the old `market_states_by_profile` calculation from this module.

- [ ] **Step 8: Run focused backtest tests**

Run:

```bash
python3 -m unittest tests.test_backtest.TestBacktest.test_local_adaptive_profiles_apply_market_parameter_overrides tests.test_backtest.TestBacktest.test_local_rule_adaptive_profiles_apply_market_parameter_overrides
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/backtest/local_adaptive.py app/backtest/local_rule_adaptive.py tests/test_backtest.py
git commit -m "feat: apply adaptive overrides to backtests"
```

---

### Task 4: Add Default Balanced Overrides

**Files:**
- Modify: `config/default.yaml`
- Test: `tests/test_recommender.py`

- [ ] **Step 1: Write failing default-config test**

In `tests/test_recommender.py`, add this test near `test_default_config_promotes_stable_v2_adaptive_rules`:

```python
    def test_default_config_defines_balanced_adaptive_parameter_overrides(self):
        cfg = load_config("config/default.yaml")
        overrides = cfg["adaptive_strategy"]["parameter_overrides"]

        bull_pullback = overrides["bull"]["recommend-pullback"]
        bear_oversold = overrides["bear"]["recommend-oversold"]

        self.assertEqual(bull_pullback["strategy"]["pick_count"], 2)
        self.assertEqual(bull_pullback["risk_filter"]["pullback"]["max_close_above_ma20_pct"], 0.07)
        self.assertEqual(bull_pullback["risk_filter"]["pullback"]["max_mom20"], 0.22)
        self.assertEqual(bear_oversold["strategy"]["pick_count"], 2)
        self.assertEqual(bear_oversold["risk_filter"]["oversold"]["max_mom5"], -0.10)
        self.assertEqual(bear_oversold["risk_filter"]["oversold"]["max_ret_1d"], -0.025)
        self.assertNotIn("neutral", overrides)
        self.assertNotIn("unknown", overrides)
```

- [ ] **Step 2: Run the new default-config test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_recommender.TestRecommender.test_default_config_defines_balanced_adaptive_parameter_overrides
```

Expected: FAIL with `KeyError: 'parameter_overrides'`.

- [ ] **Step 3: Add default overrides to `config/default.yaml`**

Under `adaptive_strategy.strategy_pick_counts`, add:

```yaml
  # 平衡型参数自适应：
  # 只影响 recommend-adaptive / backtest-adaptive / backtest-adaptive-rules。
  # 单独运行 recommend-pullback / recommend-oversold 时仍使用各自 profile 原始参数。
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

- [ ] **Step 4: Run default-config test**

Run:

```bash
python3 -m unittest tests.test_recommender.TestRecommender.test_default_config_defines_balanced_adaptive_parameter_overrides
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config/default.yaml tests/test_recommender.py
git commit -m "feat: add balanced adaptive override defaults"
```

---

### Task 5: Full Verification and Comparison

**Files:**
- No source files should be modified in this task.

- [ ] **Step 1: Run focused unit tests**

Run:

```bash
python3 -m unittest tests.test_recommender tests.test_backtest
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
python3 -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 3: Run a short adaptive backtest comparison**

Run:

```bash
python3 -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --output table
```

Expected: command completes and prints a backtest table. Inspect:

- `total_trades`
- `skipped_days`
- `avg_return_1d_net`
- `avg_return_3d_net`
- `avg_return_5d_net`
- `max_drawdown_proxy`
- `adaptive_strategy_counts`

- [ ] **Step 4: Run next-open comparison**

Run:

```bash
python3 -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --entry-price next-open --output table
```

Expected: command completes and prints a backtest table using next-open entries.

- [ ] **Step 5: Check git state**

Run:

```bash
git status --short
```

Expected: no uncommitted source changes.

- [ ] **Step 6: Final summary**

Report:

- Which tests passed.
- Short-window backtest metrics.
- Whether the adaptive overrides increased trades, reduced skips, or changed drawdown.
- Any residual risk, especially if the bear oversold override increased drawdown.
