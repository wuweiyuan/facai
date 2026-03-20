from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from datetime import date, datetime, timedelta

from app.backtest.runner import BacktestRunner
from app.config import apply_strategy_profile, load_config
from app.dashboard import export_dashboard_data
from app.data_source.akshare_client import AkshareDataSource
from app.doctor import print_doctor_report, run_doctor
from app.engine.recommender import Recommender
from app.error_messages import friendly_error_message
from app.network import clear_proxy_env, disable_requests_env_proxy, force_no_proxy_all
from app.reporting import (
    append_recommendation_csv,
    append_recommendation_md,
    append_recommendation_txt,
    resolve_recommendation_output_log_path,
)


def _resolve_dashboard_export_args(base_cfg: dict) -> tuple[str, str, str]:
    default_csv = str(base_cfg.get("reporting", {}).get("recommendation_csv", "reports/recommendations.csv"))
    pullback_cfg = apply_strategy_profile(base_cfg, "pullback_confirm")
    pullback_csv = str(pullback_cfg.get("reporting", {}).get("recommendation_csv", "reports/pullback_recommendations.csv"))
    dashboard_js = str(base_cfg.get("reporting", {}).get("dashboard_data_js", "reports/dashboard-data.js"))
    return default_csv, pullback_csv, dashboard_js


METRIC_LABELS_ZH = {
    "close": "收盘价",
    "ma20": "20日均线",
    "ma60": "60日均线",
    "mom5": "5日动量",
    "mom20": "20日动量",
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
        ]
    if cmd == "recommend":
        return [("recommend", None, "默认策略 recommend")]
    if cmd == "recommend-pullback":
        return [("recommend-pullback", "pullback_confirm", "回踩策略 recommend-pullback")]
    raise RuntimeError(f"Unsupported recommend command: {cmd}")


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


def _run_recommend_profile(
    base_cfg: dict,
    profile_name: str | None,
    section_title: str,
    target_date: date,
    count: int | None,
    output: str,
) -> tuple[list, bool]:
    cfg = apply_strategy_profile(base_cfg, profile_name)
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
            label = METRIC_LABELS_ZH.get(k, k)
            print(f"  - {label}: {v:.4f}")
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
    error_counts = summary.get("error_counts", {})
    if error_counts:
        print(f"错误统计: {error_counts}")
    examples = summary.get("error_examples", [])
    if examples:
        print("错误示例:")
        for e in examples[:5]:
            print(f"  - {e['trade_date']} {e['error_type']}: {e['message']}")


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

    p_doc = sub.add_parser("doctor", help="Run connectivity diagnostics for data sources")
    p_doc.add_argument("--output", choices=["table", "json"], default="table")

    p_ck = sub.add_parser("check-kline", help="Check single symbol kline fetch in date range")
    p_ck.add_argument("--symbol", required=True, help="Stock code like 000001")
    p_ck.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_ck.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_ck.add_argument("--output", choices=["table", "json"], default="table")

    p_dash = sub.add_parser("export-dashboard-data", help="Export merged recommendation data for index.html")
    p_dash.add_argument("--default-csv", default="reports/recommendations.csv", help="Path to recommend CSV")
    p_dash.add_argument(
        "--pullback-csv",
        default="reports/pullback_recommendations.csv",
        help="Path to recommend-pullback CSV",
    )
    p_dash.add_argument("--output", default="reports/dashboard-data.js", help="Path to generated dashboard data")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    base_cfg = load_config(args.config)
    if args.cmd == "export-dashboard-data":
        saved = export_dashboard_data(args.default_csv, args.pullback_csv, args.output)
        print(f"Dashboard data exported to {saved}")
        return

    if args.cmd in {"recommend", "recommend-all", "recommend-pullback"}:
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
            dashboard_default_csv, dashboard_pullback_csv, dashboard_output = _resolve_dashboard_export_args(base_cfg)
            saved_dashboard = export_dashboard_data(
                dashboard_default_csv,
                dashboard_pullback_csv,
                dashboard_output,
            )
            if args.output != "json":
                print(f"已写入文档: {saved_dashboard}")
        if args.output == "json":
            if args.cmd == "recommend-all":
                print(json.dumps(json_payload, ensure_ascii=False, indent=2))
            else:
                print(json.dumps(json_payload[args.cmd], ensure_ascii=False, indent=2))
        return

    cfg = base_cfg
    strategy_profile = "pullback_confirm" if args.cmd in {"backtest-pullback"} else None
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

    if args.cmd in {"backtest", "backtest-pullback"}:
        runner = BacktestRunner(rec_engine)
        # When emitting JSON, suppress verbose runtime logs and keep only final payload.
        if args.output in {"json", "json-cn"}:
            with contextlib.redirect_stdout(io.StringIO()):
                summary = runner.run(_parse_date(args.start), _parse_date(args.end), count=args.count)
        else:
            summary = runner.run(_parse_date(args.start), _parse_date(args.end), count=args.count)
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

    raise RuntimeError(f"Unsupported command: {args.cmd}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"错误: {friendly_error_message(exc)}", file=sys.stderr)
        sys.exit(1)
