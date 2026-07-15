from __future__ import annotations

import numpy as np
import pandas as pd

from app.features.indicators import add_indicators
from app.forecasting.models import HorizonForecast, RidgeModel


FEATURE_COLUMNS = (
    "ret_1d",
    "mom5",
    "mom20",
    "close_vs_ma20",
    "close_vs_ma60",
    "ma20_slope5",
    "rsi14",
    "vol20_std",
    "vol_ratio_5_20",
    "volume_zscore20",
    "atr14_pct",
    "turnover_rate",
)


def add_forecast_features(bars: pd.DataFrame) -> pd.DataFrame:
    normalized = bars.sort_values("trade_date").reset_index(drop=True).copy()
    normalized["turnover_rate"] = pd.to_numeric(normalized["turnover_rate"], errors="coerce").fillna(0.0)
    indexed = add_indicators(normalized)
    indexed["close_vs_ma20"] = indexed["close"] / indexed["ma20"] - 1.0
    indexed["close_vs_ma60"] = indexed["close"] / indexed["ma60"] - 1.0
    indexed["atr14_pct"] = indexed["atr14"] / indexed["close"]
    return indexed


def build_training_frame(bars: pd.DataFrame, horizon: int) -> pd.DataFrame:
    indexed = add_forecast_features(bars)
    indexed["close_t_plus_h"] = indexed["close"].shift(-horizon)
    indexed["target_return"] = indexed["close_t_plus_h"] / indexed["close"] - 1.0
    return indexed.dropna(subset=[*FEATURE_COLUMNS, "target_return"]).reset_index(drop=True)


def fit_ridge(x: np.ndarray, y: np.ndarray, alpha: float) -> RidgeModel:
    if len(x) == 0:
        raise ValueError("cannot fit ridge with no rows")
    if alpha < 0:
        raise ValueError("alpha must be non-negative")
    mean = np.asarray(x, dtype=float).mean(axis=0)
    scale = np.asarray(x, dtype=float).std(axis=0)
    scale = np.where(scale == 0.0, 1.0, scale)
    normalized = (np.asarray(x, dtype=float) - mean) / scale
    target = np.asarray(y, dtype=float)
    intercept = float(target.mean())
    centered_target = target - intercept
    regularizer = float(alpha) * np.eye(normalized.shape[1])
    coef = np.linalg.solve(normalized.T @ normalized + regularizer, normalized.T @ centered_target)
    return RidgeModel(mean_=mean, scale_=scale, coef_=coef, intercept_=intercept)


def predict_ridge(model: RidgeModel, x: np.ndarray) -> np.ndarray:
    normalized = (np.asarray(x, dtype=float) - model.mean_) / model.scale_
    return normalized @ model.coef_ + model.intercept_


def train_final_model(frame: pd.DataFrame, train_window: int, alpha: float) -> tuple[RidgeModel, pd.Series]:
    train = frame.tail(train_window)
    if len(train) < train_window:
        raise ValueError("insufficient training samples")
    x = train.loc[:, FEATURE_COLUMNS].to_numpy(dtype=float)
    y = train["target_return"].to_numpy(dtype=float)
    return fit_ridge(x, y, alpha), train.iloc[-1]


def probability_up(prediction: float, residuals: tuple[float, ...]) -> float:
    if not residuals:
        return 0.0
    return float(sum(prediction + residual > 0.0 for residual in residuals) / len(residuals))


def select_horizon_model(
    frame: pd.DataFrame,
    horizon: int,
    candidate_windows: tuple[int, ...],
    candidate_alphas: tuple[float, ...],
    validation_samples: int,
) -> HorizonForecast:
    if validation_samples < 1:
        raise ValueError("validation_samples must be positive")
    if len(frame) <= validation_samples:
        raise ValueError("insufficient training samples")
    validation_start = len(frame) - validation_samples
    candidates: list[tuple[float, int, float, tuple[float, ...], float]] = []
    for window in sorted(set(candidate_windows)):
        if window <= 0 or validation_start < window:
            continue
        for alpha in sorted(set(candidate_alphas)):
            if alpha < 0:
                continue
            residuals: list[float] = []
            predicted: list[float] = []
            actual: list[float] = []
            for index in range(validation_start, len(frame)):
                train = frame.iloc[index - window : index]
                model = fit_ridge(
                    train.loc[:, FEATURE_COLUMNS].to_numpy(dtype=float),
                    train["target_return"].to_numpy(dtype=float),
                    alpha,
                )
                prediction = float(
                    predict_ridge(model, frame.iloc[[index]].loc[:, FEATURE_COLUMNS].to_numpy(dtype=float))[0]
                )
                realized = float(frame.iloc[index]["target_return"])
                predicted.append(prediction)
                actual.append(realized)
                residuals.append(realized - prediction)
            mae = float(np.mean(np.abs(residuals)))
            direction_accuracy = float(
                np.mean([(prediction > 0.0) == (realized > 0.0) for prediction, realized in zip(predicted, actual)])
            )
            candidates.append((mae, window, float(alpha), tuple(residuals), direction_accuracy))
    if not candidates:
        raise ValueError("insufficient training samples")
    mae, window, alpha, residuals, direction_accuracy = min(candidates, key=lambda value: value[:3])
    return HorizonForecast(
        horizon=horizon,
        expected_return=0.0,
        probability_up=0.0,
        train_window=window,
        alpha=alpha,
        validation_mae=mae,
        validation_direction_accuracy=direction_accuracy,
        validation_residuals=residuals,
    )
