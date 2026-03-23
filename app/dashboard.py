from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import re

import pandas as pd


RECOMMENDATION_COLUMNS = [
    "run_time",
    "trade_date",
    "symbol",
    "name",
    "threshold_mode",
    "score_total",
    "close",
    "stop_loss_price",
    "take_profit_price",
    "suggested_holding_days",
]

STRATEGY_SPECS = {
    "default": {"label": "默认战法"},
    "pullback": {"label": "回头战法"},
    "oversold": {"label": "超跌反弹"},
}


def normalize_symbol(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if "." in text:
        text = text.split(".", 1)[0].strip()
    digits = re.sub(r"\D", "", text)
    if digits:
        return digits[-6:].zfill(6)
    return text


def load_strategy_records(path: str | Path, strategy_key: str) -> dict:
    if strategy_key not in STRATEGY_SPECS:
        raise ValueError(f"Unsupported strategy: {strategy_key}")

    source_path = Path(path)
    records: list[dict] = []
    available_dates: list[str] = []
    latest_run_time: str | None = None

    if source_path.exists():
        df = pd.read_csv(source_path, dtype=str)
        for col in RECOMMENDATION_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        df = df[RECOMMENDATION_COLUMNS].fillna("")
        df["symbol"] = df["symbol"].map(normalize_symbol)
        df["run_time"] = df["run_time"].map(lambda v: str(v).strip())
        df["trade_date"] = df["trade_date"].map(lambda v: str(v).strip())
        df["score_total_sort"] = pd.to_numeric(df["score_total"], errors="coerce")

        # Keep the latest append for each stock on each trade date.
        df = df.sort_values(["run_time", "trade_date", "symbol"])
        df = df.drop_duplicates(subset=["trade_date", "symbol"], keep="last")
        df = df.sort_values(
            ["trade_date", "score_total_sort", "run_time", "symbol"],
            ascending=[False, False, False, True],
        )

        records = [_row_to_record(row) for _, row in df.iterrows()]
        available_dates = sorted({record["trade_date"] for record in records if record["trade_date"]}, reverse=True)
        latest_run_time = max((record["run_time"] for record in records if record["run_time"]), default=None)

    return {
        "key": strategy_key,
        "label": STRATEGY_SPECS[strategy_key]["label"],
        "source_file": source_path.as_posix(),
        "available_dates": available_dates,
        "latest_run_time": latest_run_time,
        "records": records,
    }


def build_dashboard_payload(
    default_csv: str | Path,
    pullback_csv: str | Path,
    oversold_csv: str | Path | None = None,
) -> dict:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    default_data = load_strategy_records(default_csv, "default")
    pullback_data = load_strategy_records(pullback_csv, "pullback")
    oversold_data = load_strategy_records(oversold_csv, "oversold") if oversold_csv is not None else {
        "key": "oversold",
        "label": STRATEGY_SPECS["oversold"]["label"],
        "source_file": "",
        "available_dates": [],
        "latest_run_time": None,
        "records": [],
    }
    all_dates = sorted(
        set(default_data["available_dates"]) | set(pullback_data["available_dates"]) | set(oversold_data["available_dates"]),
        reverse=True,
    )
    return {
        "generated_at": generated_at,
        "strategies": {
            "default": default_data,
            "pullback": pullback_data,
            "oversold": oversold_data,
        },
        "all_dates": all_dates,
    }


def export_dashboard_data(
    default_csv: str | Path,
    pullback_csv: str | Path,
    oversold_csv: str | Path | None,
    output_path: str | Path,
) -> Path:
    payload = build_dashboard_payload(default_csv, pullback_csv, oversold_csv)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = "window.STOCK_DASHBOARD_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _row_to_record(row: pd.Series) -> dict:
    return {
        "run_time": _clean_text(row.get("run_time", "")),
        "trade_date": _clean_text(row.get("trade_date", "")),
        "symbol": normalize_symbol(row.get("symbol", "")),
        "name": _clean_text(row.get("name", "")),
        "threshold_mode": _clean_text(row.get("threshold_mode", "")),
        "score_total": _to_float(row.get("score_total", "")),
        "close": _to_float(row.get("close", "")),
        "stop_loss_price": _to_float(row.get("stop_loss_price", "")),
        "take_profit_price": _to_float(row.get("take_profit_price", "")),
        "suggested_holding_days": _to_int(row.get("suggested_holding_days", "")),
    }


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _to_float(value: object) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return round(float(text), 4)
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None
