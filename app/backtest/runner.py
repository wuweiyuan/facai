from __future__ import annotations

from dataclasses import asdict
from datetime import date
from statistics import mean
from collections import Counter
import sys

from app.backtest.entry_price import (
    ENTRY_PRICE_CLOSE,
    calc_target_forward_return,
    entry_price_mode_description,
    normalize_entry_price_mode,
)
from app.engine.recommender import Recommender
from app.error_messages import friendly_error_message
from app.models import BacktestRecord

MODE_ZH = {"normal": "常规", "relaxed": "放宽", "force": "强制"}


class BacktestRunner:
    def __init__(self, recommender: Recommender):
        self.recommender = recommender
        self.ds = recommender.data_source
        cfg = recommender.cfg.get("execution_cost", {})
        self.commission_rate = float(cfg.get("commission_rate", 0.0002))
        self.stamp_duty_sell_rate = float(cfg.get("stamp_duty_sell_rate", 0.0005))
        self.slippage_bps = float(cfg.get("slippage_bps", 5.0))
        self.min_commission_per_side = float(cfg.get("min_commission_per_side", 0.0))
        self.enable_cost = bool(cfg.get("enabled", True))
        backtest_cfg = recommender.cfg.get("backtest", {})
        self.backtest_verbose_errors = bool(backtest_cfg.get("verbose_errors", True))
        self.max_error_examples = int(backtest_cfg.get("max_error_examples", 20))
        self.progress_to_stderr = bool(backtest_cfg.get("progress_to_stderr", True))
        self.progress_every_days = max(int(backtest_cfg.get("progress_every_days", 5)), 1)

    def run(
        self,
        start_date: date,
        end_date: date,
        count: int | None = None,
        entry_price_mode: str = ENTRY_PRICE_CLOSE,
    ) -> dict:
        entry_price_mode = normalize_entry_price_mode(entry_price_mode)
        trade_dates = self.ds.get_trade_dates(start_date, end_date)
        if len(trade_dates) < 8:
            raise RuntimeError("Not enough trade dates for backtest")
        attempted_dates = trade_dates[:-5]
        records: list[BacktestRecord] = []
        error_counts: Counter[str] = Counter()
        error_examples: list[dict] = []
        mode_counts: Counter[str] = Counter()
        if self.progress_to_stderr:
            print(
                f"[回测] 开始: {start_date.isoformat()} -> {end_date.isoformat()} "
                f"计划交易日={len(attempted_dates)}",
                file=sys.stderr,
                flush=True,
            )
        for idx, dt in enumerate(attempted_dates, start=1):
            try:
                recs = self.recommender.recommend_many(dt, count=count)
            except Exception as exc:
                key = type(exc).__name__
                error_counts[key] += 1
                zh_msg = friendly_error_message(exc)
                if len(error_examples) < self.max_error_examples:
                    error_examples.append({"trade_date": dt.isoformat(), "error_type": key, "message": zh_msg})
                if self.backtest_verbose_errors:
                    print(f"[回测][跳过] {dt.isoformat()} {key}: {zh_msg}", flush=True)
                self._print_progress(idx, len(attempted_dates), len(records), error_counts)
                continue
            run_meta = self.recommender.get_last_run_meta() or {}
            if self.backtest_verbose_errors:
                signal_date = run_meta.get("signal_date", "unknown")
                normal_scored = run_meta.get("normal_scored", "n/a")
                relaxed_scored = run_meta.get("relaxed_scored", "n/a")
                force_scored = run_meta.get("force_scored", "n/a")
                selected_count = run_meta.get("selected_count", len(recs))
                final_mode = run_meta.get("final_mode", recs[0].threshold_mode)
                print(
                    "[回测][日度] "
                    f"目标日={dt.isoformat()} 信号日={signal_date} "
                    f"常规模式入选={normal_scored} 放宽模式入选={relaxed_scored} "
                    f"强制模式入选={force_scored} 最终入选={selected_count} "
                    f"最终模式={MODE_ZH.get(final_mode, final_mode)}",
                    flush=True,
                )
            mode_counts[recs[0].threshold_mode] += 1
            symbol_name_pairs = [(r.symbol, r.name) for r in recs]
            bar_maps: dict[str, dict] = {}
            for symbol, _name in symbol_name_pairs:
                bars = self.ds.get_daily_bars(symbol, dt, trade_dates[-1])
                bar_maps[symbol] = {b.trade_date: b for b in bars}
            ret_1d_gross = self._calc_basket_forward_return(bar_maps, dt, trade_dates, 1, entry_price_mode)
            ret_3d_gross = self._calc_basket_forward_return(bar_maps, dt, trade_dates, 3, entry_price_mode)
            ret_5d_gross = self._calc_basket_forward_return(bar_maps, dt, trade_dates, 5, entry_price_mode)
            ret_1d_net = self._apply_round_trip_cost(ret_1d_gross)
            ret_3d_net = self._apply_round_trip_cost(ret_3d_gross)
            ret_5d_net = self._apply_round_trip_cost(ret_5d_gross)
            symbols = "+".join(s for s, _ in symbol_name_pairs)
            names = "+".join(n for _, n in symbol_name_pairs)
            records.append(
                BacktestRecord(
                    trade_date=dt,
                    symbol=symbols,
                    name=names,
                    threshold_mode=recs[0].threshold_mode,
                    ret_1d_gross=ret_1d_gross,
                    ret_3d_gross=ret_3d_gross,
                    ret_5d_gross=ret_5d_gross,
                    ret_1d_net=ret_1d_net,
                    ret_3d_net=ret_3d_net,
                    ret_5d_net=ret_5d_net,
                )
            )
            self._print_progress(idx, len(attempted_dates), len(records), error_counts)
        if self.progress_to_stderr:
            print(
                f"[回测] 完成: 已处理={len(attempted_dates)} 成交={len(records)} "
                f"跳过={max(len(attempted_dates) - len(records), 0)}",
                file=sys.stderr,
                flush=True,
            )
        return self._summary(
            records,
            start_date,
            end_date,
            len(attempted_dates),
            dict(error_counts),
            error_examples,
            dict(mode_counts),
            entry_price_mode,
        )

    def _print_progress(self, completed: int, total: int, trades: int, error_counts: Counter[str]) -> None:
        if not self.progress_to_stderr:
            return
        if completed % self.progress_every_days != 0 and completed != total:
            return
        skipped = max(completed - trades, 0)
        errors = sum(error_counts.values())
        print(
            f"[回测] 进度 {completed}/{total} 成交={trades} 跳过={skipped} 错误={errors}",
            file=sys.stderr,
            flush=True,
        )

    def _apply_round_trip_cost(self, gross_ret: float | None) -> float | None:
        if gross_ret is None:
            return None
        if not self.enable_cost:
            return gross_ret
        slip = self.slippage_bps / 10000.0
        buy_slip_factor = 1.0 + slip
        sell_slip_factor = 1.0 - slip
        buy_fee_rate = self.commission_rate
        sell_fee_rate = self.commission_rate + self.stamp_duty_sell_rate

        # Approximate min commission with a notional 1.0 base.
        buy_fee = max(buy_fee_rate, self.min_commission_per_side)
        sell_fee = max(sell_fee_rate, self.min_commission_per_side)
        gross_factor = 1.0 + gross_ret
        if gross_factor <= 0:
            return -1.0
        net_factor = gross_factor * sell_slip_factor * (1.0 - sell_fee) / (buy_slip_factor * (1.0 + buy_fee))
        return net_factor - 1.0

    @classmethod
    def _calc_basket_forward_return(
        cls,
        bar_maps: dict[str, dict],
        dt: date,
        trade_dates: list[date],
        step: int,
        entry_price_mode: str,
    ) -> float | None:
        rets = []
        for bar_map in bar_maps.values():
            r = calc_target_forward_return(bar_map, dt, trade_dates, step, entry_price_mode)
            if r is not None:
                rets.append(r)
        if not rets:
            return None
        return mean(rets)

    @staticmethod
    def _summary(
        records: list[BacktestRecord],
        start_date: date,
        end_date: date,
        attempted_days: int,
        error_counts: dict[str, int],
        error_examples: list[dict],
        mode_counts: dict[str, int],
        entry_price_mode: str,
    ) -> dict:
        one_gross = [r.ret_1d_gross for r in records if r.ret_1d_gross is not None]
        three_gross = [r.ret_3d_gross for r in records if r.ret_3d_gross is not None]
        five_gross = [r.ret_5d_gross for r in records if r.ret_5d_gross is not None]
        one_net = [r.ret_1d_net for r in records if r.ret_1d_net is not None]
        three_net = [r.ret_3d_net for r in records if r.ret_3d_net is not None]
        five_net = [r.ret_5d_net for r in records if r.ret_5d_net is not None]
        win_rate_gross_1d = sum(1 for x in one_gross if x > 0) / len(one_gross) if one_gross else 0.0
        win_rate_gross_3d = sum(1 for x in three_gross if x > 0) / len(three_gross) if three_gross else 0.0
        win_rate_net_1d = sum(1 for x in one_net if x > 0) / len(one_net) if one_net else 0.0
        win_rate_net_3d = sum(1 for x in three_net if x > 0) / len(three_net) if three_net else 0.0
        equity = 1.0
        curve = []
        for v in one_net:
            equity *= 1 + v
            curve.append(equity)
        peak = 1.0
        max_dd = 0.0
        for v in curve:
            peak = max(peak, v)
            dd = (peak - v) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        return {
            "period": f"{start_date.isoformat()} -> {end_date.isoformat()}",
            "entry_price_mode": entry_price_mode,
            "entry_price_desc": entry_price_mode_description(entry_price_mode),
            "attempted_days": attempted_days,
            "total_trades": len(records),
            "skipped_days": max(attempted_days - len(records), 0),
            "win_rate_gross_1d": win_rate_gross_1d,
            "win_rate_gross_3d": win_rate_gross_3d,
            "win_rate_net_1d": win_rate_net_1d,
            "win_rate_net_3d": win_rate_net_3d,
            "avg_return_1d_gross": mean(one_gross) if one_gross else 0.0,
            "avg_return_3d_gross": mean(three_gross) if three_gross else 0.0,
            "avg_return_5d_gross": mean(five_gross) if five_gross else 0.0,
            "avg_return_1d_net": mean(one_net) if one_net else 0.0,
            "avg_return_3d_net": mean(three_net) if three_net else 0.0,
            "avg_return_5d_net": mean(five_net) if five_net else 0.0,
            "max_drawdown_proxy": max_dd,
            "threshold_mode_counts": mode_counts,
            "error_counts": error_counts,
            "error_examples": error_examples,
            "records": [asdict(r) for r in records],
        }
