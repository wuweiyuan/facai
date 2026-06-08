from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from app.backtest.runner import BacktestRunner
from app.backtest.local_adaptive import run_local_adaptive_backtest
from app.backtest.local_intraday_proxy import run_local_intraday_proxy_backtest
from app.backtest.local_rule_adaptive import run_local_rule_adaptive_backtest
from app.backtest.local_single import run_local_single_backtest
from app.config import apply_adaptive_parameter_overrides, apply_strategy_profile, load_config
from app.dashboard import export_dashboard_data
from app.data_source.akshare_client import AkshareDataSource
from app.doctor import print_doctor_report, run_doctor
from app.engine.recommender import Recommender
from app.error_messages import friendly_error_message
from app.intraday_review import analyze_intraday_pick_signals
from app.models import BacktestRecord
from app.network import clear_proxy_env, disable_requests_env_proxy, force_no_proxy_all
from app.reporting import (
    append_adaptive_run_csv,
    append_intraday_pick_signals,
    append_opportunity_pool_csv,
    append_recommendation_csv,
    append_recommendation_md,
    append_recommendation_txt,
    resolve_recommendation_output_log_path,
)
from app.sector_map import summarize_sector_map


def _resolve_dashboard_export_args(base_cfg: dict) -> tuple[str, str, str, str, str]:
    default_csv = str(base_cfg.get("reporting", {}).get("recommendation_csv", "reports/recommendations.csv"))
    pullback_cfg = apply_strategy_profile(base_cfg, "pullback_confirm")
    pullback_csv = str(pullback_cfg.get("reporting", {}).get("recommendation_csv", "reports/pullback_recommendations.csv"))
    oversold_cfg = apply_strategy_profile(base_cfg, "oversold_rebound")
    oversold_csv = str(oversold_cfg.get("reporting", {}).get("recommendation_csv", "reports/oversold_recommendations.csv"))
    opportunity_csv = str(base_cfg.get("reporting", {}).get("opportunity_recommendation_csv", "reports/opportunity_recommendations.csv"))
    dashboard_js = str(base_cfg.get("reporting", {}).get("dashboard_data_js", "reports/dashboard-data.js"))
    return default_csv, pullback_csv, oversold_csv, opportunity_csv, dashboard_js


METRIC_LABELS_ZH = {
    "close": "收盘价",
    "ma20": "20日均线",
    "ma60": "60日均线",
    "mom5": "5日动量",
    "mom20": "20日动量",
    "market_mom20": "市场20日动量",
    "mom20_excess_vs_market": "相对市场20日超额动量",
    "sector_mom20": "板块20日动量",
    "mom20_excess_vs_sector": "相对板块20日超额动量",
    "rsi14": "RSI14",
    "atr14": "ATR14",
    "stop_loss_price": "止损价",
    "take_profit_price": "止盈价",
    "suggested_holding_days": "建议持股天数",
    "vol_ratio_5_20": "量比(5/20)",
    "volume_zscore20": "成交量Z分数(20)",
    "turnover_rate": "换手率",
    "vol20_std": "20日波动率",
}


