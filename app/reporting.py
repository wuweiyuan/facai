from __future__ import annotations

from datetime import datetime
from pathlib import Path
import unicodedata

import pandas as pd

from app.models import RecommendationResult


def _build_row(rec: RecommendationResult) -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = {
        "run_time": now,
        "trade_date": rec.trade_date.isoformat(),
        "symbol": rec.symbol,
        "name": rec.name,
        "threshold_mode": rec.threshold_mode,
        "score_total": round(rec.score_total, 2),
        "close": round(float(rec.key_metrics.get("close", 0.0)), 4),
        "stop_loss_price": round(float(rec.key_metrics.get("stop_loss_price", 0.0)), 4),
        "take_profit_price": round(float(rec.key_metrics.get("take_profit_price", 0.0)), 4),
        "suggested_holding_days": int(float(rec.key_metrics.get("suggested_holding_days", 0.0))),
    }
    return row


def append_recommendation_csv(rec: RecommendationResult, path: str) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    row = _build_row(rec)
    if out_path.exists():
        try:
            old = pd.read_csv(out_path, dtype=str)
            df = pd.concat([old, pd.DataFrame([row])], ignore_index=True)
        except Exception:
            df = pd.DataFrame([row])
    else:
        df = pd.DataFrame([row])
    df.to_csv(out_path, index=False)
    return out_path


def append_recommendation_md(rec: RecommendationResult, path: str) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    row = _build_row(rec)
    csv_path = out_path.with_suffix(".csv")
    if csv_path.exists():
        try:
            df = pd.read_csv(csv_path, dtype=str)
        except Exception:
            df = pd.DataFrame([row])
    else:
        df = pd.DataFrame([row])

    cols = [
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
    header_labels_cn = {
        "run_time": "运行时间",
        "trade_date": "交易日",
        "symbol": "代码",
        "name": "名称",
        "threshold_mode": "模式",
        "score_total": "总分",
        "close": "收盘价",
        "stop_loss_price": "止损价",
        "take_profit_price": "止盈价",
        "suggested_holding_days": "建议持股天数",
    }
    header_labels_en = {
        "run_time": "run_time",
        "trade_date": "trade_date",
        "symbol": "symbol",
        "name": "name",
        "threshold_mode": "threshold_mode",
        "score_total": "score_total",
        "close": "close",
        "stop_loss_price": "stop_loss_price",
        "take_profit_price": "take_profit_price",
        "suggested_holding_days": "suggested_holding_days",
    }
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols].fillna("")
    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].map(lambda x: str(x).split(".")[0].zfill(6))

    display_cols = [header_labels_cn[c] for c in cols]
    header = "| " + " | ".join(display_cols) + " |\n"
    sep = "|---|---|---|---|---|---:|---:|---:|---:|---:|\n"
    lines = []
    for _, r in df.iterrows():
        vals = [str(r[c]) for c in cols]
        vals = [v.replace("|", "\\|") for v in vals]
        lines.append("| " + " | ".join(vals) + " |\n")
    content = (
        "# Daily Recommendations\n\n"
        "字段说明(中文): 运行时间 | 交易日 | 股票代码 | 股票名称 | 筛选模式 | 总分 | 收盘价 | 止损价 | 止盈价 | 建议持股天数\n"
        "Field Mapping(English): run_time | trade_date | symbol | name | threshold_mode | score_total | close | stop_loss_price | take_profit_price | suggested_holding_days\n\n"
        + header
        + sep
        + "".join(lines)
    )
    out_path.write_text(content, encoding="utf-8")
    return out_path


def append_recommendation_txt(rec: RecommendationResult, path: str) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = out_path.with_suffix(".csv")
    if csv_path.exists():
        try:
            df = pd.read_csv(csv_path, dtype=str)
        except Exception:
            df = pd.DataFrame([_build_row(rec)])
    else:
        df = pd.DataFrame([_build_row(rec)])

    cols = [
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
    header_labels_cn = {
        "run_time": "运行时间",
        "trade_date": "交易日",
        "symbol": "代码",
        "name": "名称",
        "threshold_mode": "模式",
        "score_total": "总分",
        "close": "收盘价",
        "stop_loss_price": "止损价",
        "take_profit_price": "止盈价",
        "suggested_holding_days": "建议持股天数",
    }
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols].fillna("")
    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].map(lambda x: str(x).split(".")[0].zfill(6))

    widths = {c: _display_width(header_labels_cn[c]) for c in cols}
    for _, r in df.iterrows():
        for c in cols:
            widths[c] = max(widths[c], _display_width(str(r[c])))

    def fmt_row(values: list[str]) -> str:
        out = []
        for c, v in zip(cols, values):
            pad = widths[c] - _display_width(v)
            out.append(v + (" " * max(pad, 0)))
        return " | ".join(out)

    header = fmt_row([header_labels_cn[c] for c in cols])
    sep = "-+-".join("-" * widths[c] for c in cols)
    rows = [fmt_row([str(r[c]) for c in cols]) for _, r in df.iterrows()]
    content = (
        "Daily Recommendations\n"
        "字段说明(中文): 运行时间 | 交易日 | 股票代码 | 股票名称 | 筛选模式 | 总分 | 收盘价 | 止损价 | 止盈价 | 建议持股天数\n"
        "Field Mapping(English): run_time | trade_date | symbol | name | threshold_mode | score_total | close | stop_loss_price | take_profit_price | suggested_holding_days\n\n"
        + header
        + "\n"
        + sep
        + "\n"
        + "\n".join(rows)
        + "\n"
    )
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _display_width(s: str) -> int:
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1
    return w
