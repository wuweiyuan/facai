from contextlib import redirect_stdout
from datetime import date
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from app.backtest.entry_price import ENTRY_PRICE_NEXT_OPEN
from app.config import apply_adaptive_parameter_overrides
from app.optimization.adaptive_parameters import (
    CandidateResult,
    apply_parameter_overrides,
    combine_override_candidates,
    generate_pullback_override_candidates,
    generate_oversold_override_candidates,
    is_primary_acceptance,
    run_baseline,
    run_candidate,
    score_candidate,
)


class AdaptiveParameterOptimizationTest(unittest.TestCase):
    def test_generate_pullback_candidates_includes_explicit_grid_dimensions_and_more_active_options(self):
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
                                "max_mom5": 0.10,
                                "max_rsi14": 78.0,
                                "max_volume_zscore20": 2.2,
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
                or item["bull"]["recommend-pullback"]["risk_filter"]["pullback"].get(
                    "max_close_above_ma20_pct", 0
                )
                > 0.07
                for item in candidates
            )
        )

    def test_generated_pullback_candidate_resolves_all_grid_values_without_inheriting_base_defaults(self):
        candidate = next(generate_pullback_override_candidates())
        candidate_pullback = candidate["bull"]["recommend-pullback"]["risk_filter"]["pullback"]
        base_cfg = {
            "risk_filter": {
                "pullback": {
                    "max_close_above_ma20_pct": 0.01,
                    "max_mom20": 0.01,
                    "max_mom5": 0.01,
                    "max_rsi14": 1.0,
                    "max_volume_zscore20": 0.1,
                }
            },
            "adaptive_strategy": {"strategy_pick_counts": {"recommend-pullback": 1}},
        }

        optimized_cfg = apply_parameter_overrides(base_cfg, candidate)
        resolved = apply_adaptive_parameter_overrides(optimized_cfg, "bull", "recommend-pullback")

        self.assertEqual(resolved["risk_filter"]["pullback"], candidate_pullback)

    def test_combine_override_candidates_preserves_bull_and_bear_branches(self):
        combined = next(
            combine_override_candidates(
                generate_pullback_override_candidates(),
                generate_oversold_override_candidates(),
            )
        )

        self.assertIn("bull", combined)
        self.assertIn("bear", combined)
        self.assertIn("recommend-pullback", combined["bull"])
        self.assertIn("recommend-oversold", combined["bear"])

    def test_apply_parameter_overrides_returns_new_cfg_without_mutating_base(self):
        base_cfg = {
            "adaptive_strategy": {"strategy_pick_counts": {"recommend-pullback": 1}},
            "risk_filter": {"pullback": {"max_mom20": 0.01}},
        }
        original = {
            "adaptive_strategy": {"strategy_pick_counts": {"recommend-pullback": 1}},
            "risk_filter": {"pullback": {"max_mom20": 0.01}},
        }
        overrides = next(generate_pullback_override_candidates())

        merged = apply_parameter_overrides(base_cfg, overrides)

        self.assertIsNot(merged, base_cfg)
        self.assertEqual(base_cfg, original)
        self.assertIn("parameter_overrides", merged["adaptive_strategy"])
        self.assertNotIn("parameter_overrides", base_cfg["adaptive_strategy"])

    def test_run_candidate_uses_next_open_entry_price_and_merged_cfg(self):
        base_cfg = {"adaptive_strategy": {"strategy_pick_counts": {"recommend-pullback": 1}}}
        overrides = next(generate_pullback_override_candidates())
        expected_cfg = apply_parameter_overrides(base_cfg, overrides)
        summary = {
            "total_trades": 3,
            "avg_return_1d_net": 0.01,
            "avg_return_3d_net": 0.02,
            "avg_return_5d_net": 0.03,
            "max_drawdown_proxy": 0.04,
            "adaptive_strategy_counts": {"recommend-pullback": 3},
        }

        with patch(
            "app.optimization.adaptive_parameters.run_local_adaptive_backtest",
            return_value=summary,
        ) as backtest:
            result = run_candidate(base_cfg, "candidate", overrides, date(2026, 1, 1), date(2026, 1, 31))

        backtest.assert_called_once_with(
            expected_cfg,
            date(2026, 1, 1),
            date(2026, 1, 31),
            None,
            ENTRY_PRICE_NEXT_OPEN,
        )
        self.assertEqual(result.total_trades, 3)

    def test_score_candidate_rewards_balanced_improvement(self):
        baseline = CandidateResult(
            name="baseline",
            overrides={},
            total_trades=61,
            avg_return_1d_net=0.003994,
            avg_return_3d_net=0.009931,
            avg_return_5d_net=0.007431,
            max_drawdown_proxy=0.1628,
            adaptive_strategy_counts={},
        )
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

        score = score_candidate(result, baseline)

        self.assertGreater(score, 0)

    def test_score_candidate_penalizes_drawdown_above_tolerance(self):
        baseline = CandidateResult(
            name="baseline",
            overrides={},
            total_trades=61,
            avg_return_1d_net=0.003994,
            avg_return_3d_net=0.009931,
            avg_return_5d_net=0.007431,
            max_drawdown_proxy=0.1628,
            adaptive_strategy_counts={},
        )
        good = CandidateResult(
            name="good",
            overrides={},
            total_trades=80,
            avg_return_1d_net=0.0045,
            avg_return_3d_net=0.012,
            avg_return_5d_net=0.009,
            max_drawdown_proxy=baseline.max_drawdown_proxy,
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

        self.assertGreater(score_candidate(good, baseline), score_candidate(bad, baseline))

    def test_score_candidate_is_negative_for_bad_returns_or_low_quality_candidate(self):
        baseline = CandidateResult(
            name="baseline",
            overrides={},
            total_trades=61,
            avg_return_1d_net=0.003994,
            avg_return_3d_net=0.009931,
            avg_return_5d_net=0.007431,
            max_drawdown_proxy=0.1628,
            adaptive_strategy_counts={},
        )
        result = CandidateResult(
            name="weak",
            overrides={},
            total_trades=40,
            avg_return_1d_net=-0.002,
            avg_return_3d_net=-0.003,
            avg_return_5d_net=-0.004,
            max_drawdown_proxy=0.20,
            adaptive_strategy_counts={},
        )

        self.assertLess(score_candidate(result, baseline), 0)

    def test_score_candidate_requires_explicit_baseline_and_uses_it(self):
        result = CandidateResult(
            name="candidate",
            overrides={},
            total_trades=12,
            avg_return_1d_net=0.02,
            avg_return_3d_net=0.03,
            avg_return_5d_net=0.04,
            max_drawdown_proxy=0.10,
            adaptive_strategy_counts={},
        )
        easy_baseline = CandidateResult(
            name="easy",
            overrides={},
            total_trades=1,
            avg_return_1d_net=0.0,
            avg_return_3d_net=0.0,
            avg_return_5d_net=0.0,
            max_drawdown_proxy=0.20,
            adaptive_strategy_counts={},
        )
        hard_baseline = CandidateResult(
            name="hard",
            overrides={},
            total_trades=100,
            avg_return_1d_net=0.05,
            avg_return_3d_net=0.06,
            avg_return_5d_net=0.07,
            max_drawdown_proxy=0.08,
            adaptive_strategy_counts={},
        )

        with self.assertRaises(TypeError):
            score_candidate(result)

        self.assertGreater(score_candidate(result, easy_baseline), 0)
        self.assertLess(score_candidate(result, hard_baseline), 0)

    def test_primary_acceptance_requires_explicit_baseline_and_uses_it(self):
        result = CandidateResult(
            name="candidate",
            overrides={},
            total_trades=12,
            avg_return_1d_net=0.02,
            avg_return_3d_net=0.03,
            avg_return_5d_net=0.04,
            max_drawdown_proxy=0.10,
            adaptive_strategy_counts={},
        )
        easy_baseline = CandidateResult(
            name="easy",
            overrides={},
            total_trades=1,
            avg_return_1d_net=0.0,
            avg_return_3d_net=0.0,
            avg_return_5d_net=0.0,
            max_drawdown_proxy=0.20,
            adaptive_strategy_counts={},
        )
        hard_baseline = CandidateResult(
            name="hard",
            overrides={},
            total_trades=100,
            avg_return_1d_net=0.05,
            avg_return_3d_net=0.06,
            avg_return_5d_net=0.07,
            max_drawdown_proxy=0.08,
            adaptive_strategy_counts={},
        )

        with self.assertRaises(TypeError):
            is_primary_acceptance(result)

        self.assertTrue(is_primary_acceptance(result, easy_baseline))
        self.assertFalse(is_primary_acceptance(result, hard_baseline))

    def test_run_baseline_uses_next_open_entry_price_and_current_cfg_overrides(self):
        current_overrides = {
            "bull": {
                "recommend-pullback": {
                    "strategy": {"pick_count": 3},
                }
            }
        }
        base_cfg = {"adaptive_strategy": {"parameter_overrides": current_overrides}}
        summary = {
            "total_trades": 5,
            "avg_return_1d_net": 0.01,
            "avg_return_3d_net": 0.02,
            "avg_return_5d_net": 0.03,
            "max_drawdown_proxy": 0.04,
            "adaptive_strategy_counts": {"recommend-pullback": 5},
        }

        with patch(
            "app.optimization.adaptive_parameters.run_local_adaptive_backtest",
            return_value=summary,
        ) as backtest:
            result = run_baseline(base_cfg, date(2026, 1, 1), date(2026, 1, 31))

        backtest.assert_called_once_with(
            base_cfg,
            date(2026, 1, 1),
            date(2026, 1, 31),
            None,
            ENTRY_PRICE_NEXT_OPEN,
        )
        self.assertEqual(result.name, "baseline")
        self.assertEqual(result.overrides, current_overrides)
        self.assertEqual(result.total_trades, 5)

    def test_cli_default_evaluates_all_candidates_and_uses_dynamic_baseline(self):
        cli = _load_optimizer_cli()
        baseline = CandidateResult(
            name="baseline",
            overrides={},
            total_trades=10,
            avg_return_1d_net=0.01,
            avg_return_3d_net=0.02,
            avg_return_5d_net=0.03,
            max_drawdown_proxy=0.10,
            adaptive_strategy_counts={},
        )
        first = CandidateResult(
            name="candidate-001",
            overrides={"candidate": 1},
            total_trades=12,
            avg_return_1d_net=0.02,
            avg_return_3d_net=0.03,
            avg_return_5d_net=0.04,
            max_drawdown_proxy=0.09,
            adaptive_strategy_counts={"recommend-pullback": 12},
        )
        second = CandidateResult(
            name="candidate-002",
            overrides={"candidate": 2},
            total_trades=11,
            avg_return_1d_net=0.015,
            avg_return_3d_net=0.025,
            avg_return_5d_net=0.035,
            max_drawdown_proxy=0.08,
            adaptive_strategy_counts={"recommend-pullback": 11},
        )
        seen_score_baselines = []
        seen_acceptance_baselines = []

        def fake_score(result, baseline_arg):
            seen_score_baselines.append(baseline_arg)
            return 2.0 if result.name == "candidate-001" else 1.0

        def fake_acceptance(result, baseline_arg):
            seen_acceptance_baselines.append(baseline_arg)
            return result.name == "candidate-001"

        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["optimize_adaptive_parameters.py", "--top", "2"]),
            patch.object(cli, "load_config", return_value={"cfg": True}),
            patch.object(cli, "run_baseline", return_value=baseline),
            patch.object(cli, "generate_pullback_override_candidates", return_value=["pullback"]),
            patch.object(cli, "generate_oversold_override_candidates", return_value=["oversold"]),
            patch.object(cli, "combine_override_candidates", return_value=[{"candidate": 1}, {"candidate": 2}]),
            patch.object(cli, "run_candidate", side_effect=[first, second]) as run_candidate_mock,
            patch.object(cli, "score_candidate", side_effect=fake_score),
            patch.object(cli, "is_primary_acceptance", side_effect=fake_acceptance),
            redirect_stdout(out),
        ):
            cli.main()

        output = out.getvalue()
        self.assertIn("evaluated=2 total=2 truncated=False", output)
        self.assertIn("TOP\n", output)
        self.assertNotIn("TOP_EVALUATED", output)
        self.assertIn("ACCEPT candidate-001", output)
        self.assertEqual(run_candidate_mock.call_count, 2)
        self.assertTrue(seen_score_baselines)
        self.assertTrue(all(item is baseline for item in seen_score_baselines))
        self.assertTrue(seen_acceptance_baselines)
        self.assertTrue(all(item is baseline for item in seen_acceptance_baselines))

    def test_cli_explicit_limit_marks_output_as_truncated_prefix(self):
        cli = _load_optimizer_cli()
        baseline = CandidateResult(
            name="baseline",
            overrides={},
            total_trades=10,
            avg_return_1d_net=0.01,
            avg_return_3d_net=0.02,
            avg_return_5d_net=0.03,
            max_drawdown_proxy=0.10,
            adaptive_strategy_counts={},
        )
        first = CandidateResult(
            name="candidate-001",
            overrides={"candidate": 1},
            total_trades=12,
            avg_return_1d_net=0.02,
            avg_return_3d_net=0.03,
            avg_return_5d_net=0.04,
            max_drawdown_proxy=0.09,
            adaptive_strategy_counts={"recommend-pullback": 12},
        )

        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["optimize_adaptive_parameters.py", "--limit", "1", "--top", "1"]),
            patch.object(cli, "load_config", return_value={"cfg": True}),
            patch.object(cli, "run_baseline", return_value=baseline),
            patch.object(cli, "generate_pullback_override_candidates", return_value=["pullback"]),
            patch.object(cli, "generate_oversold_override_candidates", return_value=["oversold"]),
            patch.object(cli, "combine_override_candidates", return_value=[{"candidate": 1}, {"candidate": 2}]),
            patch.object(cli, "run_candidate", return_value=first) as run_candidate_mock,
            patch.object(cli, "score_candidate", return_value=1.0),
            patch.object(cli, "is_primary_acceptance", return_value=False),
            redirect_stdout(out),
        ):
            cli.main()

        output = out.getvalue()
        self.assertIn("evaluated=1 total=2 truncated=True", output)
        self.assertIn("TOP_EVALUATED", output)
        self.assertEqual(run_candidate_mock.call_count, 1)

def _load_optimizer_cli():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "optimize_adaptive_parameters.py"
    spec = importlib.util.spec_from_file_location("optimize_adaptive_parameters_test_module", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