class _TeeStdout:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data: str) -> int:
        for s in self._streams:
            s.write(data)
        return len(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()


def _parse_date(v: str | None) -> date:
    if not v:
        return date.today()
    return datetime.strptime(v, "%Y-%m-%d").date()


def _resolve_next_trade_date(ds, today: date | None = None) -> date:
    anchor = today or date.today()
    future_dates = ds.get_trade_dates(anchor, anchor + timedelta(days=60))
    for trade_date in future_dates:
        if trade_date > anchor:
            return trade_date
    raise RuntimeError(f"No future trade date found after {anchor.isoformat()}")


def _resolve_recommend_target_date(ds, raw_date: str | None, today: date | None = None) -> date:
    if raw_date:
        return _parse_date(raw_date)
    return _resolve_next_trade_date(ds, today=today)


def _resolve_recommend_run_specs(cmd: str) -> list[tuple[str, str | None, str]]:
    if cmd == "recommend-all":
        return [
            ("recommend", None, "默认策略 recommend"),
            ("recommend-pullback", "pullback_confirm", "回踩策略 recommend-pullback"),
            ("recommend-oversold", "oversold_rebound", "超跌反弹策略 recommend-oversold"),
        ]
    if cmd == "recommend":
        return [("recommend", None, "默认策略 recommend")]
    if cmd == "recommend-pullback":
        return [("recommend-pullback", "pullback_confirm", "回踩策略 recommend-pullback")]
    if cmd == "recommend-oversold":
        return [("recommend-oversold", "oversold_rebound", "超跌反弹策略 recommend-oversold")]
    if cmd == "recommend-bull":
        return [("recommend-bull", "bull_trend_research", "强市趋势研究策略 recommend-bull")]
    if cmd == "recommend-relative":
        return [("recommend-relative", "relative_strength", "相对强弱研究策略 recommend-relative")]
    raise RuntimeError(f"Unsupported recommend command: {cmd}")


def _resolve_adaptive_strategy_specs(base_cfg: dict, market_label: str) -> list[tuple[str, str | None, str]]:
    adaptive_cfg = base_cfg.get("adaptive_strategy", {})
    regime_orders = adaptive_cfg.get("regime_orders", {})
    profile_overrides = adaptive_cfg.get("profile_overrides", {})
    raw_order = regime_orders.get(market_label) or regime_orders.get("unknown") or ["recommend-pullback"]
    known = {
        "cash": ("cash", None, "空仓 cash"),
        "recommend": ("recommend", None, "默认策略 recommend"),
        "recommend-pullback": ("recommend-pullback", "pullback_confirm", "回踩策略 recommend-pullback"),
        "recommend-oversold": ("recommend-oversold", "oversold_rebound", "超跌反弹策略 recommend-oversold"),
        "recommend-bull": ("recommend-bull", "bull_trend_research", "强市趋势研究策略 recommend-bull"),
        "recommend-relative": ("recommend-relative", "relative_strength", "相对强弱研究策略 recommend-relative"),
    }
    out: list[tuple[str, str | None, str]] = []
    for item in raw_order:
        key = str(item).strip()
        spec = known.get(key)
        if not spec:
            continue
        profile_name = spec[1]
        if isinstance(profile_overrides, dict):
            override_name = profile_overrides.get(key)
            if override_name is not None:
                profile_name = str(override_name).strip() or None
        resolved_spec = (spec[0], profile_name, spec[2])
        if resolved_spec not in out:
            out.append(resolved_spec)
        if key == "cash":
            break
    return out or [known["recommend-pullback"]]


def _resolve_opportunity_pool_specs(base_cfg: dict, market_label: str) -> list[tuple[str, str | None, str]]:
    pool_cfg = base_cfg.get("opportunity_pool", {})
    regime_orders = pool_cfg.get("regime_orders", {})
    profile_overrides = pool_cfg.get("profile_overrides", {})
    raw_order = regime_orders.get(market_label) or regime_orders.get("unknown") or ["recommend-pullback", "recommend-oversold"]
    known = {
        "recommend": ("recommend", None, "默认策略 recommend"),
        "recommend-pullback": ("recommend-pullback", "pullback_confirm", "回踩策略 recommend-pullback"),
        "recommend-oversold": ("recommend-oversold", "oversold_rebound", "超跌反弹策略 recommend-oversold"),
        "recommend-bull": ("recommend-bull", "bull_trend_research", "强市趋势研究策略 recommend-bull"),
        "recommend-relative": ("recommend-relative", "relative_strength", "相对强弱研究策略 recommend-relative"),
    }
    out: list[tuple[str, str | None, str]] = []
    for item in raw_order:
        key = str(item).strip()
        spec = known.get(key)
        if not spec:
            continue
        profile_name = spec[1]
        if isinstance(profile_overrides, dict):
            override_name = profile_overrides.get(key)
            if override_name is not None:
                profile_name = str(override_name).strip() or None
        resolved_spec = (spec[0], profile_name, spec[2])
        if resolved_spec not in out:
            out.append(resolved_spec)
    return out or [known["recommend-pullback"], known["recommend-oversold"]]


def _resolve_opportunity_pick_count(base_cfg: dict, cmd_name: str, override_total: int | None) -> int | None:
    if override_total is None:
        counts_cfg = base_cfg.get("opportunity_pool", {}).get("strategy_pick_counts", {})
        raw = counts_cfg.get(cmd_name)
        if raw is None:
            return 2
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return 2
    return max(min(int(override_total), 10), 1)


def _build_opportunity_pool(base_cfg: dict, ds, target_date: date, total_count: int | None) -> dict:
    base_engine = Recommender(ds, base_cfg)
    signal_date = base_engine.resolve_signal_date(target_date)
    market_state, market_reason = base_engine._resolve_market_state(signal_date)
    run_specs = _resolve_opportunity_pool_specs(base_cfg, market_state.label)
    seen_symbols: set[str] = set()
    pool: list[dict] = []
    total_limit = max(int(total_count), 1) if total_count is not None else int(base_cfg.get("opportunity_pool", {}).get("max_total", 6))
    total_limit = max(total_limit, 1)

    for cmd_name, profile_name, section_title in run_specs:
        cfg = apply_strategy_profile(base_cfg, profile_name)
        engine = Recommender(ds, cfg)
        per_strategy_count = _resolve_opportunity_pick_count(base_cfg, cmd_name, None if total_count is None else total_limit)
        try:
            recs = engine.recommend_many(target_date, count=per_strategy_count)
        except RuntimeError as exc:
            if "No candidate found in enabled modes:" not in str(exc):
                raise
            continue
        for rec in recs:
            if rec.symbol in seen_symbols:
                continue
            seen_symbols.add(rec.symbol)
            item = rec.as_dict()
            item["source_strategy"] = cmd_name
            item["source_label"] = section_title
            pool.append(item)
            if len(pool) >= total_limit:
                break
        if len(pool) >= total_limit:
            break

    return {
        "target_date": target_date.isoformat(),
        "signal_date": signal_date.isoformat(),
        "market_state": market_state.label,
        "market_reason": market_reason,
        "pool": pool,
    }


def _save_opportunity_pool_csv(base_cfg: dict, payload: dict) -> str | None:
    if not bool(base_cfg.get("reporting", {}).get("enabled", True)):
        return None
    return append_opportunity_pool_csv(
        payload,
        str(base_cfg.get("reporting", {}).get("opportunity_recommendation_csv", "reports/opportunity_recommendations.csv")),
    )


def _print_opportunity_pool(payload: dict) -> None:
    print(
        f"[机会池] 目标日={payload['target_date']} 信号日={payload['signal_date']} "
        f"市场={payload['market_state']} 原因={payload['market_reason']}"
    )
    if not payload["pool"]:
        print("[机会池] 当前没有额外候选。")
        return
    print(f"[机会池] 候选数量: {len(payload['pool'])}")
    for idx, item in enumerate(payload["pool"], start=1):
        print(f"\n[{idx}] {item['symbol']} {item['name']}")
        print(f"来源策略: {item['source_label']}")
        print(f"总分: {item['score_total']:.2f}")
        print(f"收盘价: {item['key_metrics'].get('close', '-')}")
        print(f"建议持股天数: {item['key_metrics'].get('suggested_holding_days', '-')}")
        exit_plan = item["key_metrics"].get("exit_plan", "")
        if exit_plan:
            print(f"退出规则: {exit_plan}")


def _resolve_adaptive_pick_count(base_cfg: dict, cmd_name: str, override_count: int | None) -> int | None:
    if override_count is not None:
        return override_count
    adaptive_cfg = base_cfg.get("adaptive_strategy", {})
    counts_cfg = adaptive_cfg.get("strategy_pick_counts", {})
    raw = counts_cfg.get(cmd_name)
    if raw is None:
        return None
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return None


def _choose_adaptive_recommendations(
    base_cfg: dict,
    ds,
    target_date: date,
    override_count: int | None,
) -> dict:
    base_engine = Recommender(ds, base_cfg)
    signal_date = base_engine.resolve_signal_date(target_date)
    market_state, market_reason = base_engine._resolve_market_state(signal_date)
    run_specs = _resolve_adaptive_strategy_specs(base_cfg, market_state.label)
    tried_commands: list[str] = []
    chosen_cmd: str | None = None
    chosen_recs = []
    chosen_count: int | None = None
    chosen_profile_name: str | None = None

    for cmd_name, profile_name, _section_title in run_specs:
        tried_commands.append(cmd_name)
        if cmd_name == "cash":
            chosen_cmd = "cash"
            chosen_count = 0
            chosen_profile_name = None
            break
        cfg = apply_strategy_profile(base_cfg, profile_name)
        cfg = apply_adaptive_parameter_overrides(cfg, market_state.label, cmd_name)
        resolved_count = _resolve_adaptive_pick_count(cfg, cmd_name, override_count)
        engine = Recommender(ds, cfg)
        try:
            recs = engine.recommend_many(target_date, count=resolved_count)
        except RuntimeError as exc:
            if "No candidate found in enabled modes:" not in str(exc):
                raise
            continue
        chosen_cmd = cmd_name
        chosen_recs = recs
        chosen_count = resolved_count
        chosen_profile_name = profile_name
        break

    return {
        "target_date": target_date.isoformat(),
        "signal_date": signal_date.isoformat(),
        "market_state": market_state.label,
        "market_reason": market_reason,
        "tried_strategies": tried_commands,
        "chosen_strategy": chosen_cmd,
        "chosen_count": chosen_count,
        "chosen_profile_name": chosen_profile_name,
        "recommendations": chosen_recs,
    }


def _configure_network(cfg: dict) -> None:
    if cfg.get("network", {}).get("disable_env_proxy", True):
        clear_proxy_env()
    if cfg.get("network", {}).get("force_no_proxy_all", True):
        force_no_proxy_all()
        disable_requests_env_proxy()


def _build_data_source(cfg: dict) -> AkshareDataSource:
    ds_cfg = cfg.get("data_source", {})
    return AkshareDataSource(
        request_timeout_sec=float(ds_cfg.get("request_timeout_sec", 6.0)),
        hist_retries=int(ds_cfg.get("hist_retries", 3)),
        use_spot_name_merge=bool(ds_cfg.get("use_spot_name_merge", False)),
        cache_enabled=bool(ds_cfg.get("cache_enabled", True)),
        cache_dir=str(ds_cfg.get("cache_dir", ".cache/akshare")),
    )


def _run_recommend_config(
    cfg: dict,
    section_title: str,
    target_date: date,
    count: int | None,
    output: str,
) -> tuple[list, bool]:
    _configure_network(cfg)
    ds = _build_data_source(cfg)
    rec_engine = Recommender(ds, cfg)
    report_cfg = cfg.get("reporting", {})
    reporting_enabled = bool(report_cfg.get("enabled", True))
    saved_docs: list[str] = []
    log_path = None

    def _execute_body() -> list:
        if output != "json":
            print(f"\n=== {section_title} ===")
        recs = rec_engine.recommend_many(target_date, count=count)
        if output != "json":
            _print_recommendations(recs, output)
        if reporting_enabled:
            for rec in recs:
                saved_docs.append(
                    str(
                        append_recommendation_csv(
                            rec,
                            str(report_cfg.get("recommendation_csv", "reports/recommendations.csv")),
                        )
                    )
                )
                saved_docs.append(
                    str(
                        append_recommendation_md(
                            rec,
                            str(report_cfg.get("recommendation_md", "reports/recommendations.md")),
                        )
                    )
                )
                saved_docs.append(
                    str(
                        append_recommendation_txt(
                            rec,
                            str(report_cfg.get("recommendation_txt", "reports/recommendations.txt")),
                        )
                    )
                )
        return recs

    if reporting_enabled:
        signal_date = rec_engine.resolve_signal_date(target_date)
        log_path = resolve_recommendation_output_log_path(
            signal_date,
            str(report_cfg.get("recommendation_log", "reports/{signal_date}.log")),
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_file:
            runtime_stdout = log_file if output == "json" else _TeeStdout(sys.stdout, log_file)
            with contextlib.redirect_stdout(runtime_stdout):
                recs = _execute_body()
        if output != "json":
            for saved_doc in saved_docs:
                print(f"已写入文档: {saved_doc}")
            print(f"已写入文档: {log_path}")
    else:
        if output == "json":
            with contextlib.redirect_stdout(io.StringIO()):
                recs = _execute_body()
        else:
            recs = _execute_body()
    return recs, reporting_enabled


def _run_recommend_profile(
    base_cfg: dict,
    profile_name: str | None,
    section_title: str,
    target_date: date,
    count: int | None,
    output: str,
) -> tuple[list, bool]:
    cfg = apply_strategy_profile(base_cfg, profile_name)
    return _run_recommend_config(
        cfg=cfg,
        section_title=section_title,
        target_date=target_date,
        count=count,
        output=output,
    )


def _print_recommendations(recs, output: str) -> None:
    if not recs:
        raise RuntimeError("No recommendations")
    rec = recs[0]
    if output == "json":
        payload = [item.as_dict() for item in recs]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"交易日: {rec.trade_date.isoformat()}  阈值模式: {rec.threshold_mode}  推荐数量: {len(recs)}")
    for idx, item in enumerate(recs, start=1):
        print(f"\n[{idx}] {item.symbol} {item.name}")
        print(f"总分: {item.score_total:.2f}")
        print("关键指标:")
        for k, v in item.key_metrics.items():
            if k == "exit_plan":
                continue
            label = METRIC_LABELS_ZH.get(k, k)
            print(f"  - {label}: {v:.4f}")
        exit_plan = item.key_metrics.get("exit_plan")
        if exit_plan:
            print(f"退出规则: {exit_plan}")
        print("推荐理由:")
        for ridx, r in enumerate(item.reason, start=1):
            print(f"  {ridx}. {r}")


def _print_backtest(summary: dict, output: str) -> None:
    percent_keys = {
        "win_rate_gross_1d",
        "win_rate_gross_3d",
        "win_rate_net_1d",
        "win_rate_net_3d",
        "avg_return_1d_gross",
        "avg_return_3d_gross",
        "avg_return_5d_gross",
        "avg_return_1d_net",
        "avg_return_3d_net",
        "avg_return_5d_net",
        "max_drawdown_proxy",
    }
    rec_percent_keys = {
        "ret_1d_gross",
        "ret_3d_gross",
        "ret_5d_gross",
        "ret_1d_net",
        "ret_3d_net",
        "ret_5d_net",
    }
    if output == "json":
        payload: dict = {}
        for k, v in summary.items():
            if k == "records" and isinstance(v, list):
                records_en = []
                for row in v:
                    row_en = {}
                    for rk, rv in row.items():
                        val = rv
                        if rk in rec_percent_keys and isinstance(rv, (int, float)):
                            val = f"{rv:.2%}"
                        row_en[rk] = val
                    records_en.append(row_en)
                payload[k] = records_en
                continue
            val = v
            if k in percent_keys and isinstance(v, (int, float)):
                val = f"{v:.2%}"
            payload[k] = val
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    if output == "json-cn":
        key_map = {
            "period": "回测区间",
            "entry_price_mode": "入场价格口径",
            "entry_price_desc": "入场价格说明",
            "attempted_days": "尝试交易日",
            "total_trades": "交易次数",
            "skipped_days": "跳过交易日",
            "win_rate_gross_1d": "1日胜率_毛",
            "win_rate_gross_3d": "3日胜率_毛",
            "win_rate_net_1d": "1日胜率_净",
            "win_rate_net_3d": "3日胜率_净",
            "avg_return_1d_gross": "平均1日收益_毛",
            "avg_return_3d_gross": "平均3日收益_毛",
            "avg_return_5d_gross": "平均5日收益_毛",
            "avg_return_1d_net": "平均1日收益_净",
            "avg_return_3d_net": "平均3日收益_净",
            "avg_return_5d_net": "平均5日收益_净",
            "max_drawdown_proxy": "最大回撤代理",
            "threshold_mode_counts": "模式分布",
            "error_counts": "错误统计",
            "error_examples": "错误示例",
            "records": "明细记录",
        }
        rec_key_map = {
            "trade_date": "交易日",
            "symbol": "代码",
            "name": "名称",
            "threshold_mode": "阈值模式",
            "ret_1d_gross": "1日收益_毛",
            "ret_3d_gross": "3日收益_毛",
            "ret_5d_gross": "5日收益_毛",
            "ret_1d_net": "1日收益_净",
            "ret_3d_net": "3日收益_净",
            "ret_5d_net": "5日收益_净",
        }
        err_key_map = {"trade_date": "交易日", "error_type": "错误类型", "message": "错误信息"}
        payload: dict = {}
        for k, v in summary.items():
            out_key = key_map.get(k, k)
            if k == "records" and isinstance(v, list):
                records_cn = []
                for row in v:
                    row_cn = {}
                    for rk, rv in row.items():
                        val = rv
                        if rk in rec_percent_keys and isinstance(rv, (int, float)):
                            val = f"{rv:.2%}"
                        row_cn[rec_key_map.get(rk, rk)] = val
                    records_cn.append(row_cn)
                payload[out_key] = records_cn
            elif k == "error_examples" and isinstance(v, list):
                payload[out_key] = [{err_key_map.get(ek, ek): ev for ek, ev in row.items()} for row in v]
            else:
                val = v
                if k in percent_keys and isinstance(v, (int, float)):
                    val = f"{v:.2%}"
                payload[out_key] = val
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    print(f"回测区间: {summary['period']}")
    if summary.get("entry_price_desc"):
        print(f"入场价格: {summary['entry_price_desc']} ({summary.get('entry_price_mode', 'close')})")
    print(f"尝试交易日: {summary.get('attempted_days', 0)}")
    print(f"跳过交易日: {summary.get('skipped_days', 0)}")
    print(f"交易次数: {summary['total_trades']}")
    print(f"1日胜率(毛): {summary['win_rate_gross_1d']:.2%}")
    print(f"3日胜率(毛): {summary.get('win_rate_gross_3d', 0.0):.2%}")
    print(f"1日胜率(净): {summary['win_rate_net_1d']:.2%}")
    print(f"3日胜率(净): {summary.get('win_rate_net_3d', 0.0):.2%}")
    print(f"平均1日收益(毛): {summary['avg_return_1d_gross']:.4%}")
    print(f"平均3日收益(毛): {summary.get('avg_return_3d_gross', 0.0):.4%}")
    print(f"平均1日收益(净): {summary['avg_return_1d_net']:.4%}")
    print(f"平均3日收益(净): {summary.get('avg_return_3d_net', 0.0):.4%}")
    print(f"平均5日收益(毛): {summary['avg_return_5d_gross']:.4%}")
    print(f"平均5日收益(净): {summary['avg_return_5d_net']:.4%}")
    print(f"最大回撤代理: {summary['max_drawdown_proxy']:.2%}")
    mode_counts = summary.get("threshold_mode_counts", {})
    if mode_counts:
        print(f"模式分布: {mode_counts}")
    adaptive_counts = summary.get("adaptive_strategy_counts", {})
    if adaptive_counts:
        print(f"自适应策略分布: {adaptive_counts}")
    error_counts = summary.get("error_counts", {})
    if error_counts:
        print(f"错误统计: {error_counts}")
    examples = summary.get("error_examples", [])
    if examples:
        print("错误示例:")
        for e in examples[:5]:
            print(f"  - {e['trade_date']} {e['error_type']}: {e['message']}")


def _print_intraday_review(summary: dict, output: str) -> None:
    percent_keys = {
        "win_rate_next_open",
        "avg_return_next_open",
        "median_return_next_open",
        "worst_return_next_open",
        "win_rate_next_close",
        "avg_return_next_close",
    }
    record_percent_keys = {"ret_next_open", "ret_next_close"}
    if output == "json":
        payload = {}
        for key, value in summary.items():
            if key == "records" and isinstance(value, list):
                payload[key] = [
                    {
                        row_key: f"{row_value:.2%}"
                        if row_key in record_percent_keys and isinstance(row_value, (int, float))
                        else row_value
                        for row_key, row_value in row.items()
                    }
                    for row in value
                ]
            elif key in percent_keys and isinstance(value, (int, float)):
                payload[key] = f"{value:.2%}"
            else:
                payload[key] = value
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    print(f"策略: {summary['strategy']}")
    print(f"信号日数量: {summary['signal_days']}")
    print(f"空仓日数量: {summary['no_trade_days']}")
    print(f"入选信号: {summary['selected_signals']}")
    print(f"完成复盘: {summary['completed_trades']}")
    print(f"跳过信号: {summary['skipped_signals']}")
    print(f"次日开盘胜率: {summary['win_rate_next_open']:.2%}")
    print(f"次日开盘平均收益: {summary['avg_return_next_open']:.4%}")
    print(f"次日开盘中位数收益: {summary['median_return_next_open']:.4%}")
    print(f"次日开盘最差收益: {summary['worst_return_next_open']:.4%}")
    print(f"次日收盘胜率: {summary['win_rate_next_close']:.2%}")
    print(f"次日收盘平均收益: {summary['avg_return_next_close']:.4%}")


def _format_backtest_output(summary: dict, output: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_backtest(summary, output)
    return buf.getvalue()


def _insert_path_token(path: str, token: str | None) -> str:
    if not token:
        return path
    file_path = Path(path)
    if file_path.suffix:
        return str(file_path.with_name(f"{file_path.stem}.{token}{file_path.suffix}"))
    return f"{path}.{token}"


def _resolve_adaptive_backtest_report_paths(
    base_cfg: dict,
    start_date: date,
    end_date: date,
    entry_price_mode: str,
) -> tuple[str, str]:
    reporting_cfg = base_cfg.get("reporting", {})
    period_key = f"{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}"
    period_template = str(
        reporting_cfg.get(
            "adaptive_backtest_summary",
            "reports/backtests/adaptive/{period_key}.json",
        )
    )
    latest_path = str(reporting_cfg.get("adaptive_backtest_latest", "reports/backtests/adaptive/latest.json"))
    token = None if entry_price_mode == "close" else entry_price_mode.replace("-", "_")
    return _insert_path_token(period_template.format(period_key=period_key), token), _insert_path_token(latest_path, token)


def _load_json_file(path: str) -> dict | None:
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_json_file(path: str, payload: dict) -> str:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(file_path)


def _build_backtest_delta(current: dict, previous: dict | None) -> dict | None:
    if not previous or previous.get("period") != current.get("period"):
        return None
    if previous.get("entry_price_mode", "close") != current.get("entry_price_mode", "close"):
        return None
    keys = [
        "total_trades",
        "skipped_days",
        "win_rate_net_1d",
        "win_rate_net_3d",
        "avg_return_1d_net",
        "avg_return_3d_net",
        "avg_return_5d_net",
        "max_drawdown_proxy",
    ]
    delta: dict[str, float] = {}
    for key in keys:
        cur = current.get(key)
        prev = previous.get(key)
        if isinstance(cur, (int, float)) and isinstance(prev, (int, float)):
            delta[key] = round(cur - prev, 6)
    current_counts = current.get("adaptive_strategy_counts", {})
    prev_counts = previous.get("adaptive_strategy_counts", {})
    if isinstance(current_counts, dict) and isinstance(prev_counts, dict):
        count_delta = {}
        for key in set(current_counts) | set(prev_counts):
            count_delta[key] = int(current_counts.get(key, 0)) - int(prev_counts.get(key, 0))
        delta["adaptive_strategy_counts"] = count_delta
    return delta or None


def _print_backtest_delta(delta: dict | None) -> None:
    if not delta:
        return
    print("与上次同区间结果对比:")
    for key in [
        "total_trades",
        "skipped_days",
        "win_rate_net_1d",
        "win_rate_net_3d",
        "avg_return_1d_net",
        "avg_return_3d_net",
        "avg_return_5d_net",
        "max_drawdown_proxy",
    ]:
        if key not in delta:
            continue
        print(f"  - {key}: {delta[key]:+}")
    strategy_delta = delta.get("adaptive_strategy_counts")
    if isinstance(strategy_delta, dict):
        print(f"  - adaptive_strategy_counts: {strategy_delta}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A-share daily stock picker")
    p.add_argument("--config", default="config/default.yaml", help="Path to YAML config")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("recommend", help="Recommend top stocks for target trading day")
    p_rec.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    p_rec.add_argument("--count", type=int, default=None, help="How many stocks to pick; defaults to strategy.pick_count")
    p_rec.add_argument("--output", choices=["table", "json"], default="table")

    p_rec_all = sub.add_parser("recommend-all", help="Run both recommend and recommend-pullback for target trading day")
    p_rec_all.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    p_rec_all.add_argument("--count", type=int, default=None, help="How many stocks to pick per strategy run")
    p_rec_all.add_argument("--output", choices=["table", "json"], default="table")

    p_rec_pb = sub.add_parser("recommend-pullback", help="Recommend pullback-confirmation stocks for target trading day")
    p_rec_pb.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    p_rec_pb.add_argument(
        "--count",
        type=int,
        default=None,
        help="How many stocks to pick; defaults to strategy.pick_count in pullback profile",
    )
    p_rec_pb.add_argument("--output", choices=["table", "json"], default="table")

    p_rec_os = sub.add_parser("recommend-oversold", help="Recommend oversold-rebound stocks for target trading day")
    p_rec_os.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    p_rec_os.add_argument(
        "--count",
        type=int,
        default=None,
        help="How many stocks to pick; defaults to strategy.pick_count in oversold profile",
    )
    p_rec_os.add_argument("--output", choices=["table", "json"], default="table")

    p_rec_bull = sub.add_parser("recommend-bull", help="Recommend bull-trend research stocks for target trading day")
    p_rec_bull.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    p_rec_bull.add_argument(
        "--count",
        type=int,
        default=None,
        help="How many stocks to pick; defaults to strategy.pick_count in bull research profile",
    )
    p_rec_bull.add_argument("--output", choices=["table", "json"], default="table")

    p_rec_rel = sub.add_parser("recommend-relative", help="Recommend relative-strength research stocks for target trading day")
    p_rec_rel.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    p_rec_rel.add_argument(
        "--count",
        type=int,
        default=None,
        help="How many stocks to pick; defaults to strategy.pick_count in relative-strength profile",
    )
    p_rec_rel.add_argument("--output", choices=["table", "json"], default="table")

    p_rec_ad = sub.add_parser("recommend-adaptive", help="Auto-pick strategy by market regime for target trading day")
    p_rec_ad.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    p_rec_ad.add_argument("--count", type=int, default=None, help="How many stocks to pick for the chosen strategy")
    p_rec_ad.add_argument("--output", choices=["table", "json"], default="table")

    p_rec_pool = sub.add_parser("recommend-opportunity", help="Build a wider opportunity pool for discretionary review")
    p_rec_pool.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    p_rec_pool.add_argument("--count", type=int, default=None, help="Total number of opportunity names to show")
    p_rec_pool.add_argument("--output", choices=["table", "json"], default="table")

    p_tail = sub.add_parser("tail-pick", help="Pick one isolated tail-session candidate")
    p_tail.add_argument("--date", default=None, help="Run date YYYY-MM-DD; defaults to today")
    p_tail.add_argument("--output", choices=["table", "json"], default="table")

    p_auction = sub.add_parser("auction-pick", help="Pick opening auction candidates from one quote snapshot")
    p_auction.add_argument("--date", default=None, help="Run date YYYY-MM-DD; defaults to today")
    p_auction.add_argument("--count", type=int, default=None, help="How many auction candidates to show")
    p_auction.add_argument("--output", choices=["table", "json"], default="table")

    p_intraday_review = sub.add_parser("analyze-intraday-picks", help="Review saved auction/tail pick signals")
    p_intraday_review.add_argument(
        "--signals",
        default="reports/intraday_pick_signals.jsonl",
        help="JSONL signal file saved by auction-pick and tail-pick",
    )
    p_intraday_review.add_argument("--strategy", choices=["auction-pick", "tail-pick"], default=None)
    p_intraday_review.add_argument("--start", default=None, help="Start signal date YYYY-MM-DD")
    p_intraday_review.add_argument("--end", default=None, help="End signal date YYYY-MM-DD")
    p_intraday_review.add_argument("--output", choices=["table", "json"], default="table")

    p_exp = sub.add_parser("explain", help="Explain one stock score on target date")
    p_exp.add_argument("--symbol", required=True, help="Stock code like 000001")
    p_exp.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    p_exp.add_argument("--mode", choices=["normal", "relaxed", "force"], default="normal")
    p_exp.add_argument("--output", choices=["table", "json"], default="table")

    p_bt = sub.add_parser("backtest", help="Backtest over period")
    p_bt.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_bt.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_bt.add_argument("--count", type=int, default=None, help="How many stocks per day; defaults to strategy.pick_count")
    p_bt.add_argument("--output", choices=["table", "json", "json-cn"], default="json-cn")
    p_bt.add_argument("--entry-price", choices=["close", "next-open"], default="close", help="Entry price mode")

    p_bt_pb = sub.add_parser("backtest-pullback", help="Backtest the pullback-confirmation strategy over period")
    p_bt_pb.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_bt_pb.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_bt_pb.add_argument(
        "--count",
        type=int,
        default=None,
        help="How many stocks per day; defaults to strategy.pick_count in pullback profile",
    )
    p_bt_pb.add_argument("--output", choices=["table", "json", "json-cn"], default="json-cn")
    p_bt_pb.add_argument("--entry-price", choices=["close", "next-open"], default="close", help="Entry price mode")

    p_bt_bull = sub.add_parser("backtest-bull", help="Backtest the bull-trend research strategy over period")
    p_bt_bull.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_bt_bull.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_bt_bull.add_argument(
        "--count",
        type=int,
        default=None,
        help="How many stocks per day; defaults to strategy.pick_count in bull research profile",
    )
    p_bt_bull.add_argument("--output", choices=["table", "json", "json-cn"], default="json-cn")
    p_bt_bull.add_argument("--entry-price", choices=["close", "next-open"], default="close", help="Entry price mode")

    p_bt_rel = sub.add_parser("backtest-relative", help="Backtest the relative-strength research strategy over period")
    p_bt_rel.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_bt_rel.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_bt_rel.add_argument(
        "--count",
        type=int,
        default=None,
        help="How many stocks per day; defaults to strategy.pick_count in relative-strength profile",
    )
    p_bt_rel.add_argument("--output", choices=["table", "json", "json-cn"], default="json-cn")
    p_bt_rel.add_argument("--entry-price", choices=["close", "next-open"], default="close", help="Entry price mode")

    p_bt_ad = sub.add_parser("backtest-adaptive", help="Backtest the adaptive strategy over period")
    p_bt_ad.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_bt_ad.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_bt_ad.add_argument("--count", type=int, default=None, help="Optional override for per-strategy adaptive pick count")
    p_bt_ad.add_argument("--output", choices=["table", "json", "json-cn"], default="json-cn")
    p_bt_ad.add_argument("--no-save-report", action="store_true", help="Do not save adaptive backtest report files")
    p_bt_ad.add_argument("--entry-price", choices=["close", "next-open"], default="close", help="Entry price mode")

    p_bt_ad_rules = sub.add_parser("backtest-adaptive-rules", help="Backtest the adaptive strategy with rule-based exits")
    p_bt_ad_rules.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_bt_ad_rules.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_bt_ad_rules.add_argument("--count", type=int, default=None, help="Optional override for per-strategy adaptive pick count")
    p_bt_ad_rules.add_argument("--output", choices=["table", "json", "json-cn"], default="json-cn")
    p_bt_ad_rules.add_argument("--entry-price", choices=["close", "next-open"], default="close", help="Entry price mode")

    p_bt_auction_proxy = sub.add_parser(
        "backtest-auction-pick-proxy",
        help="Daily-bar proxy backtest for auction-pick with next-open exit",
    )
    p_bt_auction_proxy.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_bt_auction_proxy.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_bt_auction_proxy.add_argument("--count", type=int, default=None, help="How many proxy candidates per day")
    p_bt_auction_proxy.add_argument("--output", choices=["table", "json", "json-cn"], default="json-cn")

    p_bt_tail_proxy = sub.add_parser(
        "backtest-tail-pick-proxy",
        help="Daily-bar proxy backtest for tail-pick with next-open exit",
    )
    p_bt_tail_proxy.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_bt_tail_proxy.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_bt_tail_proxy.add_argument("--count", type=int, default=None, help="How many proxy candidates per day")
    p_bt_tail_proxy.add_argument("--output", choices=["table", "json", "json-cn"], default="json-cn")

    p_doc = sub.add_parser("doctor", help="Run connectivity diagnostics for data sources")
    p_doc.add_argument("--output", choices=["table", "json"], default="table")

    p_ck = sub.add_parser("check-kline", help="Check single symbol kline fetch in date range")
    p_ck.add_argument("--symbol", required=True, help="Stock code like 000001")
    p_ck.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_ck.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_ck.add_argument("--output", choices=["table", "json"], default="table")

    p_sector = sub.add_parser("check-sector-map", help="Validate local sector map coverage")
    p_sector.add_argument("--path", default=None, help="Path to sector map CSV; defaults to config sector_map.path")
    p_sector.add_argument("--output", choices=["table", "json"], default="table")

    p_dash = sub.add_parser("export-dashboard-data", help="Export merged recommendation data for index.html")
    p_dash.add_argument("--default-csv", default="reports/recommendations.csv", help="Path to recommend CSV")
    p_dash.add_argument(
        "--pullback-csv",
        default="reports/pullback_recommendations.csv",
        help="Path to recommend-pullback CSV",
    )
    p_dash.add_argument(
        "--oversold-csv",
        default="reports/oversold_recommendations.csv",
        help="Path to recommend-oversold CSV",
    )
    p_dash.add_argument(
        "--opportunity-csv",
        default="reports/opportunity_recommendations.csv",
        help="Path to recommend-opportunity CSV",
    )
    p_dash.add_argument("--output", default="reports/dashboard-data.js", help="Path to generated dashboard data")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    base_cfg = load_config(args.config)
    if args.cmd == "export-dashboard-data":
        saved = export_dashboard_data(
            default_csv=args.default_csv,
            pullback_csv=args.pullback_csv,
            oversold_csv=args.oversold_csv,
            output_path=args.output,
            cfg=base_cfg,
            opportunity_csv=args.opportunity_csv,
        )
        print(f"Dashboard data exported to {saved}")
        return

    if args.cmd == "tail-pick":
        from app.tail_pick.engine import TailPickEngine

        _configure_network(base_cfg)
        ds = _build_data_source(base_cfg)
        trade_date = _parse_date(args.date)
        payload = TailPickEngine(ds, base_cfg).pick(trade_date)
        signal_path = None
        if bool(base_cfg.get("reporting", {}).get("enabled", True)):
            signal_path = append_intraday_pick_signals(
                "tail-pick",
                payload,
                str(base_cfg.get("reporting", {}).get("intraday_pick_signals_jsonl", "reports/intraday_pick_signals.jsonl")),
                source="cli",
            )
        if args.output == "json":
            print(json.dumps(payload.as_dict(), ensure_ascii=False, indent=2))
            return
        print(
            f"[尾盘] 日期={payload.trade_date.isoformat()} "
            f"扫描={payload.candidates_scanned} 入围={payload.candidates_passed}"
        )
        if payload.selected is None:
            print("[尾盘] 当前没有符合条件的尾盘候选，建议空仓。")
            if signal_path:
                print(f"已写入文档: {signal_path}")
            return
        item = payload.selected
        print(f"[尾盘] 主选: {item.quote.symbol} {item.quote.name}")
        print(f"现价: {item.quote.latest:.2f} 涨幅: {item.intraday_return:.2%} 分数: {item.score:.2f}")
        print(f"参考买入: {item.entry_price:.2f} 止损: {item.stop_loss_price:.2f}")
        print("理由:")
        for idx, reason in enumerate(item.reasons, start=1):
            print(f"  {idx}. {reason}")
        print("次日卖出规则:")
        for idx, rule in enumerate(item.next_day_sell_rules, start=1):
            print(f"  {idx}. {rule}")
        if signal_path:
            print(f"已写入文档: {signal_path}")
        return

    if args.cmd == "auction-pick":
        from app.auction_pick.engine import AuctionPickEngine

        _configure_network(base_cfg)
        ds = _build_data_source(base_cfg)
        trade_date = _parse_date(args.date)
        payload = AuctionPickEngine(ds, base_cfg).pick(trade_date, count=args.count)
        signal_path = None
        if bool(base_cfg.get("reporting", {}).get("enabled", True)):
            signal_path = append_intraday_pick_signals(
                "auction-pick",
                payload,
                str(base_cfg.get("reporting", {}).get("intraday_pick_signals_jsonl", "reports/intraday_pick_signals.jsonl")),
                source="cli",
            )
        if args.output == "json":
            print(json.dumps(payload.as_dict(), ensure_ascii=False, indent=2))
            return
        print(
            f"[竞价] 日期={payload.trade_date.isoformat()} "
            f"扫描={payload.candidates_scanned} 入围={payload.candidates_passed}"
        )
        if not payload.selected:
            print("[竞价] 当前没有符合条件的竞价候选，建议空仓或只观察。")
            if signal_path:
                print(f"已写入文档: {signal_path}")
            return
        for idx, item in enumerate(payload.selected, start=1):
            print(f"\n[{idx}] {item.quote.symbol} {item.quote.name}")
            print(
                f"现价: {item.quote.latest:.2f} 开盘: {item.quote.open:.2f} "
                f"高开: {item.opening_gap:.2%} 当前涨幅: {item.current_return:.2%} "
                f"成交额: {item.quote.amount / 10000:.0f}万 分数: {item.score:.2f}"
            )
            print("理由:")
            for reason_idx, reason in enumerate(item.reasons, start=1):
                print(f"  {reason_idx}. {reason}")
            print("执行观察:")
            for note_idx, note in enumerate(item.execution_notes, start=1):
                print(f"  {note_idx}. {note}")
        if signal_path:
            print(f"已写入文档: {signal_path}")
        return

    if args.cmd == "analyze-intraday-picks":
        _configure_network(base_cfg)
        ds = _build_data_source(base_cfg)
        summary = analyze_intraday_pick_signals(
            args.signals,
            ds,
            strategy=args.strategy,
            start_date=_parse_date(args.start) if args.start else None,
            end_date=_parse_date(args.end) if args.end else None,
        )
        _print_intraday_review(summary, args.output)
        return

    if args.cmd == "recommend-adaptive":
        _configure_network(base_cfg)
        ds = _build_data_source(base_cfg)
        target_date = _resolve_recommend_target_date(ds, args.date)
        base_engine = Recommender(ds, base_cfg)
        signal_date = base_engine.resolve_signal_date(target_date)
        market_state, market_reason = base_engine._resolve_market_state(signal_date)
        run_specs = _resolve_adaptive_strategy_specs(base_cfg, market_state.label)
        chosen_cmd: str | None = None
        chosen_recs = []
        any_reporting_enabled = False
        tried_commands: list[str] = []
        adaptive_reporting_enabled = bool(base_cfg.get("reporting", {}).get("enabled", True))
        chosen_count: int | None = None
        opportunity_payload: dict | None = None
        saved_opportunity_csv: str | None = None

        if args.output != "json":
            print(
                f"[自适应] 目标日={target_date.isoformat()} 信号日={signal_date.isoformat()} "
                f"市场={market_state.label} 原因={market_reason}"
            )
            print(f"[自适应] 策略顺序: {', '.join(cmd_name for cmd_name, _, _ in run_specs)}")

        for cmd_name, profile_name, section_title in run_specs:
            tried_commands.append(cmd_name)
            if cmd_name == "cash":
                chosen_cmd = "cash"
                chosen_count = 0
                break
            cfg = apply_strategy_profile(base_cfg, profile_name)
            cfg = apply_adaptive_parameter_overrides(cfg, market_state.label, cmd_name)
            resolved_count = _resolve_adaptive_pick_count(cfg, cmd_name, args.count)
            try:
                recs, reporting_enabled = _run_recommend_config(
                    cfg=cfg,
                    section_title=f"自适应选择: {section_title}",
                    target_date=target_date,
                    count=resolved_count,
                    output=args.output,
                )
            except RuntimeError as exc:
                if "No candidate found in enabled modes:" not in str(exc):
                    raise
                if args.output != "json":
                    print(f"[自适应] {cmd_name} 当前无候选，继续尝试下一策略。")
                continue
            chosen_cmd = cmd_name
            chosen_recs = recs
            chosen_count = resolved_count
            any_reporting_enabled = any_reporting_enabled or reporting_enabled
            break

        if adaptive_reporting_enabled:
            adaptive_summary = {
                "target_date": target_date.isoformat(),
                "signal_date": signal_date.isoformat(),
                "market_state": market_state.label,
                "market_reason": market_reason,
                "tried_strategies": tried_commands,
                "chosen_strategy": chosen_cmd,
                "has_recommendations": bool(chosen_recs),
                "chosen_count": chosen_count or 0,
            }
            saved_adaptive_csv = append_adaptive_run_csv(
                adaptive_summary,
                str(base_cfg.get("reporting", {}).get("adaptive_run_csv", "reports/adaptive_runs.csv")),
            )
            if args.output != "json":
                print(f"已写入文档: {saved_adaptive_csv}")
        if not chosen_recs:
            opportunity_payload = _build_opportunity_pool(base_cfg, ds, target_date, args.count)
            saved_opportunity_csv = _save_opportunity_pool_csv(base_cfg, opportunity_payload)
            if saved_opportunity_csv and args.output != "json":
                print(f"已写入文档: {saved_opportunity_csv}")

        if any_reporting_enabled or adaptive_reporting_enabled or saved_opportunity_csv:
            dashboard_default_csv, dashboard_pullback_csv, dashboard_oversold_csv, dashboard_opportunity_csv, dashboard_output = _resolve_dashboard_export_args(base_cfg)
            saved_dashboard = export_dashboard_data(
                default_csv=dashboard_default_csv,
                pullback_csv=dashboard_pullback_csv,
                oversold_csv=dashboard_oversold_csv,
                output_path=dashboard_output,
                cfg=base_cfg,
                opportunity_csv=dashboard_opportunity_csv,
            )
            if args.output != "json":
                print(f"已写入文档: {saved_dashboard}")

        if args.output == "json":
            payload = {
                "target_date": target_date.isoformat(),
                "signal_date": signal_date.isoformat(),
                "market_state": market_state.label,
                "market_reason": market_reason,
                "tried_strategies": tried_commands,
                "chosen_strategy": chosen_cmd,
                "chosen_count": chosen_count,
                "recommendations": [item.as_dict() for item in chosen_recs],
                "opportunity_pool": opportunity_payload,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif not chosen_recs:
            print("[自适应] 当前市场下所有候选策略都无信号，建议空仓。")
            print("[自适应] 已自动生成机会池供人工复核。")
            _print_opportunity_pool(opportunity_payload or {"target_date": target_date.isoformat(), "signal_date": signal_date.isoformat(), "market_state": market_state.label, "market_reason": market_reason, "pool": []})
        else:
            count_note = f"；采用数量: {chosen_count}" if chosen_count else ""
            print(f"[自适应] 已采用策略: {chosen_cmd}{count_note}")
        return

    if args.cmd == "recommend-opportunity":
        _configure_network(base_cfg)
        ds = _build_data_source(base_cfg)
        target_date = _resolve_recommend_target_date(ds, args.date)
        payload = _build_opportunity_pool(base_cfg, ds, target_date, args.count)
        saved_opportunity_csv = _save_opportunity_pool_csv(base_cfg, payload)
        if saved_opportunity_csv:
            dashboard_default_csv, dashboard_pullback_csv, dashboard_oversold_csv, dashboard_opportunity_csv, dashboard_output = _resolve_dashboard_export_args(base_cfg)
            saved_dashboard = export_dashboard_data(
                default_csv=dashboard_default_csv,
                pullback_csv=dashboard_pullback_csv,
                oversold_csv=dashboard_oversold_csv,
                output_path=dashboard_output,
                cfg=base_cfg,
                opportunity_csv=dashboard_opportunity_csv,
            )
            if args.output != "json":
                print(f"已写入文档: {saved_opportunity_csv}")
                print(f"已写入文档: {saved_dashboard}")
        if args.output == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        _print_opportunity_pool(payload)
        return

    if args.cmd in {"recommend", "recommend-all", "recommend-pullback", "recommend-oversold", "recommend-bull", "recommend-relative"}:
        _configure_network(base_cfg)
        target_date = _resolve_recommend_target_date(_build_data_source(base_cfg), args.date)
        run_specs = _resolve_recommend_run_specs(args.cmd)
        json_payload: dict[str, list[dict]] = {}
        any_reporting_enabled = False
        for cmd_name, profile_name, section_title in run_specs:
            recs, reporting_enabled = _run_recommend_profile(
                base_cfg=base_cfg,
                profile_name=profile_name,
                section_title=section_title,
                target_date=target_date,
                count=args.count,
                output=args.output,
            )
            any_reporting_enabled = any_reporting_enabled or reporting_enabled
            json_payload[cmd_name] = [item.as_dict() for item in recs]
        if any_reporting_enabled:
            dashboard_default_csv, dashboard_pullback_csv, dashboard_oversold_csv, dashboard_opportunity_csv, dashboard_output = _resolve_dashboard_export_args(base_cfg)
            saved_dashboard = export_dashboard_data(
                default_csv=dashboard_default_csv,
                pullback_csv=dashboard_pullback_csv,
                oversold_csv=dashboard_oversold_csv,
                output_path=dashboard_output,
                cfg=base_cfg,
                opportunity_csv=dashboard_opportunity_csv,
            )
            if args.output != "json":
                print(f"已写入文档: {saved_dashboard}")
        if args.output == "json":
            if args.cmd == "recommend-all":
                print(json.dumps(json_payload, ensure_ascii=False, indent=2))
            else:
                print(json.dumps(json_payload[args.cmd], ensure_ascii=False, indent=2))
        return

    if args.cmd == "backtest-adaptive":
        cfg = base_cfg
        _configure_network(cfg)
        start = _parse_date(args.start)
        end = _parse_date(args.end)
        summary = run_local_adaptive_backtest(base_cfg, start, end, args.count, args.entry_price)
        previous_summary = None
        saved_paths: list[str] = []
        if not args.no_save_report:
            period_path, latest_path = _resolve_adaptive_backtest_report_paths(base_cfg, start, end, args.entry_price)
            previous_summary = _load_json_file(period_path)
            saved_paths.append(_save_json_file(period_path, summary))
            saved_paths.append(_save_json_file(latest_path, summary))
        delta = _build_backtest_delta(summary, previous_summary)
        if args.output in {"json", "json-cn"}:
            payload = dict(summary)
            if delta:
                payload["comparison_to_previous"] = delta
            print(_format_backtest_output(payload, args.output), end="")
        else:
            _print_backtest(summary, args.output)
            if delta:
                _print_backtest_delta(delta)
            if saved_paths:
                for path in saved_paths:
                    print(f"已写入文档: {path}")
        return

    if args.cmd == "backtest-adaptive-rules":
        cfg = base_cfg
        _configure_network(cfg)
        start = _parse_date(args.start)
        end = _parse_date(args.end)
        summary = run_local_rule_adaptive_backtest(base_cfg, start, end, args.count, args.entry_price)
        _print_backtest(summary, args.output)
        return

    if args.cmd in {"backtest-auction-pick-proxy", "backtest-tail-pick-proxy"}:
        strategy = "auction-pick" if args.cmd == "backtest-auction-pick-proxy" else "tail-pick"
        start = _parse_date(args.start)
        end = _parse_date(args.end)
        summary = run_local_intraday_proxy_backtest(base_cfg, strategy, start, end, args.count)
        _print_backtest(summary, args.output)
        return

    cfg = base_cfg
    strategy_profile = None
    if args.cmd in {"backtest-pullback"}:
        strategy_profile = "pullback_confirm"
    elif args.cmd in {"backtest-bull"}:
        strategy_profile = "bull_trend_research"
    cfg = apply_strategy_profile(cfg, strategy_profile)
    _configure_network(cfg)
    ds = _build_data_source(cfg)
    rec_engine = Recommender(ds, cfg)

    if args.cmd == "explain":
        target = _parse_date(args.date)
        cand = rec_engine.explain(args.symbol, target, mode=args.mode)
        payload = {
            "trade_date": target.isoformat(),
            "symbol": cand.symbol,
            "name": cand.name,
            "score_total": cand.score_total,
            "score_breakdown": cand.score_breakdown,
            "key_metrics": cand.key_metrics,
            "reason": cand.reason,
        }
        if args.output == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        print(f"交易日: {target.isoformat()}  股票: {cand.symbol} {cand.name}")
        print(f"总分: {cand.score_total:.2f}")
        print("分项:")
        for k, v in cand.score_breakdown.items():
            print(f"  - {k}: {v:.2f}")
        print("关键指标:")
        for k, v in cand.key_metrics.items():
            print(f"  - {k}: {v:.4f}")
        print("理由:")
        for idx, r in enumerate(cand.reason, start=1):
            print(f"  {idx}. {r}")
        return

    if args.cmd == "backtest-bull":
        start = _parse_date(args.start)
        end = _parse_date(args.end)
        summary = run_local_single_backtest(base_cfg, "bull_trend_research", start, end, args.count, args.entry_price)
        _print_backtest(summary, args.output)
        return

    if args.cmd == "backtest-relative":
        start = _parse_date(args.start)
        end = _parse_date(args.end)
        summary = run_local_single_backtest(base_cfg, "relative_strength", start, end, args.count, args.entry_price)
        _print_backtest(summary, args.output)
        return

    if args.cmd in {"backtest", "backtest-pullback"}:
        runner = BacktestRunner(rec_engine)
        # When emitting JSON, suppress verbose runtime logs and keep only final payload.
        if args.output in {"json", "json-cn"}:
            with contextlib.redirect_stdout(io.StringIO()):
                summary = runner.run(
                    _parse_date(args.start),
                    _parse_date(args.end),
                    count=args.count,
                    entry_price_mode=args.entry_price,
                )
        else:
            summary = runner.run(
                _parse_date(args.start),
                _parse_date(args.end),
                count=args.count,
                entry_price_mode=args.entry_price,
            )
        _print_backtest(summary, args.output)
        return

    if args.cmd == "doctor":
        report = run_doctor()
        if args.output == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return
        print_doctor_report(report)
        return

    if args.cmd == "check-kline":
        start = _parse_date(args.start)
        end = _parse_date(args.end)
        bars = ds.get_daily_bars(args.symbol, start, end)
        payload = {
            "symbol": args.symbol,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "rows": len(bars),
            "first_date": bars[0].trade_date.isoformat() if bars else None,
            "last_date": bars[-1].trade_date.isoformat() if bars else None,
        }
        if args.output == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        print(f"symbol: {payload['symbol']}")
        print(f"range: {payload['start']} -> {payload['end']}")
        print(f"rows: {payload['rows']}")
        print(f"first_date: {payload['first_date']}")
        print(f"last_date: {payload['last_date']}")
        return

    if args.cmd == "check-sector-map":
        sector_path = args.path or str(base_cfg.get("sector_map", {}).get("path", "data/sector_map.csv"))
        cache_dir = str(base_cfg.get("data_source", {}).get("cache_dir", ".cache/akshare"))
        payload = summarize_sector_map(sector_path, cache_dir)
        if args.output == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        print(f"path: {payload['path']}")
        print(f"rows: {payload['rows']}")
        print(f"unique_sectors: {payload['unique_sectors']}")
        print(f"matched_cached_symbols: {payload['matched_cached_symbols']}")
        print(f"unmatched_symbols: {payload['unmatched_symbols']}")
        print(f"top_sectors: {payload['top_sectors']}")
        print(f"sample_matches: {payload['sample_matches']}")
        print(f"sample_unmatched: {payload['sample_unmatched']}")
        return

    raise RuntimeError(f"Unsupported command: {args.cmd}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"错误: {friendly_error_message(exc)}", file=sys.stderr)
        sys.exit(1)
