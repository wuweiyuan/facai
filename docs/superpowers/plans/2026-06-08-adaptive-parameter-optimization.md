# Adaptive Parameter Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a local parameter optimizer for `recommend-adaptive`, then apply a default config only if it improves trade count and net return without materially increasing drawdown.

**Architecture:** Add a small optimization module that generates candidate adaptive override dictionaries, runs `run_local_adaptive_backtest` in memory, scores candidates, and prints ranked metrics. Keep the optimizer separate from production recommendation paths so normal commands remain unchanged. Apply the winning candidate only through `config/default.yaml` after validation.

**Tech Stack:** Python standard library, existing YAML config helpers, existing local adaptive backtest engine, `unittest`.

---

## File Structure

- Create `app/optimization/adaptive_parameters.py`: candidate generation, metric extraction, candidate scoring, validation-window evaluation.
- Create `scripts/optimize_adaptive_parameters.py`: CLI wrapper to run the optimizer against `config/default.yaml`.
- Modify `tests/test_adaptive_parameter_optimization.py`: focused tests for candidate generation and scoring.
- Modify `config/default.yaml`: only after search finds a candidate that satisfies the accepted balanced objective.
- Modify `tests/test_recommender.py`: update default adaptive override assertions if config defaults change.

## Task 1: Add Optimization Helper Tests

**Files:**
- Create: `tests/test_adaptive_parameter_optimization.py`
- Create in Task 2: `app/optimization/adaptive_parameters.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_adaptive_parameter_optimization.py`:

```python
import unittest

from app.optimization.adaptive_parameters import (
    BASELINE,
    CandidateResult,
    generate_pullback_override_candidates,
    score_candidate,
)


class AdaptiveParameterOptimizationTest(unittest.TestCase):
    def test_generate_pullback_candidates_includes_current_balanced_and_more_active_options(self):
        candidates = list(generate_pullback_override_candidates())

        self.assertIn(
            {
                "bull": {
                    "recommend-pullback": {
                        "strategy": {"pick_count": 2},
                        "risk_filter": {
                            "pullback": {
                                "max_close_above_ma20_pct": 0.07,
                                "max_mom20": 0.22,
                            }
                        },
                    }
                }
            },
            candidates,
        )
        self.assertTrue(
            any(
                item["bull"]["recommend-pullback"]["strategy"]["pick_count"] >= 3
                or item["bull"]["recommend-pullback"]["risk_filter"]["pullback"].get("max_close_above_ma20_pct", 0) > 0.07
                for item in candidates
            )
        )

    def test_score_candidate_rewards_balanced_improvement(self):
        result = CandidateResult(
            name="candidate",
            overrides={},
            total_trades=80,
            avg_return_1d_net=0.0045,
            avg_return_3d_net=0.012,
            avg_return_5d_net=0.009,
            max_drawdown_proxy=0.15,
            adaptive_strategy_counts={"recommend-pullback": 78, "recommend-oversold": 2},
        )

        score = score_candidate(result)

        self.assertGreater(score, 0)

    def test_score_candidate_penalizes_drawdown_above_tolerance(self):
        good = CandidateResult(
            name="good",
            overrides={},
            total_trades=80,
            avg_return_1d_net=0.0045,
            avg_return_3d_net=0.012,
            avg_return_5d_net=0.009,
            max_drawdown_proxy=BASELINE.max_drawdown_proxy,
            adaptive_strategy_counts={},
        )
        bad = CandidateResult(
            name="bad",
            overrides={},
            total_trades=95,
            avg_return_1d_net=0.006,
            avg_return_3d_net=0.018,
            avg_return_5d_net=0.014,
            max_drawdown_proxy=0.22,
            adaptive_strategy_counts={},
        )

        self.assertGreater(score_candidate(good), score_candidate(bad))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_adaptive_parameter_optimization
```

Expected: fail with `ModuleNotFoundError` for `app.optimization.adaptive_parameters`.

## Task 2: Implement Optimization Helper

**Files:**
- Create: `app/optimization/__init__.py`
- Create: `app/optimization/adaptive_parameters.py`

- [ ] **Step 1: Add minimal implementation**

Create `app/optimization/__init__.py` as an empty package marker.

