from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
import time

from app.data_source.base import MarketDataSource
from app.error_messages import friendly_error_message
from app.features.indicators import add_indicators, bars_to_df
from app.models import CandidateScore, RecommendationResult, StockInfo
from app.sector_strength import build_sector_metrics_from_cache, load_symbol_sector_map, should_use_sector_metrics
from app.strategy.holding_period import build_exit_plan, suggest_holding_days
from app.strategy.regime_risk import MarketState, detect_market_state, passes_risk_filter
from app.strategy.risk_targets import compute_stop_take_prices
from app.strategy.scoring import build_reason, compute_score, passes_threshold
from app.universe.filtering import filter_universe

MODE_ZH = {"normal": "常规", "relaxed": "放宽", "force": "强制"}


@dataclass
class StockScanOutcome:
    index: int
    symbol: str
    status: str
    kline_success: bool = False
    candidate: CandidateScore | None = None
    error_message: str | None = None
    warning_message: str | None = None


class Recommender:
    def __init__(self, data_source: MarketDataSource, cfg: dict):
        self.data_source = data_source
        self.cfg = cfg
        self._stock_name_map: dict[str, str] | None = None
        self._last_run_meta: dict | None = None
        self._sector_symbol_map: dict[str, str] | None = None
        self._sector_metrics_cache: dict[tuple[date, str], dict[str, float]] | None = None
        self._sector_metrics_range: tuple[date, date] | None = None

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
        return self.recommend_many(target_date, count=1)[0]

    def recommend_many(self, target_date: date, count: int | None = None) -> list[RecommendationResult]:
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
        scan_workers = self._resolve_scan_workers()
        market_label_zh = {"bull": "牛市", "bear": "熊市", "neutral": "震荡", "unknown": "未知"}.get(
            market_state.label, market_state.label
        )
        print(
            f"[推荐] 信号日={signal_date} 股票总数={stocks_total} "
            f"过滤后={filtered_total} 实际扫描={len(universe)} "
            f"市场={market_label_zh}(mom20={market_state.mom20:.2%}) 原因={market_reason} "
            f"并发={scan_workers}",
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
        pick_count = self._resolve_pick_count(count)
        selected = candidates[:pick_count]
        self._last_run_meta = {
            "target_date": target_date.isoformat(),
            "signal_date": signal_date.isoformat(),
            "final_mode": mode,
            "enabled_modes": enabled_modes,
            "selected_count": len(selected),
            "available_candidates": len(candidates),
            "scan_workers": scan_workers,
            "normal_scored": int(stats_by_mode.get("normal", {}).get("scored", 0)) if "normal" in enabled_modes else None,
            "relaxed_scored": int(stats_by_mode.get("relaxed", {}).get("scored", 0)) if "relaxed" in enabled_modes else None,
            "force_scored": int(stats_by_mode.get("force", {}).get("scored", 0)) if "force" in enabled_modes else None,
        }
        print(
            f"[推荐] 完成，用时 {time.time() - t0:.1f}s，候选数={len(candidates)}，选中={len(selected)}，最终模式={MODE_ZH.get(mode, mode)}",
            flush=True,
        )
        return [
            RecommendationResult(
                trade_date=target_date,
                symbol=item.symbol,
                name=item.name,
                score_total=item.score_total,
                score_breakdown=item.score_breakdown,
                key_metrics=item.key_metrics,
                reason=item.reason,
                threshold_mode=mode,
            )
            for item in selected
        ]

    def _resolve_pick_count(self, count: int | None) -> int:
        if count is not None:
            return max(int(count), 1)
        return max(int(self.cfg.get("strategy", {}).get("pick_count", 1)), 1)

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

    def _resolve_scan_workers(self) -> int:
        raw = self.cfg.get("strategy", {}).get("scan_workers", 1)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return 1

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
        latest = df.iloc[-1].copy()
        latest["market_mom20"] = market_state.mom20
        latest = self._enrich_sector_metrics(latest, symbol, signal_date)
        if not passes_threshold(latest, mode, self.cfg):
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
            reason=build_reason(latest, breakdown, mode, self.cfg),
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

    def _enrich_sector_metrics(self, latest, symbol: str, signal_date: date):
        if not should_use_sector_metrics(self.cfg):
            return latest
        if self._sector_symbol_map is None:
            self._sector_symbol_map = load_symbol_sector_map(self.cfg)
        if not self._sector_symbol_map:
            return latest
        sector = self._sector_symbol_map.get(symbol)
        if not sector:
            return latest
        cache_dir = str(self.cfg.get("data_source", {}).get("cache_dir", ".cache/akshare"))
        required_range = (signal_date - timedelta(days=220), signal_date)
        if self._sector_metrics_cache is None or self._sector_metrics_range != required_range:
            self._sector_metrics_cache = build_sector_metrics_from_cache(
                cache_dir=cache_dir,
                symbol_sector_map=self._sector_symbol_map,
                start_date=required_range[0],
                end_date=required_range[1],
            )
            self._sector_metrics_range = required_range
        sector_metrics = (self._sector_metrics_cache or {}).get((signal_date, sector))
        if not sector_metrics:
            return latest
        latest["sector_mom20"] = sector_metrics.get("sector_mom20", 0.0)
        latest["sector_mom5"] = sector_metrics.get("sector_mom5", 0.0)
        latest["mom20_excess_vs_sector"] = float(latest.get("mom20", 0.0)) - float(latest["sector_mom20"])
        return latest

    def _rank_candidates(
        self,
        universe,
        signal_date: date,
        mode: str,
        market_state: MarketState,
    ) -> tuple[list[CandidateScore], dict]:
        ranked: list[tuple[int, CandidateScore]] = []
        total_symbols = len(universe)
        progress_every = int(self.cfg.get("strategy", {}).get("progress_every", 10))
        scan_workers = min(self._resolve_scan_workers(), max(total_symbols, 1))
        data_fresh_cfg = self.cfg.get("data_freshness", {}) if isinstance(self.cfg.get("data_freshness", {}), dict) else {}
        # Per-stock staleness often means suspension/停牌; don't abort unless explicitly configured.
        stock_stop_on_stale = bool(data_fresh_cfg.get("stop_on_stale_stock", False))
        suspend_days = int(data_fresh_cfg.get("stock_stale_suspend_days", 5))
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
        completed = 0
        if scan_workers <= 1 or total_symbols <= 1:
            for idx, stock in enumerate(universe, start=1):
                outcome = self._scan_stock(
                    stock=stock,
                    index=idx,
                    signal_date=signal_date,
                    mode=mode,
                    market_state=market_state,
                    stock_stop_on_stale=stock_stop_on_stale,
                    suspend_days=suspend_days,
                )
                self._apply_scan_outcome(outcome, stats, ranked)
                completed += 1
                self._print_scan_progress(mode, completed, total_symbols, len(ranked), progress_every)
        else:
            with ThreadPoolExecutor(max_workers=scan_workers, thread_name_prefix="recommend-scan") as executor:
                futures = {
                    executor.submit(
                        self._scan_stock,
                        stock=stock,
                        index=idx,
                        signal_date=signal_date,
                        mode=mode,
                        market_state=market_state,
                        stock_stop_on_stale=stock_stop_on_stale,
                        suspend_days=suspend_days,
                    ): idx
                    for idx, stock in enumerate(universe, start=1)
                }
                for future in as_completed(futures):
                    outcome = future.result()
                    self._apply_scan_outcome(outcome, stats, ranked)
                    completed += 1
                    self._print_scan_progress(mode, completed, total_symbols, len(ranked), progress_every)
        ranked.sort(key=lambda item: (-item[1].score_total, item[0]))
        return [item[1] for item in ranked], stats

    def _scan_stock(
        self,
        *,
        stock: StockInfo,
        index: int,
        signal_date: date,
        mode: str,
        market_state: MarketState,
        stock_stop_on_stale: bool,
        suspend_days: int,
    ) -> StockScanOutcome:
        try:
            bars = self._fetch_recent_bars(stock.symbol, signal_date)
        except Exception as exc:
            return StockScanOutcome(
                index=index,
                symbol=stock.symbol,
                status="kline_failed",
                error_message=friendly_error_message(exc),
            )
        if not bars:
            return StockScanOutcome(index=index, symbol=stock.symbol, status="no_bars")
        latest_stock_date = max(b.trade_date for b in bars)
        if latest_stock_date < signal_date:
            stale_msg = f"Stock data stale: symbol={stock.symbol}, signal_date={signal_date}, latest={latest_stock_date}"
            stale_days = (signal_date - latest_stock_date).days
            treat_as_suspended = suspend_days > 0 and stale_days >= suspend_days
            if stock_stop_on_stale and not treat_as_suspended:
                return StockScanOutcome(index=index, symbol=stock.symbol, status="fatal_error", error_message=stale_msg)
            warning = (
                f"[警告] {stale_msg}（可能停牌，已跳过）"
                if treat_as_suspended
                else f"[警告] {stale_msg}（数据滞后，已跳过）"
            )
            return StockScanOutcome(index=index, symbol=stock.symbol, status="stale_skip", warning_message=warning)
        min_bars = 70 if mode != "force" else 30
        if len(bars) < min_bars:
            return StockScanOutcome(index=index, symbol=stock.symbol, status="insufficient_bars", kline_success=True)
        df = add_indicators(bars_to_df(bars))
        if df.empty:
            return StockScanOutcome(index=index, symbol=stock.symbol, status="df_empty", kline_success=True)
        latest = df.iloc[-1].copy()
        latest["market_mom20"] = market_state.mom20
        latest = self._enrich_sector_metrics(latest, stock.symbol, signal_date)
        if mode != "force" and not passes_threshold(latest, mode, self.cfg):
            return StockScanOutcome(index=index, symbol=stock.symbol, status="threshold_reject", kline_success=True)
        if not passes_risk_filter(latest, market_state, mode, self.cfg):
            market_enabled = bool(self.cfg.get("market_filter", {}).get("enabled", True))
            status = "market_reject" if mode != "force" and market_enabled and market_state.label == "bear" else "risk_reject"
            return StockScanOutcome(index=index, symbol=stock.symbol, status=status, kline_success=True)
        score_total, breakdown = compute_score(latest, self.cfg)
        return StockScanOutcome(
            index=index,
            symbol=stock.symbol,
            status="candidate",
            kline_success=True,
            candidate=CandidateScore(
                symbol=stock.symbol,
                name=stock.name,
                score_total=score_total,
                score_breakdown=breakdown,
                key_metrics=self._build_metrics(latest, market_state),
                reason=build_reason(latest, breakdown, mode, self.cfg),
            ),
        )

    def _apply_scan_outcome(
        self,
        outcome: StockScanOutcome,
        stats: dict,
        ranked: list[tuple[int, CandidateScore]],
    ) -> None:
        if outcome.warning_message:
            print(outcome.warning_message, flush=True)
        if outcome.status == "fatal_error":
            raise RuntimeError(outcome.error_message or f"Stock scan failed: {outcome.symbol}")
        if outcome.kline_success:
            stats["kline_success"] += 1
        if outcome.status == "kline_failed":
            stats["kline_failed"] += 1
            if len(stats["kline_failed_examples"]) < int(self.cfg.get("strategy", {}).get("failed_symbol_examples", 20)):
                stats["kline_failed_examples"].append(
                    {"symbol": outcome.symbol, "reason": outcome.error_message or "unknown"}
                )
            return
        if outcome.status == "no_bars":
            stats["no_bars"] += 1
            if len(stats["no_bars_symbols"]) < int(self.cfg.get("strategy", {}).get("failed_symbol_examples", 20)):
                stats["no_bars_symbols"].append(outcome.symbol)
            return
        if outcome.status == "insufficient_bars":
            stats["insufficient_bars"] += 1
            return
        if outcome.status == "df_empty":
            stats["df_empty"] += 1
            return
        if outcome.status == "threshold_reject":
            stats["threshold_reject"] += 1
            return
        if outcome.status == "risk_reject":
            stats["risk_reject"] += 1
            return
        if outcome.status == "market_reject":
            stats["market_reject"] += 1
            return
        if outcome.status == "candidate" and outcome.candidate is not None:
            stats["scored"] += 1
            ranked.append((outcome.index, outcome.candidate))

    @staticmethod
    def _print_scan_progress(mode: str, completed: int, total_symbols: int, candidate_count: int, progress_every: int) -> None:
        if progress_every > 0 and (completed % progress_every == 0 or completed == total_symbols):
            print(f"[{MODE_ZH.get(mode, mode)}] 已扫描 {completed}/{total_symbols}，候选={candidate_count}", flush=True)

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
        fail_on_error = bool(mcfg.get("fail_on_error", False))
        stop_on_stale = bool(mcfg.get("stop_on_stale", True))
        index_symbol = str(mcfg.get("index_symbol", "000300"))
        lookback = int(mcfg.get("lookback_days", 120))
        start = signal_date - timedelta(days=max(lookback * 2, 180))
        try:
            closes = self.data_source.get_index_closes(index_symbol, start, signal_date)
            if not closes:
                if fail_on_error:
                    raise RuntimeError(
                        f"Market index data unavailable: symbol={index_symbol}, range={start}->{signal_date}, reason=index_closes_empty"
                    )
                return MarketState(label="unknown", close=0.0, ma20=0.0, ma60=0.0, mom20=0.0), "index_closes_empty"
            latest_index_date = max(closes.keys())
            if latest_index_date < signal_date:
                stale_msg = (
                    f"Market index stale: symbol={index_symbol}, signal_date={signal_date}, latest={latest_index_date}"
                )
                if stop_on_stale:
                    raise RuntimeError(stale_msg)
                print(f"[警告] {stale_msg}", flush=True)
            st = detect_market_state(closes, signal_date, self.cfg)
            return st, "ok"
        except Exception as exc:
            if str(exc).startswith("Market index stale:"):
                if fail_on_error:
                    raise RuntimeError(str(exc)) from exc
                return MarketState(label="unknown", close=0.0, ma20=0.0, ma60=0.0, mom20=0.0), "index_stale"
            if fail_on_error:
                raise RuntimeError(
                    f"Market index data unavailable: symbol={index_symbol}, range={start}->{signal_date}, reason={type(exc).__name__}"
                ) from exc
            return MarketState(label="unknown", close=0.0, ma20=0.0, ma60=0.0, mom20=0.0), f"index_error:{type(exc).__name__}"

    def _fetch_recent_bars(self, symbol: str, signal_date: date):
        start = signal_date - timedelta(days=220)
        bars = self.data_source.get_daily_bars(symbol, start, signal_date)
        return [b for b in bars if b.trade_date <= signal_date]

    def _build_metrics(self, latest, market_state: MarketState) -> dict[str, float]:
        close = float(latest["close"])
        atr14 = float(latest["atr14"]) if latest.get("atr14") is not None else 0.0
        stop_loss_price, take_profit_price = compute_stop_take_prices(close, atr14, self.cfg)
        suggested_days = float(suggest_holding_days(latest, market_state, self.cfg))
        exit_plan = build_exit_plan(latest, market_state, self.cfg)
        return {
            "close": close,
            "ma20": float(latest["ma20"]),
            "ma60": float(latest["ma60"]),
            "ret_1d": float(latest["ret_1d"]) if latest.get("ret_1d") is not None else 0.0,
            "mom5": float(latest["mom5"]),
            "mom20": float(latest["mom20"]),
            "market_mom20": float(latest["market_mom20"]) if latest.get("market_mom20") is not None else 0.0,
            "mom20_excess_vs_market": float(latest["mom20"] - latest["market_mom20"]) if latest.get("market_mom20") is not None else 0.0,
            "sector_mom20": float(latest["sector_mom20"]) if latest.get("sector_mom20") is not None else 0.0,
            "mom20_excess_vs_sector": float(latest["mom20_excess_vs_sector"]) if latest.get("mom20_excess_vs_sector") is not None else 0.0,
            "rsi14": float(latest["rsi14"]),
            "atr14": atr14,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "suggested_holding_days": suggested_days,
            "vol_ratio_5_20": float(latest["vol_ratio_5_20"]) if latest.get("vol_ratio_5_20") is not None else 0.0,
            "volume_ratio_1_20": float(latest["volume_ratio_1_20"]) if latest.get("volume_ratio_1_20") is not None else 0.0,
            "volume_zscore20": float(latest["volume_zscore20"]) if latest.get("volume_zscore20") is not None else 0.0,
            "close_vs_ma20_pct": close / float(latest["ma20"]) - 1.0 if float(latest["ma20"]) > 0 else 0.0,
            "turnover_rate": float(latest["turnover_rate"]) if latest.get("turnover_rate") is not None else 0.0,
            "vol20_std": float(latest["vol20_std"]),
            "exit_plan": exit_plan,
        }
