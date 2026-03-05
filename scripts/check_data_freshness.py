#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_config
from app.data_source.akshare_client import AkshareDataSource
from app.error_messages import friendly_error_message
from app.network import clear_proxy_env, disable_requests_env_proxy, force_no_proxy_all


def _parse_date(v: str | None) -> date:
    if not v:
        return date.today()
    return datetime.strptime(v, "%Y-%m-%d").date()


def _resolve_expected_data_date(ds: AkshareDataSource, target_date: date) -> date:
    start = target_date - timedelta(days=30)
    dates = ds.get_trade_dates(start, target_date)
    if not dates:
        raise RuntimeError("交易日不足，无法计算应更新交易日")
    return dates[-1]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Check if latest A-share daily data is updated")
    p.add_argument("--config", default="config/default.yaml", help="Path to YAML config")
    p.add_argument("--date", default=None, help="Target date YYYY-MM-DD, default today")
    p.add_argument("--probe-symbol", action="append", dest="probe_symbols", help="Probe symbol, can pass multiple times")
    p.add_argument("--lookback-days", type=int, default=None, help="Probe lookback window, default from config")
    p.add_argument("--require", choices=["any", "all"], default="any", help="Pass condition for multi symbols")
    p.add_argument("--output", choices=["table", "json"], default="table")
    return p


def main() -> None:
    args = build_parser().parse_args()
    cfg = load_config(args.config)

    if cfg.get("network", {}).get("disable_env_proxy", True):
        clear_proxy_env()
    if cfg.get("network", {}).get("force_no_proxy_all", True):
        force_no_proxy_all()
        disable_requests_env_proxy()

    ds_cfg = cfg.get("data_source", {})
    ds = AkshareDataSource(
        request_timeout_sec=float(ds_cfg.get("request_timeout_sec", 6.0)),
        hist_retries=int(ds_cfg.get("hist_retries", 3)),
        use_spot_name_merge=bool(ds_cfg.get("use_spot_name_merge", False)),
        cache_enabled=bool(ds_cfg.get("cache_enabled", True)),
        cache_dir=str(ds_cfg.get("cache_dir", ".cache/akshare")),
    )

    target_date = _parse_date(args.date)
    expected_data_date = _resolve_expected_data_date(ds, target_date)
    freshness_cfg = cfg.get("data_freshness", {})
    probe_symbols = args.probe_symbols or [str(freshness_cfg.get("probe_symbol", "000001"))]
    lookback_days = int(
        args.lookback_days if args.lookback_days is not None else freshness_cfg.get("probe_lookback_days", 10)
    )
    start = expected_data_date - timedelta(days=max(lookback_days, 3))

    checks: list[dict] = []
    for symbol in probe_symbols:
        item = {"symbol": symbol, "updated": False, "last_date": None, "rows": 0, "error": None}
        try:
            bars = ds.get_daily_bars(symbol, start, expected_data_date)
            item["rows"] = len(bars)
            if bars:
                last_date = max(b.trade_date for b in bars)
                item["last_date"] = last_date.isoformat()
                item["updated"] = last_date >= expected_data_date
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {friendly_error_message(exc)}"
        checks.append(item)

    if args.require == "all":
        is_updated = all(c["updated"] for c in checks)
    else:
        is_updated = any(c["updated"] for c in checks)

    payload = {
        "target_date": target_date.isoformat(),
        "expected_data_date": expected_data_date.isoformat(),
        "lookback_days": lookback_days,
        "require": args.require,
        "updated": is_updated,
        "checks": checks,
    }

    if args.output == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        status = "已更新" if is_updated else "未更新"
        print(f"目标日: {payload['target_date']}")
        print(f"应更新交易日: {payload['expected_data_date']}")
        print(f"检查规则: {payload['require']}")
        print(f"结果: {status}")
        for c in checks:
            line = f"- {c['symbol']} updated={c['updated']} last_date={c['last_date']} rows={c['rows']}"
            if c["error"]:
                line += f" error={c['error']}"
            print(line)

    if not is_updated:
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"错误: {friendly_error_message(exc)}", file=sys.stderr)
        sys.exit(1)