Create `app/optimization/adaptive_parameters.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import product
from typing import Any, Iterable

from app.backtest.entry_price import ENTRY_PRICE_NEXT_OPEN
from app.backtest.local_adaptive import run_local_adaptive_backtest
from app.config import merge_config


@dataclass(frozen=True)
class CandidateResult:
    name: str
    overrides: dict[str, Any]
    total_trades: int
    avg_return_1d_net: float
    avg_return_3d_net: float
    avg_return_5d_net: float
    max_drawdown_proxy: float
    adaptive_strategy_counts: dict[str, int]


BASELINE = CandidateResult(
    name="current-balanced",
    overrides={},
    total_trades=61,
    avg_return_1d_net=0.003994,
    avg_return_3d_net=0.009931,
    avg_return_5d_net=0.007431,
    max_drawdown_proxy=0.1628,
    adaptive_strategy_counts={"recommend-oversold": 2, "recommend-pullback": 59},
)


def generate_pullback_override_candidates() -> Iterable[dict[str, Any]]:
    pick_counts = [2, 3]
    max_distances = [0.07, 0.08, 0.09, 0.10]
    max_mom20_values = [0.22, 0.25, 0.28]
    max_mom5_values = [0.10, 0.12]
    max_rsi_values = [78.0, 80.0]
    max_volume_zscore_values = [2.2, 2.5]

    for pick_count, max_distance, max_mom20, max_mom5, max_rsi14, max_volume_zscore20 in product(
        pick_counts,
        max_distances,
        max_mom20_values,
        max_mom5_values,
        max_rsi_values,
        max_volume_zscore_values,
    ):
        pullback_filter = {
            "max_close_above_ma20_pct": max_distance,
            "max_mom20": max_mom20,
        }
        if max_mom5 != 0.10:
            pullback_filter["max_mom5"] = max_mom5
        if max_rsi14 != 78.0:
            pullback_filter["max_rsi14"] = max_rsi14
        if max_volume_zscore20 != 2.2:
            pullback_filter["max_volume_zscore20"] = max_volume_zscore20

        yield {
            "bull": {
                "recommend-pullback": {
                    "strategy": {"pick_count": pick_count},
                    "risk_filter": {"pullback": pullback_filter},
                }
            }
        }


def generate_oversold_override_candidates() -> Iterable[dict[str, Any]]:
    pick_counts = [2, 3]
    max_mom5_values = [-0.12, -0.10, -0.08]
    max_ret_1d_values = [-0.035, -0.025, -0.015]
    max_rsi_values = [42.0, 45.0]
    min_volume_ratio_values = [0.8, 1.0]

    for pick_count, max_mom5, max_ret_1d, max_rsi14, min_volume_ratio in product(
        pick_counts,
        max_mom5_values,
        max_ret_1d_values,
        max_rsi_values,
        min_volume_ratio_values,
    ):
        yield {
            "bear": {
                "recommend-oversold": {
                    "strategy": {"pick_count": pick_count},
                    "risk_filter": {
                        "oversold": {
                            "max_mom5": max_mom5,
                            "max_ret_1d": max_ret_1d,
                            "max_rsi14": max_rsi14,
                            "min_volume_ratio_1_20": min_volume_ratio,
                        }
                    },
                }
            }
        }


def combine_override_candidates(
    pullback_candidates: Iterable[dict[str, Any]],
    oversold_candidates: Iterable[dict[str, Any]],
) -> Iterable[dict[str, Any]]:
    oversold_list = list(oversold_candidates)
    for pullback in pullback_candidates:
        for oversold in oversold_list:
            yield merge_config({"adaptive_strategy": {"parameter_overrides": pullback}}, {"adaptive_strategy": {"parameter_overrides": oversold}})[
                "adaptive_strategy"
            ]["parameter_overrides"]


def apply_parameter_overrides(base_cfg: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    return merge_config(base_cfg, {"adaptive_strategy": {"parameter_overrides": overrides}})


def summarize_candidate(name: str, overrides: dict[str, Any], summary: dict[str, Any]) -> CandidateResult:
    return CandidateResult(
        name=name,
        overrides=overrides,
        total_trades=int(summary.get("total_trades", 0)),
        avg_return_1d_net=float(summary.get("avg_return_1d_net", 0.0)),
        avg_return_3d_net=float(summary.get("avg_return_3d_net", 0.0)),
        avg_return_5d_net=float(summary.get("avg_return_5d_net", 0.0)),
        max_drawdown_proxy=float(summary.get("max_drawdown_proxy", 0.0)),
        adaptive_strategy_counts=dict(summary.get("adaptive_strategy_counts", {})),
    )


def score_candidate(result: CandidateResult, baseline: CandidateResult = BASELINE) -> float:
    drawdown_penalty = max(result.max_drawdown_proxy - baseline.max_drawdown_proxy, 0.0) * 80.0
    severe_drawdown_penalty = max(result.max_drawdown_proxy - 0.17, 0.0) * 200.0
    trade_bonus = min(max(result.total_trades - baseline.total_trades, 0), 40) * 0.01
    return_bonus = (
        (result.avg_return_3d_net - baseline.avg_return_3d_net) * 35.0
        + (result.avg_return_5d_net - baseline.avg_return_5d_net) * 25.0
        + (result.avg_return_1d_net - baseline.avg_return_1d_net) * 10.0
    )
    return return_bonus + trade_bonus - drawdown_penalty - severe_drawdown_penalty


def is_primary_acceptance(result: CandidateResult, baseline: CandidateResult = BASELINE) -> bool:
    return (
        result.total_trades > baseline.total_trades
        and result.max_drawdown_proxy <= baseline.max_drawdown_proxy
        and result.avg_return_3d_net > baseline.avg_return_3d_net
        and result.avg_return_5d_net > baseline.avg_return_5d_net
    )


def run_candidate(
    base_cfg: dict[str, Any],
    name: str,
    overrides: dict[str, Any],
    start: date,
    end: date,
) -> CandidateResult:
    cfg = apply_parameter_overrides(base_cfg, overrides)
    summary = run_local_adaptive_backtest(cfg, start, end, None, ENTRY_PRICE_NEXT_OPEN)
    return summarize_candidate(name, overrides, summary)
```

