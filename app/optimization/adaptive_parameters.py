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
            merged = merge_config(
                {"adaptive_strategy": {"parameter_overrides": pullback}},
                {"adaptive_strategy": {"parameter_overrides": oversold}},
            )
            yield merged["adaptive_strategy"]["parameter_overrides"]


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
