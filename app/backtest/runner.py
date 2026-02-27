from __future__ import annotations

from dataclasses import asdict
from datetime import date
from statistics import mean
from collections import Counter

from app.engine.recommender import Recommender
from app.error_messages import friendly_error_message
from app.models import BacktestRecord


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
        self.backtest_verbose_errors = bool(recommender.cfg.get("backtest", {}).get("verbose_errors", True))
        self.max_error_examples = int(recommender.cfg.get("backtest", {}).get("max_error_examples", 20))

    def run(self, start_date: date, end_date: date) -> dict:
        trade_dates = self.ds.get_trade_dates(start_date, end_date)
        if len(trade_dates) < 8:
            raise RuntimeError("Not enough trade dates for backtest")
        records: list[BacktestRecord] = []
        error_counts: Counter[str] = Counter()
        error_examples: list[dict] = []
        mode_counts: Counter[str] = Counter()
        for dt in trade_dates[:-5]:
            try:
                rec = self.recommender.recommend(dt)
            except Exception as exc:
                key = type(exc).__name__
                error_counts[key] += 1
                zh_msg = friendly_error_message(exc)
                if len(error_examples) < self.max_error_examples:
                    error_examples.append({"trade_date": dt.isoformat(), "error_type": key, "message": zh_msg})
                if self.backtest_verbose_errors:
                    print(f"[backtest][skip] {dt.isoformat()} {key}: {zh_msg}", flush=True)
                continue
            run_meta = self.recommender.get_last_run_meta() or {}
            if self.backtest_verbose_errors:
                signal_date = run_meta.get("signal_date", "unknown")
                normal_scored = run_meta.get("normal_scored", "n/a")
                relaxed_scored = run_meta.get("relaxed_scored", "n/a")
                force_scored = run_meta.get("force_scored", "n/a")
                final_mode = run_meta.get("final_mode", rec.threshold_mode)
                print(
                    "[backtest][day] "
                    f"target={dt.isoformat()} signal={signal_date} "
                    f"normal_scored={normal_scored} relaxed_scored={relaxed_scored} "
                    f"force_scored={force_scored} final_mode={final_mode}",
                    flush=True,
                )
            mode_counts[rec.threshold_mode] += 1
            bars = self.ds.get_daily_bars(rec.symbol, dt, trade_dates[-1])
            close_map = {b.trade_date: b.close for b in bars}
            ret_1d_gross = self._calc_forward_return(close_map, dt, trade_dates, 1)
            ret_5d_gross = self._calc_forward_return(close_map, dt, trade_dates, 5)
            ret_1d_net = self._apply_round_trip_cost(ret_1d_gross)
            ret_5d_net = self._apply_round_trip_cost(ret_5d_gross)
            records.append(
                BacktestRecord(
                    trade_date=dt,
                    symbol=rec.symbol,
                    name=rec.name,
                    threshold_mode=rec.threshold_mode,
                    ret_1d_gross=ret_1d_gross,
                    ret_5d_gross=ret_5d_gross,
                    ret_1d_net=ret_1d_net,
                    ret_5d_net=ret_5d_net,
                )
            )
        return self._summary(records, start_date, end_date, len(trade_dates[:-5]), dict(error_counts), error_examples, dict(mode_counts))

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

    @staticmethod
    def _calc_forward_return(close_map: dict, dt: date, trade_dates: list[date], step: int) -> float | None:
        if dt not in trade_dates:
            return None
        idx = trade_dates.index(dt)
        if idx + step >= len(trade_dates):
            return None
        d1 = dt
        d2 = trade_dates[idx + step]
        c1 = close_map.get(d1)
        c2 = close_map.get(d2)
        if c1 is None or c2 is None or c1 <= 0:
            return None
        return c2 / c1 - 1.0

    @staticmethod
    def _summary(
        records: list[BacktestRecord],
        start_date: date,
        end_date: date,
        attempted_days: int,
        error_counts: dict[str, int],
        error_examples: list[dict],
        mode_counts: dict[str, int],
    ) -> dict:
        one_gross = [r.ret_1d_gross for r in records if r.ret_1d_gross is not None]
        five_gross = [r.ret_5d_gross for r in records if r.ret_5d_gross is not None]
        one_net = [r.ret_1d_net for r in records if r.ret_1d_net is not None]
        five_net = [r.ret_5d_net for r in records if r.ret_5d_net is not None]
        win_rate_gross = sum(1 for x in one_gross if x > 0) / len(one_gross) if one_gross else 0.0
        win_rate_net = sum(1 for x in one_net if x > 0) / len(one_net) if one_net else 0.0
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
            "attempted_days": attempted_days,
            "total_trades": len(records),
            "skipped_days": max(attempted_days - len(records), 0),
            "win_rate_gross_1d": win_rate_gross,
            "win_rate_net_1d": win_rate_net,
            "avg_return_1d_gross": mean(one_gross) if one_gross else 0.0,
            "avg_return_5d_gross": mean(five_gross) if five_gross else 0.0,
            "avg_return_1d_net": mean(one_net) if one_net else 0.0,
            "avg_return_5d_net": mean(five_net) if five_net else 0.0,
            "max_drawdown_proxy": max_dd,
            "threshold_mode_counts": mode_counts,
            "error_counts": error_counts,
            "error_examples": error_examples,
            "records": [asdict(r) for r in records],
        }
