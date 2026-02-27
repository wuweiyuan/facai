from __future__ import annotations

from datetime import date, timedelta
import time

from app.data_source.base import MarketDataSource
from app.error_messages import friendly_error_message
from app.features.indicators import add_indicators, bars_to_df
from app.models import CandidateScore, RecommendationResult
from app.strategy.holding_period import suggest_holding_days
from app.strategy.regime_risk import MarketState, detect_market_state, passes_risk_filter
from app.strategy.risk_targets import compute_stop_take_prices
from app.strategy.scoring import build_reason, compute_score, passes_threshold
from app.universe.filtering import filter_universe

MODE_ZH = {"normal": "常规", "relaxed": "放宽", "force": "强制"}


class Recommender:
    def __init__(self, data_source: MarketDataSource, cfg: dict):
        self.data_source = data_source
        self.cfg = cfg
        self._stock_name_map: dict[str, str] | None = None
        self._last_run_meta: dict | None = None

    def get_last_run_meta(self) -> dict | None:
        return self._last_run_meta

    def resolve_signal_date(self, target_date: date) -> date:
        start = target_date - timedelta(days=30)
        dates = self.data_source.get_trade_dates(start, target_date)
        if len(dates) < 2:
            raise RuntimeError("No enough trade dates to resolve T-1 signal date")
        if dates[-1] == target_date:
            return dates[-2]
        return dates[-1]

    def recommend(self, target_date: date) -> RecommendationResult:
        t0 = time.time()
        signal_date = self.resolve_signal_date(target_date)
        fresh_ok, freshness_msg = self._check_signal_data_freshness(signal_date)
        if not fresh_ok:
            print(f"[警告] {freshness_msg}", flush=True)
            if bool(self.cfg.get("data_freshness", {}).get("stop_on_stale", True)):
                raise RuntimeError(f"数据未更新，已停止执行: {freshness_msg}")
        market_state, market_reason = self._resolve_market_state(signal_date)
        stocks = self.data_source.get_stock_list()
        stocks_total = len(stocks)
        universe = filter_universe(stocks, self.cfg, signal_date)
        filtered_total = len(universe)
        max_symbols = int(self.cfg.get("strategy", {}).get("max_symbols_per_run", 0))
        if max_symbols > 0:
            universe = universe[:max_symbols]
        market_label_zh = {"bull": "牛市", "bear": "熊市", "neutral": "震荡", "unknown": "未知"}.get(
            market_state.label, market_state.label
        )
        print(
            f"[推荐] 信号日={signal_date} 股票总数={stocks_total} "
            f"过滤后={filtered_total} 实际扫描={len(universe)} "
            f"市场={market_label_zh}(mom20={market_state.mom20:.2%}) 原因={market_reason}",
            flush=True,
        )
        enabled_modes = self._resolve_enabled_modes()
        stats_by_mode: dict[str, dict] = {}
        candidates: list[CandidateScore] = []
        mode = enabled_modes[0]
        for m in enabled_modes:
            candidates, mode_stats = self._rank_candidates(universe, signal_date, mode=m, market_state=market_state)
            stats_by_mode[m] = mode_stats
            mode = m
            if candidates:
                break
        if not candidates:
            raise RuntimeError(f"No candidate found in enabled modes: {','.join(enabled_modes)}")
        for m in enabled_modes:
            mode_stats = stats_by_mode.get(m)
            if mode_stats is None:
                continue
            self._print_mode_stats(m, mode_stats)
        top = candidates[0]
        self._last_run_meta = {
            "target_date": target_date.isoformat(),
            "signal_date": signal_date.isoformat(),
            "final_mode": mode,
            "enabled_modes": enabled_modes,
            "normal_scored": int(stats_by_mode.get("normal", {}).get("scored", 0)) if "normal" in enabled_modes else None,
            "relaxed_scored": int(stats_by_mode.get("relaxed", {}).get("scored", 0)) if "relaxed" in enabled_modes else None,
            "force_scored": int(stats_by_mode.get("force", {}).get("scored", 0)) if "force" in enabled_modes else None,
        }
        print(
            f"[推荐] 完成，用时 {time.time() - t0:.1f}s，候选数={len(candidates)}，最终模式={MODE_ZH.get(mode, mode)}",
            flush=True,
        )
        return RecommendationResult(
            trade_date=target_date,
            symbol=top.symbol,
            name=top.name,
            score_total=top.score_total,
            score_breakdown=top.score_breakdown,
            key_metrics=top.key_metrics,
            reason=top.reason,
            threshold_mode=mode,
        )

    def _resolve_enabled_modes(self) -> list[str]:
        cfg_modes = self.cfg.get("strategy", {}).get("enabled_modes", ["normal", "relaxed", "force"])
        allowed = {"normal", "relaxed", "force"}
        if not isinstance(cfg_modes, list):
            return ["normal", "relaxed", "force"]
        out: list[str] = []
        for m in cfg_modes:
            mode = str(m).strip().lower()
            if mode in allowed and mode not in out:
                out.append(mode)
        return out or ["normal", "relaxed", "force"]

    def _check_signal_data_freshness(self, signal_date: date) -> tuple[bool, str]:
        cfg = self.cfg.get("data_freshness", {})
        if not bool(cfg.get("enabled", True)):
            return True, "disabled"
        probe_symbol = str(cfg.get("probe_symbol", "000001"))
        lookback_days = int(cfg.get("probe_lookback_days", 10))
        start = signal_date - timedelta(days=max(lookback_days, 3))
        try:
            bars = self.data_source.get_daily_bars(probe_symbol, start, signal_date)
        except Exception as exc:
            return False, f"无法确认数据更新状态: {type(exc).__name__}"
        if not bars:
            return False, f"未获取到探针股票 {probe_symbol} 的日线，可能数据源未更新"
        last_date = max(b.trade_date for b in bars)
        if last_date < signal_date:
            return (
                False,
                f"信号日 {signal_date} 可能未更新，当前探针股票 {probe_symbol} 最新仅到 {last_date}；"
                "本次 candidates=0 可能由数据未落地导致",
            )
        return True, "ok"

    def explain(self, symbol: str, target_date: date, mode: str = "normal") -> CandidateScore:
        signal_date = self.resolve_signal_date(target_date)
        market_state, _ = self._resolve_market_state(signal_date)
        bars = self._fetch_recent_bars(symbol, signal_date)
        df = add_indicators(bars_to_df(bars))
        if df.empty:
            raise RuntimeError(f"No bars found for {symbol}")
        latest = df.iloc[-1]
        if not passes_threshold(latest, mode):
            raise RuntimeError(f"{symbol} does not pass {mode} threshold")
        if not passes_risk_filter(latest, market_state, mode, self.cfg):
            raise RuntimeError(f"{symbol} does not pass risk filter in {mode} mode")
        total, breakdown = compute_score(latest, self.cfg)
        return CandidateScore(
            symbol=symbol,
            name=self._resolve_stock_name(symbol),
            score_total=total,
            score_breakdown=breakdown,
            key_metrics=self._build_metrics(latest, market_state),
            reason=build_reason(latest, breakdown, mode),
        )

    def _resolve_stock_name(self, symbol: str) -> str:
        if self._stock_name_map is None:
            self._stock_name_map = {}
            try:
                for stock in self.data_source.get_stock_list():
                    self._stock_name_map[stock.symbol] = stock.name
            except Exception:
                # Keep explain resilient even when stock list API is unavailable.
                self._stock_name_map = {}
        return self._stock_name_map.get(symbol, symbol)

    def _rank_candidates(
        self,
        universe,
        signal_date: date,
        mode: str,
        market_state: MarketState,
    ) -> tuple[list[CandidateScore], dict]:
        out: list[CandidateScore] = []
        total_symbols = len(universe)
        progress_every = int(self.cfg.get("strategy", {}).get("progress_every", 10))
        stats = {
            "scanned": total_symbols,
            "kline_success": 0,
            "kline_failed": 0,
            "kline_failed_examples": [],
            "no_bars": 0,
            "no_bars_symbols": [],
            "insufficient_bars": 0,
            "df_empty": 0,
            "threshold_reject": 0,
            "risk_reject": 0,
            "market_reject": 0,
            "scored": 0,
        }
        for idx, stock in enumerate(universe, start=1):
            try:
                bars = self._fetch_recent_bars(stock.symbol, signal_date)
            except Exception as exc:
                stats["kline_failed"] += 1
                if len(stats["kline_failed_examples"]) < int(
                    self.cfg.get("strategy", {}).get("failed_symbol_examples", 20)
                ):
                    stats["kline_failed_examples"].append(
                        {"symbol": stock.symbol, "reason": friendly_error_message(exc)}
                    )
                if progress_every > 0 and (idx % progress_every == 0 or idx == total_symbols):
                    print(f"[{MODE_ZH.get(mode, mode)}] 已扫描 {idx}/{total_symbols}，候选={len(out)}", flush=True)
                continue
            if not bars:
                stats["no_bars"] += 1
                if len(stats["no_bars_symbols"]) < int(self.cfg.get("strategy", {}).get("failed_symbol_examples", 20)):
                    stats["no_bars_symbols"].append(stock.symbol)
                if progress_every > 0 and (idx % progress_every == 0 or idx == total_symbols):
                    print(f"[{MODE_ZH.get(mode, mode)}] 已扫描 {idx}/{total_symbols}，候选={len(out)}", flush=True)
                continue
            stats["kline_success"] += 1
            min_bars = 70 if mode != "force" else 30
            if len(bars) < min_bars:
                stats["insufficient_bars"] += 1
                if progress_every > 0 and (idx % progress_every == 0 or idx == total_symbols):
                    print(f"[{MODE_ZH.get(mode, mode)}] 已扫描 {idx}/{total_symbols}，候选={len(out)}", flush=True)
                continue
            df = add_indicators(bars_to_df(bars))
            if df.empty:
                stats["df_empty"] += 1
                if progress_every > 0 and (idx % progress_every == 0 or idx == total_symbols):
                    print(f"[{MODE_ZH.get(mode, mode)}] 已扫描 {idx}/{total_symbols}，候选={len(out)}", flush=True)
                continue
            latest = df.iloc[-1]
            if mode != "force" and not passes_threshold(latest, mode):
                stats["threshold_reject"] += 1
                if progress_every > 0 and (idx % progress_every == 0 or idx == total_symbols):
                    print(f"[{MODE_ZH.get(mode, mode)}] 已扫描 {idx}/{total_symbols}，候选={len(out)}", flush=True)
                continue
            if not passes_risk_filter(latest, market_state, mode, self.cfg):
                market_enabled = bool(self.cfg.get("market_filter", {}).get("enabled", True))
                if mode != "force" and market_enabled and market_state.label == "bear":
                    stats["market_reject"] += 1
                else:
                    stats["risk_reject"] += 1
                if progress_every > 0 and (idx % progress_every == 0 or idx == total_symbols):
                    print(f"[{MODE_ZH.get(mode, mode)}] 已扫描 {idx}/{total_symbols}，候选={len(out)}", flush=True)
                continue
            score_total, breakdown = compute_score(latest, self.cfg)
            out.append(
                CandidateScore(
                    symbol=stock.symbol,
                    name=stock.name,
                    score_total=score_total,
                    score_breakdown=breakdown,
                    key_metrics=self._build_metrics(latest, market_state),
                    reason=build_reason(latest, breakdown, mode),
                )
            )
            stats["scored"] += 1
            if progress_every > 0 and (idx % progress_every == 0 or idx == total_symbols):
                print(f"[{MODE_ZH.get(mode, mode)}] 已扫描 {idx}/{total_symbols}，候选={len(out)}", flush=True)
        out.sort(key=lambda x: x.score_total, reverse=True)
        return out, stats

    @staticmethod
    def _print_mode_stats(mode: str, stats: dict) -> None:
        print(
            f"[{MODE_ZH.get(mode, mode)}][统计] 总扫描={stats['scanned']} "
            f"K线成功={stats['kline_success']} K线失败={stats['kline_failed']} "
            f"无K线={stats['no_bars']} 历史不足={stats['insufficient_bars']} "
            f"指标空表={stats['df_empty']} 阈值淘汰={stats['threshold_reject']} "
            f"风控淘汰={stats['risk_reject']} 市场淘汰={stats['market_reject']} "
            f"入选={stats['scored']}",
            flush=True,
        )
        failed_examples = stats.get("kline_failed_examples", [])
        if failed_examples:
            preview = "; ".join([f"{x['symbol']}: {x['reason']}" for x in failed_examples[:5]])
            print(f"[{MODE_ZH.get(mode, mode)}][K线失败示例] {preview}", flush=True)
        no_bars_symbols = stats.get("no_bars_symbols", [])
        if no_bars_symbols:
            preview = ", ".join(no_bars_symbols[:10])
            print(f"[{MODE_ZH.get(mode, mode)}][无K线代码] {preview}", flush=True)

    def _resolve_market_state(self, signal_date: date) -> tuple[MarketState, str]:
        mcfg = self.cfg.get("market_filter", {})
        if not bool(mcfg.get("enabled", True)):
            return MarketState(label="unknown", close=0.0, ma20=0.0, ma60=0.0, mom20=0.0), "market_filter_disabled"
        index_symbol = str(mcfg.get("index_symbol", "000300"))
        lookback = int(mcfg.get("lookback_days", 120))
        start = signal_date - timedelta(days=max(lookback * 2, 180))
        try:
            closes = self.data_source.get_index_closes(index_symbol, start, signal_date)
            if not closes:
                return MarketState(label="unknown", close=0.0, ma20=0.0, ma60=0.0, mom20=0.0), "index_closes_empty"
            st = detect_market_state(closes, signal_date, self.cfg)
            return st, "ok"
        except Exception as exc:
            return MarketState(label="unknown", close=0.0, ma20=0.0, ma60=0.0, mom20=0.0), f"index_error:{type(exc).__name__}"

    def _fetch_recent_bars(self, symbol: str, signal_date: date):
        start = signal_date - timedelta(days=220)
        bars = self.data_source.get_daily_bars(symbol, start, signal_date)
        return [b for b in bars if b.trade_date <= signal_date]

    def _build_metrics(self, latest, market_state: MarketState) -> dict[str, float]:
        close = float(latest["close"])
        atr14 = float(latest["atr14"]) if latest.get("atr14") is not None else 0.0
        stop_loss_price, take_profit_price = compute_stop_take_prices(close, atr14, self.cfg)
        suggested_days = float(suggest_holding_days(latest, market_state))
        return {
            "close": close,
            "ma20": float(latest["ma20"]),
            "ma60": float(latest["ma60"]),
            "mom5": float(latest["mom5"]),
            "mom20": float(latest["mom20"]),
            "rsi14": float(latest["rsi14"]),
            "atr14": atr14,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "suggested_holding_days": suggested_days,
            "vol_ratio_5_20": float(latest["vol_ratio_5_20"]) if latest.get("vol_ratio_5_20") is not None else 0.0,
            "volume_zscore20": float(latest["volume_zscore20"]) if latest.get("volume_zscore20") is not None else 0.0,
            "turnover_rate": float(latest["turnover_rate"]) if latest.get("turnover_rate") is not None else 0.0,
            "vol20_std": float(latest["vol20_std"]),
        }
