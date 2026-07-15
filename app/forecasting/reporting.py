from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.forecasting.models import ForecastBatch


def forecast_rows(batch: ForecastBatch) -> list[dict]:
    rows: list[dict] = []
    for rank, item in enumerate(batch.items, start=1):
        rows.append(
            {
                "rank": rank,
                "symbol": item.symbol,
                "name": item.name,
                "signal_date": item.signal_date.isoformat(),
                "last_bar_date": item.last_bar_date.isoformat(),
                "sample_count": item.sample_count,
                "expected_return_5d": item.forecast_5d.expected_return,
                "probability_up_5d": item.forecast_5d.probability_up,
                "train_window_5d": item.forecast_5d.train_window,
                "ridge_alpha_5d": item.forecast_5d.alpha,
                "validation_mae_5d": item.forecast_5d.validation_mae,
                "validation_direction_accuracy_5d": item.forecast_5d.validation_direction_accuracy,
                "expected_return_10d": item.forecast_10d.expected_return,
                "probability_up_10d": item.forecast_10d.probability_up,
                "train_window_10d": item.forecast_10d.train_window,
                "ridge_alpha_10d": item.forecast_10d.alpha,
                "validation_mae_10d": item.forecast_10d.validation_mae,
                "validation_direction_accuracy_10d": item.forecast_10d.validation_direction_accuracy,
            }
        )
    return rows


def write_forecast_csv(batch: ForecastBatch, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(forecast_rows(batch)).to_csv(target, index=False)
    return target


def batch_to_dict(batch: ForecastBatch, limit: int | None = None) -> dict:
    rows = forecast_rows(batch)
    if limit is not None:
        rows = rows[: max(limit, 0)]
    return {
        "signal_date": batch.signal_date.isoformat(),
        "source_last_bar_date": batch.source_last_bar_date.isoformat(),
        "summary": {"eligible_count": len(batch.items), "skipped": batch.skipped},
        "items": rows,
    }


def format_forecast_table(batch: ForecastBatch, limit: int) -> str:
    payload = batch_to_dict(batch, limit)
    lines = [
        f"[逐股预测] 信号日={payload['signal_date']} 合格={payload['summary']['eligible_count']} "
        f"跳过={payload['summary']['skipped']}"
    ]
    for item in payload["items"]:
        lines.append(
            f"{item['rank']:>3} {item['symbol']} {item['name']} "
            f"5日={item['expected_return_5d']:.2%} 概率={item['probability_up_5d']:.2%} "
            f"10日={item['expected_return_10d']:.2%} "
            f"5日模型=Ridge(w={item['train_window_5d']}, α={item['ridge_alpha_5d']:g})"
        )
    return "\n".join(lines)
