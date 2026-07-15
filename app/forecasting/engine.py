from __future__ import annotations

import csv
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd

from app.forecasting.features import (
    FEATURE_COLUMNS,
    add_forecast_features,
    build_training_frame,
    predict_ridge,
    probability_up,
    select_horizon_model,
    train_final_model,
)
from app.forecasting.models import ForecastBatch, HorizonForecast, StockForecast


_REQUIRED_COLUMNS = {"trade_date", "open", "high", "low", "close", "volume", "turnover_rate"}


class ForecastEngine:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.cache_dir = Path(str(config.get("data_source", {}).get("cache_dir", ".cache/akshare")))
        self.settings = config.get("forecasting", {})

    def rank(self, signal_date: date | None = None) -> ForecastBatch:
        bars_dir = self.cache_dir / "bars"
        if not bars_dir.is_dir():
            raise RuntimeError(f"Local bars cache directory not found: {bars_dir}")
        paths = sorted(bars_dir.glob("*.csv"))
        if not paths:
            raise RuntimeError(f"No bar files found in: {bars_dir}")
        source_last_bar_date = self._latest_bar_date(paths)
        resolved_signal_date = signal_date or source_last_bar_date
        names = self._load_names()
        skipped: Counter[str] = Counter()
        items: list[StockForecast] = []
        for path in paths:
            try:
                item = self._forecast_symbol(path, names.get(path.stem, path.stem), resolved_signal_date)
            except _SkipStock as exc:
                skipped[exc.reason] += 1
            except Exception:
                skipped["model_error"] += 1
            else:
                items.append(item)
        items.sort(
            key=lambda item: (
                -item.forecast_5d.expected_return,
                -item.forecast_5d.probability_up,
                item.forecast_5d.validation_mae,
                item.symbol,
            )
        )
        return ForecastBatch(
            signal_date=resolved_signal_date,
            source_last_bar_date=source_last_bar_date,
            items=tuple(items),
            skipped=dict(sorted(skipped.items())),
        )

    def _latest_bar_date(self, paths: list[Path]) -> date:
        latest: date | None = None
        for path in paths:
            try:
                frame = pd.read_csv(path, usecols=["trade_date"])
                dates = pd.to_datetime(frame["trade_date"], errors="raise").dt.date
                if not dates.empty:
                    candidate = dates.max()
                    latest = candidate if latest is None or candidate > latest else latest
            except Exception:
                continue
        if latest is None:
            raise RuntimeError("No readable trade dates in local bar cache")
        return latest

    def _load_names(self) -> dict[str, str]:
        path = self.cache_dir / "meta" / "stock_list.csv"
        if not path.is_file():
            return {}
        with path.open("r", encoding="utf-8", newline="") as handle:
            return {
                str(row.get("symbol", "")).zfill(6): str(row.get("name", ""))
                for row in csv.DictReader(handle)
                if row.get("symbol")
            }

    def _forecast_symbol(self, path: Path, name: str, signal_date: date) -> StockForecast:
        try:
            bars = pd.read_csv(path)
        except Exception as exc:
            raise _SkipStock("read_error") from exc
        if not _REQUIRED_COLUMNS.issubset(bars.columns):
            raise _SkipStock("missing_columns")
        try:
            bars["trade_date"] = pd.to_datetime(bars["trade_date"], errors="raise").dt.date
        except Exception as exc:
            raise _SkipStock("invalid_dates") from exc
        if bars["trade_date"].duplicated().any() or not bars["trade_date"].is_monotonic_increasing:
            raise _SkipStock("invalid_dates")
        bars = bars[bars["trade_date"] <= signal_date].copy()
        if len(bars) < int(self.settings.get("min_history_bars", 252)):
            raise _SkipStock("insufficient_history")
        if bars.empty or bars.iloc[-1]["trade_date"] != signal_date:
            raise _SkipStock("stale_bar")
        model_5d = self._fit_horizon(bars, 5)
        model_10d = self._fit_horizon(bars, 10)
        return StockForecast(
            symbol=path.stem,
            name=name,
            signal_date=signal_date,
            last_bar_date=bars.iloc[-1]["trade_date"],
            sample_count=len(bars),
            forecast_5d=model_5d,
            forecast_10d=model_10d,
        )

    def _fit_horizon(self, bars: pd.DataFrame, horizon: int) -> HorizonForecast:
        train_frame = build_training_frame(bars, horizon)
        validation_samples = int(self.settings.get("validation_samples", 60))
        min_train_samples = int(self.settings.get("min_train_samples", 120))
        if len(train_frame) < min_train_samples + validation_samples:
            raise _SkipStock("insufficient_training_samples")
        selected = select_horizon_model(
            train_frame,
            horizon=horizon,
            candidate_windows=tuple(int(value) for value in self.settings.get("candidate_train_windows", [120, 180, 240])),
            candidate_alphas=tuple(float(value) for value in self.settings.get("ridge_alphas", [0.1, 1.0, 10.0])),
            validation_samples=validation_samples,
        )
        model, _ = train_final_model(train_frame, selected.train_window, selected.alpha)
        latest_features = add_forecast_features(bars).dropna(subset=list(FEATURE_COLUMNS)).tail(1)
        if latest_features.empty:
            raise _SkipStock("insufficient_training_samples")
        prediction = float(predict_ridge(model, latest_features.loc[:, FEATURE_COLUMNS].to_numpy(dtype=float))[0])
        return replace(
            selected,
            expected_return=prediction,
            probability_up=probability_up(prediction, selected.validation_residuals),
        )


class _SkipStock(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
