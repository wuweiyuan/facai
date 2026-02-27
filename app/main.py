from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

from app.backtest.runner import BacktestRunner
from app.config import load_config
from app.data_source.akshare_client import AkshareDataSource
from app.doctor import print_doctor_report, run_doctor
from app.engine.recommender import Recommender
from app.error_messages import friendly_error_message
from app.network import clear_proxy_env, disable_requests_env_proxy, force_no_proxy_all
from app.reporting import append_recommendation_csv, append_recommendation_md, append_recommendation_txt


def _parse_date(v: str | None) -> date:
    if not v:
        return date.today()
    return datetime.strptime(v, "%Y-%m-%d").date()


def _print_recommendation(rec, output: str) -> None:
    if output == "json":
        print(json.dumps(rec.as_dict(), ensure_ascii=False, indent=2))
        return
    print(f"交易日: {rec.trade_date.isoformat()}  阈值模式: {rec.threshold_mode}")
    print(f"推荐: {rec.symbol} {rec.name}")
    print(f"总分: {rec.score_total:.2f}")
    print("关键指标:")
    for k, v in rec.key_metrics.items():
        print(f"  - {k}: {v:.4f}")
    print("推荐理由:")
    for idx, r in enumerate(rec.reason, start=1):
        print(f"  {idx}. {r}")


def _print_backtest(summary: dict, output: str) -> None:
    if output == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return
    print(f"回测区间: {summary['period']}")
    print(f"尝试交易日: {summary.get('attempted_days', 0)}")
    print(f"跳过交易日: {summary.get('skipped_days', 0)}")
    print(f"交易次数: {summary['total_trades']}")
    print(f"1日胜率(毛): {summary['win_rate_gross_1d']:.2%}")
    print(f"1日胜率(净): {summary['win_rate_net_1d']:.2%}")
    print(f"平均1日收益(毛): {summary['avg_return_1d_gross']:.4%}")
    print(f"平均1日收益(净): {summary['avg_return_1d_net']:.4%}")
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

    p_rec = sub.add_parser("recommend", help="Recommend one stock for target trading day")
    p_rec.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    p_rec.add_argument("--output", choices=["table", "json"], default="table")

    p_exp = sub.add_parser("explain", help="Explain one stock score on target date")
    p_exp.add_argument("--symbol", required=True, help="Stock code like 000001")
    p_exp.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    p_exp.add_argument("--mode", choices=["normal", "relaxed", "force"], default="normal")
    p_exp.add_argument("--output", choices=["table", "json"], default="table")

    p_bt = sub.add_parser("backtest", help="Backtest over period")
    p_bt.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_bt.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_bt.add_argument("--output", choices=["table", "json"], default="table")

    p_doc = sub.add_parser("doctor", help="Run connectivity diagnostics for data sources")
    p_doc.add_argument("--output", choices=["table", "json"], default="table")

    p_ck = sub.add_parser("check-kline", help="Check single symbol kline fetch in date range")
    p_ck.add_argument("--symbol", required=True, help="Stock code like 000001")
    p_ck.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_ck.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_ck.add_argument("--output", choices=["table", "json"], default="table")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
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
    rec_engine = Recommender(ds, cfg)

    if args.cmd == "recommend":
        rec = rec_engine.recommend(_parse_date(args.date))
        _print_recommendation(rec, args.output)
        report_cfg = cfg.get("reporting", {})
        if bool(report_cfg.get("enabled", True)):
            saved = append_recommendation_csv(rec, str(report_cfg.get("recommendation_csv", "reports/recommendations.csv")))
            saved_md = append_recommendation_md(rec, str(report_cfg.get("recommendation_md", "reports/recommendations.md")))
            saved_txt = append_recommendation_txt(rec, str(report_cfg.get("recommendation_txt", "reports/recommendations.txt")))
            print(f"已写入文档: {saved}")
            print(f"已写入文档: {saved_md}")
            print(f"已写入文档: {saved_txt}")
        return

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

    if args.cmd == "backtest":
        runner = BacktestRunner(rec_engine)
        summary = runner.run(_parse_date(args.start), _parse_date(args.end))
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