- [ ] **Step 2: Run helper tests**

Run:

```bash
python3 -m unittest tests.test_adaptive_parameter_optimization
```

Expected: pass.

- [ ] **Step 3: Commit helper**

Run:

```bash
git add app/optimization tests/test_adaptive_parameter_optimization.py
git commit -m "feat: add adaptive parameter optimization helpers"
```

## Task 3: Add Optimizer CLI

**Files:**
- Create: `scripts/optimize_adaptive_parameters.py`

- [ ] **Step 1: Write CLI script**

Create `scripts/optimize_adaptive_parameters.py`:

```python
from __future__ import annotations

import argparse
from datetime import date
from itertools import islice

from app.config import load_config
from app.optimization.adaptive_parameters import (
    BASELINE,
    combine_override_candidates,
    generate_oversold_override_candidates,
    generate_pullback_override_candidates,
    is_primary_acceptance,
    run_candidate,
    score_candidate,
)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize adaptive strategy parameter overrides")
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-03-23")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()

    cfg = load_config(args.config)
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    candidates = combine_override_candidates(
        generate_pullback_override_candidates(),
        generate_oversold_override_candidates(),
    )

    results = []
    for idx, overrides in enumerate(islice(candidates, args.limit), start=1):
        name = f"candidate-{idx:03d}"
        result = run_candidate(cfg, name, overrides, start, end)
        results.append(result)
        print(
            f"{name} trades={result.total_trades} "
            f"1d={result.avg_return_1d_net:.4%} 3d={result.avg_return_3d_net:.4%} "
            f"5d={result.avg_return_5d_net:.4%} dd={result.max_drawdown_proxy:.2%} "
            f"score={score_candidate(result):.4f}",
            flush=True,
        )

    ranked = sorted(results, key=score_candidate, reverse=True)
    accepted = [item for item in ranked if is_primary_acceptance(item)]

    print()
    print("BASELINE")
    print(
        f"{BASELINE.name} trades={BASELINE.total_trades} "
        f"1d={BASELINE.avg_return_1d_net:.4%} 3d={BASELINE.avg_return_3d_net:.4%} "
        f"5d={BASELINE.avg_return_5d_net:.4%} dd={BASELINE.max_drawdown_proxy:.2%}"
    )
    print()
    print("TOP")
    for item in ranked[: args.top]:
        flag = "ACCEPT" if is_primary_acceptance(item) else "REVIEW"
        print(
            f"{flag} {item.name} trades={item.total_trades} "
            f"1d={item.avg_return_1d_net:.4%} 3d={item.avg_return_3d_net:.4%} "
            f"5d={item.avg_return_5d_net:.4%} dd={item.max_drawdown_proxy:.2%} "
            f"counts={item.adaptive_strategy_counts} score={score_candidate(item):.4f} "
            f"overrides={item.overrides}"
        )
    print()
    print(f"primary_acceptance_count={len(accepted)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run smoke search**

Run:

```bash
python3 scripts/optimize_adaptive_parameters.py --limit 3 --top 3
```

Expected: prints three candidate rows, a `BASELINE` section, a `TOP` section, and `primary_acceptance_count=...`.

- [ ] **Step 3: Commit CLI**

Run:

```bash
git add scripts/optimize_adaptive_parameters.py
git commit -m "feat: add adaptive parameter optimization CLI"
```

## Task 4: Run Search And Select Candidate

**Files:**
- No code edits unless a qualifying candidate is found.

- [ ] **Step 1: Run first search batch**

Run:

```bash
python3 scripts/optimize_adaptive_parameters.py --limit 120 --top 20
```

Expected: candidate table with at least one `TOP` section. If `primary_acceptance_count` is zero, increase `--limit` or run narrower manual grids around the best scoring candidates.

- [ ] **Step 2: Validate top candidates manually**

For each top candidate that either passes primary acceptance or is close, apply its `overrides` in memory or temporary config and run:

```bash
python3 -m app.main backtest-adaptive --start 2024-01-01 --end 2026-03-23 --entry-price next-open --output table --no-save-report
python3 -m app.main backtest-adaptive --start 2024-01-01 --end 2024-12-31 --entry-price next-open --output table --no-save-report
python3 -m app.main backtest-adaptive --start 2025-01-01 --end 2025-12-31 --entry-price next-open --output table --no-save-report
python3 -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --entry-price next-open --output table --no-save-report
```

Expected: selected candidate improves the primary window and does not badly degrade robustness windows. If no candidate qualifies, do not modify `config/default.yaml`.

## Task 5: Apply Winning Defaults

**Files:**
- Modify: `config/default.yaml`
- Modify: `tests/test_recommender.py`

- [ ] **Step 1: Update failing config test**

If Task 4 finds no qualifying candidate, skip Task 5 and do not change `config/default.yaml`.

If Task 4 finds a qualifying candidate, modify `tests/test_recommender.py` test `test_default_config_defines_balanced_adaptive_parameter_overrides` to assert the exact values copied from that candidate's printed `overrides={...}` block. For example, if the chosen output contains `"strategy": {"pick_count": 3}` and `"pullback": {"max_close_above_ma20_pct": 0.08, "max_mom20": 0.25}`, assert those literal values directly in the test.

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m unittest tests.test_recommender
```

