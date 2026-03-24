from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import re

import pandas as pd

from app.strategy.regime_risk import detect_market_state


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
    "exit_plan",
]

STRATEGY_SPECS = {
    "default": {"label": "默认战法"},
    "pullback": {"label": "回头战法"},
    "oversold": {"label": "超跌反弹"},
    "adaptive": {"label": "自适应策略"},
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
    cfg: dict | None = None,
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
    adaptive_data = _build_adaptive_strategy_data(
        default_data=default_data,
        pullback_data=pullback_data,
        oversold_data=oversold_data,
        cfg=cfg,
    )
    all_dates = sorted(
        set(adaptive_data["available_dates"]),
        reverse=True,
    )
    return {
        "generated_at": generated_at,
        "strategies": {
            "adaptive": adaptive_data,
        },
        "all_dates": all_dates,
    }


def export_dashboard_data(
    default_csv: str | Path,
    pullback_csv: str | Path,
    oversold_csv: str | Path | None,
    output_path: str | Path,
    cfg: dict | None = None,
) -> Path:
    payload = build_dashboard_payload(default_csv, pullback_csv, oversold_csv, cfg)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = "window.STOCK_DASHBOARD_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _build_adaptive_strategy_data(default_data: dict, pullback_data: dict, oversold_data: dict, cfg: dict | None) -> dict:
    data_by_key = {
        "recommend": default_data,
        "recommend-pullback": pullback_data,
        "recommend-oversold": oversold_data,
    }
    adaptive_run_summaries = _load_adaptive_run_summaries(cfg)
    adaptive_dates = sorted(
        set(adaptive_run_summaries.keys())
        | set(default_data.get("available_dates", []))
        | set(pullback_data.get("available_dates", []))
        | set(oversold_data.get("available_dates", [])),
        reverse=True,
    )
    order_map = _resolve_adaptive_regime_orders(cfg)
    market_labels = _resolve_market_labels(available_dates=adaptive_dates, cfg=cfg)

    adaptive_records: list[dict] = []
    latest_run_time: str | None = max(
        [value for value in [default_data.get("latest_run_time"), pullback_data.get("latest_run_time"), oversold_data.get("latest_run_time")] if value],
        default=None,
    )
    date_summaries: dict[str, dict] = dict(adaptive_run_summaries)

    records_by_strategy_and_date = {
        "recommend": _group_records_by_date(default_data.get("records", [])),
        "recommend-pullback": _group_records_by_date(pullback_data.get("records", [])),
        "recommend-oversold": _group_records_by_date(oversold_data.get("records", [])),
    }

    for trade_date in adaptive_dates:
        market_label = market_labels[trade_date]
        saved_summary = date_summaries.get(trade_date)
        ordered_cmds = order_map.get(market_label) or order_map.get("unknown") or ["recommend-pullback"]
        if saved_summary and saved_summary.get("tried_strategies"):
            ordered_cmds = saved_summary["tried_strategies"]
        chosen_records: list[dict] = []
        chosen_cmd: str | None = None
        if saved_summary:
            chosen_cmd = saved_summary.get("chosen_strategy")
            if chosen_cmd:
                chosen_records = records_by_strategy_and_date.get(chosen_cmd, {}).get(trade_date, [])
                if not saved_summary.get("has_recommendations", False):
                    chosen_records = []
            else:
                chosen_records = []
            saved_summary.setdefault("market_state", market_label)
            saved_summary.setdefault("tried_strategies", ordered_cmds)
        else:
            for cmd_name in ordered_cmds:
                chosen_records = records_by_strategy_and_date.get(cmd_name, {}).get(trade_date, [])
                if chosen_records:
                    chosen_cmd = cmd_name
                    break
            date_summaries[trade_date] = {
                "market_state": market_label,
                "tried_strategies": ordered_cmds,
                "chosen_strategy": chosen_cmd,
                "has_recommendations": bool(chosen_records),
            }
        for record in chosen_records:
            enriched = dict(record)
            enriched["source_strategy"] = chosen_cmd
            adaptive_records.append(enriched)

    return {
        "key": "adaptive",
        "label": STRATEGY_SPECS["adaptive"]["label"],
        "source_file": "adaptive://derived",
        "available_dates": adaptive_dates,
        "latest_run_time": latest_run_time,
        "records": adaptive_records,
        "date_summaries": date_summaries,
    }


def _group_records_by_date(records: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for record in records:
        trade_date = record.get("trade_date")
        if not trade_date:
            continue
        out.setdefault(trade_date, []).append(record)
    return out


def _resolve_adaptive_regime_orders(cfg: dict | None) -> dict[str, list[str]]:
    defaults = {
        "bull": ["recommend-pullback", "recommend"],
        "neutral": ["recommend-pullback", "recommend"],
        "bear": ["recommend-oversold"],
        "unknown": ["recommend-pullback"],
    }
    if not cfg:
        return defaults
    adaptive_cfg = cfg.get("adaptive_strategy", {})
    regime_orders = adaptive_cfg.get("regime_orders", {})
    merged = dict(defaults)
    for key, value in regime_orders.items():
        if isinstance(value, list) and value:
            merged[str(key)] = [str(item) for item in value]
    return merged


def _resolve_market_labels(available_dates: list[str], cfg: dict | None) -> dict[str, str]:
    labels = {trade_date: "unknown" for trade_date in available_dates}
    if not cfg or not available_dates:
        return labels
    cache_dir = Path(str(cfg.get("data_source", {}).get("cache_dir", ".cache/akshare")))
    index_symbol = str(cfg.get("market_filter", {}).get("index_symbol", "000300"))
    index_path = cache_dir / "index" / f"{index_symbol}.csv"
    if not index_path.exists():
        return labels
    frame = pd.read_csv(index_path)
    if frame.empty or "trade_date" not in frame.columns or "close" not in frame.columns:
        return labels
    closes = {}
    for _, row in frame.iterrows():
        try:
            closes[str(row["trade_date"]).strip()] = float(row["close"])
        except Exception:
            continue
    if not closes:
        return labels
    normalized_closes = {}
    for raw_date, close in closes.items():
        try:
            normalized_closes[pd.to_datetime(raw_date).date()] = close
        except Exception:
            continue
    for trade_date in available_dates:
        try:
            dt = pd.to_datetime(trade_date).date()
        except Exception:
            continue
        labels[trade_date] = detect_market_state(normalized_closes, dt, cfg).label
    return labels


def _load_adaptive_run_summaries(cfg: dict | None) -> dict[str, dict]:
    if not cfg:
        return {}
    reporting_cfg = cfg.get("reporting", {})
    raw_path = reporting_cfg.get("adaptive_run_csv")
    if not raw_path:
        return {}
    path = Path(str(raw_path))
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception:
        return {}
    required = {"target_date", "market_state", "tried_strategies", "chosen_strategy", "has_recommendations", "run_time"}
    if df.empty or not required.issubset(df.columns):
        return {}
    df = df.fillna("")
    df = df.sort_values(["run_time", "target_date"])
    df = df.drop_duplicates(subset=["target_date"], keep="last")
    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        target_date = _clean_text(row.get("target_date", ""))
        if not target_date:
            continue
        tried = [item.strip() for item in _clean_text(row.get("tried_strategies", "")).split(",") if item.strip()]
        out[target_date] = {
            "market_state": _clean_text(row.get("market_state", "")),
            "tried_strategies": tried,
            "chosen_strategy": _clean_text(row.get("chosen_strategy", "")) or None,
            "has_recommendations": _clean_text(row.get("has_recommendations", "")).lower() == "true",
            "chosen_count": _to_int(row.get("chosen_count", "")),
        }
    return out


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
        "exit_plan": _clean_text(row.get("exit_plan", "")),
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
