from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backtest.local_adaptive import run_local_adaptive_backtest
from app.config import load_config


KEYS = [
    "total_trades",
    "skipped_days",
    "win_rate_net_1d",
    "win_rate_net_3d",
    "avg_return_1d_net",
    "avg_return_3d_net",
    "avg_return_5d_net",
    "max_drawdown_proxy",
]


def _parse_date(raw: str):
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.4f}"
    if v is None:
        return "-"
    return str(v)


def _build_delta(base: dict, cand: dict) -> dict:
    delta = {}
    for key in KEYS:
        bv = base.get(key)
        cv = cand.get(key)
        if isinstance(bv, (int, float)) and isinstance(cv, (int, float)):
            delta[key] = round(cv - bv, 6)
    base_counts = base.get("adaptive_strategy_counts", {}) or {}
    cand_counts = cand.get("adaptive_strategy_counts", {}) or {}
    count_delta = {}
    for key in sorted(set(base_counts) | set(cand_counts)):
        count_delta[key] = int(cand_counts.get(key, 0)) - int(base_counts.get(key, 0))
    delta["adaptive_strategy_counts"] = count_delta
    return delta


def _render_table(base_label: str, cand_label: str, base: dict, cand: dict, delta: dict) -> str:
    lines = []
    lines.append(f"Period: {base['period']}")
    lines.append("")
    lines.append(f"{'metric':<24} {base_label:>12} {cand_label:>12} {'delta':>12}")
    for key in KEYS:
        lines.append(
            f"{key:<24} "
            f"{_fmt(base.get(key)):>12} "
            f"{_fmt(cand.get(key)):>12} "
            f"{_fmt(delta.get(key)):>12}"
        )
    lines.append("")
    lines.append(f"{'adaptive_strategy_counts':<24} {str(base.get('adaptive_strategy_counts', {})):>12}")
    lines.append(f"{'':<24} {str(cand.get('adaptive_strategy_counts', {})):>12}")
    lines.append(f"{'':<24} {str(delta.get('adaptive_strategy_counts', {})):>12}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two adaptive strategy configs over the same backtest window.")
    parser.add_argument("--base-config", default="config/default.baseline.yaml", help="Baseline config path")
    parser.add_argument("--candidate-config", default="config/default.yaml", help="Candidate config path")
    parser.add_argument("--base-label", default="baseline", help="Label for baseline output")
    parser.add_argument("--candidate-label", default="current", help="Label for candidate output")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--output", choices=["table", "json"], default="table")
    parser.add_argument("--save", default=None, help="Optional path to save the comparison result")
    parser.add_argument("--no-save-report", action="store_true", help="Do not auto-save comparison reports")
    args = parser.parse_args()

    start_date = _parse_date(args.start)
    end_date = _parse_date(args.end)
    base_summary = run_local_adaptive_backtest(load_config(args.base_config), start_date, end_date)
    cand_summary = run_local_adaptive_backtest(load_config(args.candidate_config), start_date, end_date)
    delta = _build_delta(base_summary, cand_summary)

    payload = {
        "period": base_summary["period"],
        args.base_label: {k: base_summary.get(k) for k in KEYS + ["adaptive_strategy_counts"]},
        args.candidate_label: {k: cand_summary.get(k) for k in KEYS + ["adaptive_strategy_counts"]},
        "delta": delta,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) if args.output == "json" else _render_table(
        args.base_label,
        args.candidate_label,
        base_summary,
        cand_summary,
        delta,
    )
    print(rendered)
    save_targets: list[Path] = []
    if args.save:
        save_targets.append(Path(args.save))
    elif not args.no_save_report:
        ext = "json" if args.output == "json" else "txt"
        period_key = f"{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}"
        base_dir = Path("reports/backtests/adaptive_compare")
        save_targets.append(base_dir / f"{period_key}.{ext}")
        save_targets.append(base_dir / f"latest.{ext}")

    for save_path in save_targets:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(rendered + ("\n" if not rendered.endswith("\n") else ""), encoding="utf-8")
        print(f"Saved report: {save_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
