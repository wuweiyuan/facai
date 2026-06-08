from __future__ import annotations

import argparse
import sys
from datetime import date
from itertools import islice
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