Expected: fail because `config/default.yaml` still has old defaults.

- [ ] **Step 3: Update config defaults**

Modify `config/default.yaml` under `adaptive_strategy.parameter_overrides` with the selected candidate values.

- [ ] **Step 4: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_recommender tests.test_backtest tests.test_adaptive_parameter_optimization
```

Expected: pass.

- [ ] **Step 5: Commit defaults**

Run:

```bash
git add config/default.yaml tests/test_recommender.py
git commit -m "feat: tune adaptive parameter defaults"
```

## Task 6: Final Verification

**Files:**
- No code edits expected.

- [ ] **Step 1: Run full unit suite**

Run:

```bash
python3 -m unittest discover -s tests
```

Expected: `OK`.

- [ ] **Step 2: Run final validation windows**

Run:

```bash
python3 -m app.main backtest-adaptive --start 2024-01-01 --end 2026-03-23 --entry-price next-open --output table --no-save-report
python3 -m app.main backtest-adaptive --start 2024-01-01 --end 2024-12-31 --entry-price next-open --output table --no-save-report
python3 -m app.main backtest-adaptive --start 2025-01-01 --end 2025-12-31 --entry-price next-open --output table --no-save-report
python3 -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --entry-price next-open --output table --no-save-report
```

Expected: collect and report trade count, 1 day net return, 3 day net return, 5 day net return, max drawdown proxy, and adaptive strategy counts for each window.

- [ ] **Step 3: Clean temporary artifacts**

Run:

```bash
git status --short
```

Expected: no unexpected report files or temporary configs. Clean only artifacts created during this task.

- [ ] **Step 4: Final response**

Report:

- Whether a qualifying candidate was found.
- The exact config changes made, if any.
- Full-window and robustness-window metrics.
- Full test-suite result.
- Residual caveat that backtests do not guarantee real-money profit.
