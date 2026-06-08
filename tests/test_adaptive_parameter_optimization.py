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
                or item["bull"]["recommend-pullback"]["risk_filter"]["pullback"].get(
                    "max_close_above_ma20_pct", 0
                )
                > 0.07
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
