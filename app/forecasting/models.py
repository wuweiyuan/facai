from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np


@dataclass(frozen=True)
class RidgeModel:
    mean_: np.ndarray
    scale_: np.ndarray
    coef_: np.ndarray
    intercept_: float


@dataclass(frozen=True)
class HorizonForecast:
    horizon: int
    expected_return: float
    probability_up: float
    train_window: int
    alpha: float
    validation_mae: float
    validation_direction_accuracy: float
    validation_residuals: tuple[float, ...]


@dataclass(frozen=True)
class StockForecast:
    symbol: str
    name: str
    signal_date: date
    last_bar_date: date
    sample_count: int
    forecast_5d: HorizonForecast
    forecast_10d: HorizonForecast


@dataclass(frozen=True)
class ForecastBatch:
    signal_date: date
    source_last_bar_date: date
    items: tuple[StockForecast, ...]
    skipped: dict[str, int]
