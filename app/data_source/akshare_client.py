from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.models import DailyBar, StockInfo

try:
    import akshare as ak
except ImportError as exc:  # pragma: no cover
    ak = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _parse_trade_date(v: Any) -> date:
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, pd.Timestamp):
        return v.date()
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


class AkshareDataSource:
    def __init__(
        self,
        request_timeout_sec: float = 6.0,
        hist_retries: int = 3,
        use_spot_name_merge: bool = False,
        cache_enabled: bool = True,
        cache_dir: str = ".cache/akshare",
    ) -> None:
        if ak is None:
            raise RuntimeError(
                f"akshare is required but unavailable: {_IMPORT_ERROR}. "
                "Install dependencies from pyproject.toml."
            )
        self.request_timeout_sec = request_timeout_sec
        self.hist_retries = hist_retries
        self.use_spot_name_merge = use_spot_name_merge
        self.prefer_tx_fallback = True
        self.cache_enabled = cache_enabled
        self.cache_dir = Path(cache_dir)
        self.bars_cache_dir = self.cache_dir / "bars"
        self.meta_cache_dir = self.cache_dir / "meta"
        self.index_cache_dir = self.cache_dir / "index"
        if self.cache_enabled:
            self.bars_cache_dir.mkdir(parents=True, exist_ok=True)
            self.meta_cache_dir.mkdir(parents=True, exist_ok=True)
            self.index_cache_dir.mkdir(parents=True, exist_ok=True)

    def get_stock_list(self) -> list[StockInfo]:
        if self.cache_enabled:
            cached = self._load_stock_list_cache()
            if cached:
                return cached
        names_df = pd.DataFrame(columns=["code", "name"])
        spot_df = pd.DataFrame(columns=["代码", "名称"])
        try:
            names_df = ak.stock_info_a_code_name()
        except Exception:
            names_df = pd.DataFrame(columns=["code", "name"])

        if self.use_spot_name_merge or names_df.empty or len(names_df) < 500:
            for _ in range(2):
                try:
                    spot_df = ak.stock_zh_a_spot_em()
                    if not spot_df.empty and {"代码", "名称"}.issubset(set(spot_df.columns)):
                        break
                except Exception:
                    spot_df = pd.DataFrame(columns=["代码", "名称"])
                    time.sleep(0.2)

        # Build union by code to avoid single-source partial lists.
        name_map: dict[str, str] = {}
        for _, row in names_df.iterrows():
            code = str(row.get("code", "")).zfill(6)
            if code and code != "000000":
                name_map[code] = str(row.get("name", "") or "")
        for _, row in spot_df.iterrows():
            code = str(row.get("代码", "")).zfill(6)
            if code and code != "000000":
                spot_name = str(row.get("名称", "") or "")
                if code not in name_map or not name_map[code]:
                    name_map[code] = spot_name

        if not name_map:
            raise RuntimeError("Failed to fetch stock universe from both name and spot sources")

        items: list[StockInfo] = []
        for symbol, name in name_map.items():
            items.append(
                StockInfo(
                    symbol=symbol,
                    name=name,
                    listing_date=None,
                    is_st="ST" in name.upper(),
                    is_paused=False,
                    market=self._guess_market(symbol),
                )
            )
        if self.cache_enabled and items:
            self._save_stock_list_cache(items)
        return items

    def get_trade_dates(self, start_date: date, end_date: date) -> list[date]:
        cal = self._load_trade_calendar()
        trade_col = pd.to_datetime(cal["trade_date"])
        cal = cal[(trade_col >= pd.Timestamp(start_date)) & (trade_col <= pd.Timestamp(end_date))]
        out = [_parse_trade_date(v) for v in cal["trade_date"].tolist()]
        out.sort()
        return out

    def get_daily_bars(self, symbol: str, start_date: date, end_date: date) -> list[DailyBar]:
        if self.cache_enabled:
            cached = self._get_bars_from_cache(symbol, start_date, end_date)
            if cached is not None:
                return cached
        if self.prefer_tx_fallback:
            bars = self._fetch_remote_bars(symbol, start_date, end_date)
        else:
            bars = self._get_daily_bars_em(symbol, start_date, end_date, raise_on_error=True)
        if self.cache_enabled and bars:
            self._merge_save_bars_cache(symbol, bars)
        return bars

    def _fetch_remote_bars(self, symbol: str, start_date: date, end_date: date) -> list[DailyBar]:
        bars = self._get_daily_bars_em(symbol, start_date, end_date, raise_on_error=False)
        if bars:
            return bars
        tx_bars = self._get_daily_bars_tx(symbol, start_date, end_date, raise_on_error=False)
        if tx_bars:
            return tx_bars
        raise RuntimeError(f"Failed to fetch daily bars from both EM/TX for {symbol}")

    def _get_daily_bars_em(
        self, symbol: str, start_date: date, end_date: date, raise_on_error: bool = True
    ) -> list[DailyBar]:
        period = "daily"
        start = start_date.strftime("%Y%m%d")
        end = end_date.strftime("%Y%m%d")
        last_err: Exception | None = None
        df = pd.DataFrame()
        for attempt in range(self.hist_retries):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=symbol,
                    period=period,
                    start_date=start,
                    end_date=end,
                    adjust="qfq",
                    timeout=self.request_timeout_sec,
                )
                break
            except Exception as exc:
                last_err = exc
                if attempt < self.hist_retries - 1:
                    time.sleep(0.25 * (attempt + 1))
        if df.empty and last_err is not None:
            if raise_on_error:
                raise RuntimeError(f"Failed to fetch daily bars(EM) for {symbol}") from last_err
            return []
        if df.empty:
            return []
        return self._rows_to_bars_em(df)

    def _get_daily_bars_tx(
        self, symbol: str, start_date: date, end_date: date, raise_on_error: bool = False
    ) -> list[DailyBar]:
        tx_symbol = self._to_tx_symbol(symbol)
        start = start_date.strftime("%Y%m%d")
        end = end_date.strftime("%Y%m%d")
        last_err: Exception | None = None
        df = pd.DataFrame()
        for attempt in range(max(1, self.hist_retries)):
            try:
                df = ak.stock_zh_a_hist_tx(
                    symbol=tx_symbol,
                    start_date=start,
                    end_date=end,
                    adjust="qfq",
                    timeout=self.request_timeout_sec,
                )
                break
            except Exception as exc:
                last_err = exc
                if attempt < self.hist_retries - 1:
                    time.sleep(0.2 * (attempt + 1))
        if df.empty and last_err is not None:
            if raise_on_error:
                raise RuntimeError(f"Failed to fetch daily bars(TX) for {symbol}") from last_err
            return []
        if df.empty:
            return []
        return self._rows_to_bars_tx(df)

    @staticmethod
    def _rows_to_bars_em(df: pd.DataFrame) -> list[DailyBar]:
        bars: list[DailyBar] = []
        for _, row in df.iterrows():
            bars.append(
                DailyBar(
                    trade_date=_parse_trade_date(row["日期"]),
                    open=float(row["开盘"]),
                    high=float(row["最高"]),
                    low=float(row["最低"]),
                    close=float(row["收盘"]),
                    volume=float(row["成交量"]),
                    turnover_rate=float(row["换手率"]) if "换手率" in row and pd.notna(row["换手率"]) else None,
                )
            )
        bars.sort(key=lambda x: x.trade_date)
        return bars

    @staticmethod
    def _rows_to_bars_tx(df: pd.DataFrame) -> list[DailyBar]:
        bars: list[DailyBar] = []
        for _, row in df.iterrows():
            bars.append(
                DailyBar(
                    trade_date=_parse_trade_date(row["date"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["amount"]) if "amount" in row and pd.notna(row["amount"]) else 0.0,
                    turnover_rate=float(row["turnover"]) if "turnover" in row and pd.notna(row["turnover"]) else None,
                )
            )
        bars.sort(key=lambda x: x.trade_date)
        return bars

    @staticmethod
    def _to_tx_symbol(symbol: str) -> str:
        if symbol.startswith(("600", "601", "603", "605", "688")):
            return f"sh{symbol}"
        return f"sz{symbol}"

    def get_index_closes(self, symbol: str, start_date: date, end_date: date) -> dict[date, float]:
        frame = self._get_index_frame(symbol, start_date, end_date)
        if frame.empty:
            return {}
        out: dict[date, float] = {}
        for _, row in frame.iterrows():
            try:
                out[row["trade_date"]] = float(row["close"])
            except Exception:
                continue
        return out

    def _index_cache_path(self, symbol: str) -> Path:
        return self.index_cache_dir / f"{symbol}.csv"

    def _get_index_frame(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        cache_path = self._index_cache_path(symbol)
        cached = pd.DataFrame()
        if self.cache_enabled and cache_path.exists():
            try:
                cached = pd.read_csv(cache_path)
                cached = self._normalize_index_frame(cached)
            except Exception:
                cached = pd.DataFrame()

        if not cached.empty:
            cached_min = cached["trade_date"].min()
            cached_max = cached["trade_date"].max()
            if cached_min <= start_date and cached_max >= end_date:
                print(
                    f"[index][cache_hit] symbol={symbol} range={start_date}->{end_date} "
                    f"cached={cached_min}->{cached_max}",
                    flush=True,
                )
                return cached[(cached["trade_date"] >= start_date) & (cached["trade_date"] <= end_date)].copy()

        fresh = self._fetch_index_frame(symbol)
        if fresh.empty:
            if not cached.empty:
                print(
                    f"[index][cache_stale_use] symbol={symbol} range={start_date}->{end_date}",
                    flush=True,
                )
                return cached[(cached["trade_date"] >= start_date) & (cached["trade_date"] <= end_date)].copy()
            return pd.DataFrame(columns=["trade_date", "close"])

        if self.cache_enabled:
            self._save_index_frame(symbol, fresh)
        if cached.empty:
            print(f"[index][cache_miss] symbol={symbol} range={start_date}->{end_date}", flush=True)
        else:
            print(
                f"[index][cache_refresh] symbol={symbol} range={start_date}->{end_date} "
                f"cached_max={cached['trade_date'].max()} new_max={fresh['trade_date'].max()}",
                flush=True,
            )
        return fresh[(fresh["trade_date"] >= start_date) & (fresh["trade_date"] <= end_date)].copy()

    def _fetch_index_frame(self, symbol: str) -> pd.DataFrame:
        # hs300: "000300"
        raw_symbol = f"sh{symbol}" if symbol.startswith("000") else symbol
        idx = ak.stock_zh_index_daily(symbol=raw_symbol)
        if idx.empty:
            return pd.DataFrame(columns=["trade_date", "close"])
        return self._normalize_index_frame(idx)

    def _save_index_frame(self, symbol: str, frame: pd.DataFrame) -> None:
        path = self._index_cache_path(symbol)
        out = frame.copy()
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        out = out.dropna(subset=["trade_date"]).drop_duplicates(subset=["trade_date"], keep="last")
        out = out.sort_values("trade_date").reset_index(drop=True)
        out.to_csv(path, index=False)

    @staticmethod
    def _normalize_index_frame(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        if "date" in out.columns:
            dt = pd.to_datetime(out["date"], errors="coerce")
        else:
            dt = pd.to_datetime(out.index, errors="coerce")
        out["trade_date"] = dt.dt.date
        out = out.dropna(subset=["trade_date"])
        if "close" not in out.columns:
            return pd.DataFrame(columns=["trade_date", "close"])
        out = out[["trade_date", "close"]].copy()
        out = out.dropna(subset=["close"]).reset_index(drop=True)
        return out

    def _load_trade_calendar(self) -> pd.DataFrame:
        cache_path = self.meta_cache_dir / "trade_calendar.csv"
        if self.cache_enabled and cache_path.exists():
            try:
                return pd.read_csv(cache_path)
            except Exception:
                pass
        cal = ak.tool_trade_date_hist_sina()
        if self.cache_enabled:
            cal.to_csv(cache_path, index=False)
        return cal

    def _stock_list_cache_path(self) -> Path:
        return self.meta_cache_dir / "stock_list.csv"

    def _load_stock_list_cache(self) -> list[StockInfo]:
        path = self._stock_list_cache_path()
        if not path.exists():
            return []
        try:
            df = pd.read_csv(path, dtype={"symbol": str, "name": str, "market": str})
        except Exception:
            return []
        if df.empty:
            return []
        items: list[StockInfo] = []
        for _, row in df.iterrows():
            items.append(
                StockInfo(
                    symbol=str(row.get("symbol", "")).zfill(6),
                    name=str(row.get("name", "") or ""),
                    listing_date=None,
                    is_st=bool(row.get("is_st", False)),
                    is_paused=bool(row.get("is_paused", False)),
                    market=str(row.get("market", "") or None),
                )
            )
        return items

    def _save_stock_list_cache(self, stocks: list[StockInfo]) -> None:
        path = self._stock_list_cache_path()
        df = pd.DataFrame(
            {
                "symbol": [s.symbol for s in stocks],
                "name": [s.name for s in stocks],
                "is_st": [s.is_st for s in stocks],
                "is_paused": [s.is_paused for s in stocks],
                "market": [s.market for s in stocks],
            }
        )
        df.to_csv(path, index=False)

    def _bars_cache_path(self, symbol: str) -> Path:
        return self.bars_cache_dir / f"{symbol}.csv"

    def _get_bars_from_cache(self, symbol: str, start_date: date, end_date: date) -> list[DailyBar] | None:
        path = self._bars_cache_path(symbol)
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path)
        except Exception:
            return None
        if df.empty:
            return None
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
        df = df.dropna(subset=["trade_date"]).reset_index(drop=True)
        if df.empty:
            return None
        cached_min = df["trade_date"].min()
        cached_max = df["trade_date"].max()
        need_remote = start_date < cached_min or end_date > cached_max
        if need_remote:
            # Incrementally backfill missing left/right range.
            merged = self._incremental_fill_cache(symbol, df, start_date, end_date, cached_min, cached_max)
            if merged is not None:
                df = merged
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
        df = df.dropna(subset=["trade_date"]).reset_index(drop=True)
        sub = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)].copy()
        if sub.empty:
            return []
        return self._rows_to_bars_cache(sub)

    def _incremental_fill_cache(
        self,
        symbol: str,
        df: pd.DataFrame,
        start_date: date,
        end_date: date,
        cached_min: date,
        cached_max: date,
    ) -> pd.DataFrame | None:
        fetched: list[DailyBar] = []
        if start_date < cached_min:
            left_end = cached_min
            try:
                fetched.extend(self._fetch_remote_bars(symbol, start_date, left_end))
            except Exception:
                pass
        if end_date > cached_max:
            right_start = cached_max
            try:
                fetched.extend(self._fetch_remote_bars(symbol, right_start, end_date))
            except Exception:
                pass
        if not fetched:
            return None
        df_local = df.copy()
        df_local["trade_date"] = pd.to_datetime(df_local["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df_local = df_local.dropna(subset=["trade_date"]).reset_index(drop=True)
        fetched_df = pd.DataFrame(
            {
                "trade_date": [b.trade_date.isoformat() for b in fetched],
                "open": [b.open for b in fetched],
                "high": [b.high for b in fetched],
                "low": [b.low for b in fetched],
                "close": [b.close for b in fetched],
                "volume": [b.volume for b in fetched],
                "turnover_rate": [b.turnover_rate for b in fetched],
            }
        )
        frames = [frame for frame in (df_local, fetched_df) if not frame.empty]
        if not frames:
            return None
        merged = frames[0].copy() if len(frames) == 1 else pd.concat(frames, ignore_index=True)
        merged["trade_date"] = pd.to_datetime(merged["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        merged = merged.dropna(subset=["trade_date"])
        merged = merged.drop_duplicates(subset=["trade_date"], keep="last").sort_values("trade_date").reset_index(drop=True)
        self._save_bars_df(symbol, merged)
        merged["trade_date"] = pd.to_datetime(merged["trade_date"], errors="coerce").dt.date
        merged = merged.dropna(subset=["trade_date"]).reset_index(drop=True)
        return merged

    def _merge_save_bars_cache(self, symbol: str, bars: list[DailyBar]) -> None:
        new_df = pd.DataFrame(
            {
                "trade_date": [b.trade_date.isoformat() for b in bars],
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
                "volume": [b.volume for b in bars],
                "turnover_rate": [b.turnover_rate for b in bars],
            }
        )
        path = self._bars_cache_path(symbol)
        if path.exists():
            try:
                old = pd.read_csv(path)
                old["trade_date"] = pd.to_datetime(old["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
                frames = [frame for frame in (old, new_df) if not frame.empty]
                if not frames:
                    out = new_df
                else:
                    out = frames[0].copy() if len(frames) == 1 else pd.concat(frames, ignore_index=True)
            except Exception:
                out = new_df
        else:
            out = new_df
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        out = out.dropna(subset=["trade_date"])
        out = out.drop_duplicates(subset=["trade_date"], keep="last").sort_values("trade_date").reset_index(drop=True)
        self._save_bars_df(symbol, out)

    def _save_bars_df(self, symbol: str, df: pd.DataFrame) -> None:
        path = self._bars_cache_path(symbol)
        df.to_csv(path, index=False)

    @staticmethod
    def _rows_to_bars_cache(df: pd.DataFrame) -> list[DailyBar]:
        bars: list[DailyBar] = []
        for _, row in df.iterrows():
            bars.append(
                DailyBar(
                    trade_date=_parse_trade_date(row["trade_date"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    turnover_rate=float(row["turnover_rate"]) if pd.notna(row.get("turnover_rate")) else None,
                )
            )
        bars.sort(key=lambda x: x.trade_date)
        return bars

    @staticmethod
    def _guess_market(symbol: str) -> str:
        if symbol.startswith(("600", "601", "603", "605")):
            return "SH"
        if symbol.startswith("688"):
            return "STAR"
        if symbol.startswith(("000", "001", "002", "003", "300")):
            return "SZ"
        if symbol.startswith(("4", "8")):
            return "BJ"
        return "OTHER"
