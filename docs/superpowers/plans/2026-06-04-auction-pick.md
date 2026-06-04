# Auction Pick Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an isolated `auction-pick` command that ranks A-share opening auction candidates from one current quote snapshot without changing existing recommendation or tail-pick strategy behavior.

**Architecture:** Create a new `app/auction_pick/` package with its own models and engine. Reuse `IntradayQuote`, `DailyBar`, `StockInfo`, `filter_universe()`, and `add_indicators()` through narrow imports, but do not call `Recommender` or `TailPickEngine`. Wire one new CLI branch in `app/main.py` and keep output print-only.

**Tech Stack:** Python dataclasses, Protocol typing, existing AkShare data source, pandas-backed indicator helpers, pytest.

---

## File Structure

- Create `app/auction_pick/__init__.py`: package marker.
- Create `app/auction_pick/models.py`: auction result and payload dataclasses.
- Create `app/auction_pick/engine.py`: snapshot prefiltering, daily trend filtering, scoring, and ranking.
- Create `tests/test_auction_pick.py`: fake data source tests for filters, trend checks, scoring, no-candidate behavior, and parser wiring.
- Modify `app/main.py`: add `auction-pick` parser and isolated command branch.
- Modify `config/default.yaml`: add documented `auction_pick` defaults only.

No existing strategy module should import from `app.auction_pick`.

---

### Task 1: Write Auction Pick Model and Engine Tests

**Files:**
- Create: `tests/test_auction_pick.py`
- Implementation target: `app/auction_pick/models.py`
- Implementation target: `app/auction_pick/engine.py`

- [ ] **Step 1: Write failing tests for models, filtering, trend, ranking, and no-candidate behavior**

Add `tests/test_auction_pick.py` with this content:

```python
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.auction_pick.engine import AuctionPickEngine
from app.auction_pick.models import AuctionPickResult
from app.models import DailyBar, StockInfo
from app.tail_pick.models import IntradayQuote


class FakeAuctionDataSource:
    def __init__(self):
        self.trade_dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(180)]
        self.stocks = [
            StockInfo(symbol="000001", name="Leader"),
            StockInfo(symbol="000002", name="TooHot"),
            StockInfo(symbol="000003", name="Fade"),
            StockInfo(symbol="000004", name="WeakTrend"),
            StockInfo(symbol="000005", name="Second"),
        ]
        self.quotes = [
            IntradayQuote(
                "000001",
                "Leader",
                10.35,
                10.00,
                10.20,
                10.40,
                10.18,
                2_000_000,
                25_000_000,
                2.1,
                datetime(2026, 6, 4, 9, 26),
            ),
            IntradayQuote(
                "000002",
                "TooHot",
                10.85,
                10.00,
                10.60,
                10.90,
                10.55,
                3_000_000,
                35_000_000,
                3.2,
                datetime(2026, 6, 4, 9, 26),
            ),
            IntradayQuote(
                "000003",
                "Fade",
                10.10,
                10.00,
                10.20,
                10.25,
                10.05,
                2_500_000,
                26_000_000,
                2.8,
                datetime(2026, 6, 4, 9, 26),
            ),
            IntradayQuote(
                "000004",
                "WeakTrend",
                10.30,
                10.00,
                10.15,
                10.35,
                10.10,
                2_000_000,
                24_000_000,
                2.0,
                datetime(2026, 6, 4, 9, 26),
            ),
            IntradayQuote(
                "000005",
                "Second",
                10.28,
                10.00,
                10.12,
                10.32,
                10.10,
                2_000_000,
                20_000_000,
                1.8,
                datetime(2026, 6, 4, 9, 26),
            ),
        ]
        self.daily_bar_symbols: list[str] = []
        self.daily_bar_ranges: list[tuple[str, date, date]] = []

    def get_stock_list(self):
        return self.stocks

    def get_trade_dates(self, start_date, end_date):
        return [d for d in self.trade_dates if start_date <= d <= end_date]

    def get_daily_bars(self, symbol, start_date, end_date):
        self.daily_bar_symbols.append(symbol)
        self.daily_bar_ranges.append((symbol, start_date, end_date))
        dates = [d for d in self.trade_dates if start_date <= d <= end_date]
        close = 10.0
        bars = []
        for trade_date in dates:
            close = close * (0.995 if symbol == "000004" else 1.002)
            bars.append(
                DailyBar(
                    trade_date=trade_date,
                    open=close * 0.99,
                    high=close * 1.01,
                    low=close * 0.98,
                    close=close,
                    volume=1_000_000,
                    turnover_rate=2.0,
                )
            )
        return bars

    def get_intraday_quotes(self):
        return self.quotes


def test_auction_pick_result_serializes_core_fields():
    quote = IntradayQuote(
        "000001",
        "Leader",
        10.35,
        10.00,
        10.20,
        10.40,
        10.18,
        2_000_000,
        25_000_000,
        2.1,
        datetime(2026, 6, 4, 9, 26),
    )
    result = AuctionPickResult(
        trade_date=date(2026, 6, 4),
        quote=quote,
        score=81.5,
        opening_gap=0.02,
        current_return=0.035,
        reasons=["opening gap 2.00%", "amount 2500w"],
    )

    payload = result.as_dict()

    assert payload["symbol"] == "000001"
    assert payload["opening_gap"] == 0.02
    assert payload["current_return"] == 0.035
    assert payload["execution_notes"][0] == "9:30-9:35 不破开盘价和分时均价线再考虑试仓"


def test_auction_pick_selects_ranked_candidates_and_skips_failed_filters():
    engine = AuctionPickEngine(FakeAuctionDataSource(), {"auction_pick": {"count": 2}})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.candidates_scanned == 5
    assert payload.candidates_passed == 2
    assert [item.quote.symbol for item in payload.selected] == ["000001", "000005"]
    assert payload.selected[0].score > payload.selected[1].score


def test_auction_pick_prefilters_snapshot_before_fetching_daily_bars():
    ds = FakeAuctionDataSource()
    engine = AuctionPickEngine(ds, {"auction_pick": {"max_snapshot_candidates": 2}})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.selected
    assert ds.daily_bar_symbols == ["000001", "000005"]


def test_auction_pick_uses_previous_trade_date_for_daily_trend():
    ds = FakeAuctionDataSource()
    engine = AuctionPickEngine(ds, {"auction_pick": {"count": 1}})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.selected
    assert ds.daily_bar_ranges[0][2] == date(2026, 6, 3)


def test_auction_pick_returns_no_trade_when_all_quotes_fail():
    ds = FakeAuctionDataSource()
    ds.quotes = [
        IntradayQuote(
            "000001",
            "Leader",
            10.05,
            10.00,
            10.04,
            10.08,
            10.00,
            1_000_000,
            9_000_000,
            1.0,
            datetime(2026, 6, 4, 9, 26),
        )
    ]
    engine = AuctionPickEngine(ds, {})

    payload = engine.pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_scanned == 1
    assert payload.candidates_passed == 0
```

- [ ] **Step 2: Run tests to verify import failure**

Run:

```bash
pytest tests/test_auction_pick.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.auction_pick'`.

- [ ] **Step 3: Commit the failing tests**

Run:

```bash
git add tests/test_auction_pick.py
git commit -m "test: add auction pick engine expectations"
```

Expected: commit succeeds with only the new test file staged.

---

### Task 2: Implement Auction Pick Models and Engine

**Files:**
- Create: `app/auction_pick/__init__.py`
- Create: `app/auction_pick/models.py`
- Create: `app/auction_pick/engine.py`
- Test: `tests/test_auction_pick.py`

- [ ] **Step 1: Create package marker**

Create `app/auction_pick/__init__.py`:

```python
"""Opening auction stock picker."""
```

- [ ] **Step 2: Implement result models**

Create `app/auction_pick/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.tail_pick.models import IntradayQuote


@dataclass(frozen=True)
class AuctionPickResult:
    trade_date: date
    quote: IntradayQuote
    score: float
    opening_gap: float
    current_return: float
    reasons: list[str]

    @property
    def execution_notes(self) -> list[str]:
        return [
            "9:30-9:35 不破开盘价和分时均价线再考虑试仓",
            "跌破开盘价或竞价强势快速消失，放弃买入",
            "板块没有同步走强时，只观察不追高",
            "首笔仓位控制在计划仓位的 1/3 到 1/2",
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "symbol": self.quote.symbol,
            "name": self.quote.name,
            "latest": round(self.quote.latest, 4),
            "open": round(self.quote.open, 4),
            "previous_close": round(self.quote.previous_close, 4),
            "opening_gap": round(self.opening_gap, 6),
            "current_return": round(self.current_return, 6),
            "amount": round(self.quote.amount, 2),
            "score": round(self.score, 2),
            "snapshot_time": self.quote.snapshot_time.isoformat() if self.quote.snapshot_time else None,
            "reasons": list(self.reasons),
            "execution_notes": self.execution_notes,
        }


@dataclass(frozen=True)
class AuctionPickPayload:
    trade_date: date
    selected: list[AuctionPickResult]
    candidates_scanned: int
    candidates_passed: int
    filters: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "selected": [item.as_dict() for item in self.selected],
            "candidates_scanned": self.candidates_scanned,
            "candidates_passed": self.candidates_passed,
            "filters": dict(self.filters),
        }
```

- [ ] **Step 3: Implement engine**

Create `app/auction_pick/engine.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from app.auction_pick.models import AuctionPickPayload, AuctionPickResult
from app.features.indicators import add_indicators, bars_to_df
from app.tail_pick.models import IntradayQuote
from app.universe.filtering import filter_universe


class AuctionPickDataSource(Protocol):
    def get_stock_list(self) -> list[Any]:
        ...

    def get_trade_dates(self, start_date: date, end_date: date) -> list[date]:
        ...

    def get_daily_bars(self, symbol: str, start_date: date, end_date: date) -> list[Any]:
        ...

    def get_intraday_quotes(self) -> list[IntradayQuote]:
        ...


@dataclass(frozen=True)
class _ScoredQuote:
    result: AuctionPickResult
    amount: float


class AuctionPickEngine:
    def __init__(self, data_source: AuctionPickDataSource, cfg: dict[str, Any]):
        self.data_source = data_source
        self.cfg = cfg

    def pick(self, trade_date: date, count: int | None = None) -> AuctionPickPayload:
        filters = self._filters()
        pick_count = max(int(count if count is not None else filters["count"]), 1)
        daily_end_date = self._resolve_completed_daily_date(trade_date)
        universe = filter_universe(self.data_source.get_stock_list(), self.cfg, trade_date)
        allowed_symbols = {stock.symbol for stock in universe}
        quotes = [q for q in self.data_source.get_intraday_quotes() if q.symbol in allowed_symbols]
        scanned_count = len(quotes)
        quotes = self._prefilter_quotes(quotes, filters)
        ranked: list[_ScoredQuote] = []
        for quote in quotes:
            result = self._score_quote(quote, trade_date, daily_end_date, filters)
            if result is not None:
                ranked.append(_ScoredQuote(result=result, amount=quote.amount))
        ranked.sort(key=lambda item: (-item.result.score, -item.amount, item.result.quote.symbol))
        selected = [item.result for item in ranked[:pick_count]]
        return AuctionPickPayload(
            trade_date=trade_date,
            selected=selected,
            candidates_scanned=scanned_count,
            candidates_passed=len(ranked),
            filters=filters,
        )

    def _filters(self) -> dict[str, float]:
        cfg = self.cfg.get("auction_pick", {}) if isinstance(self.cfg.get("auction_pick", {}), dict) else {}
        return {
            "count": float(cfg.get("count", 5)),
            "min_opening_gap": float(cfg.get("min_opening_gap", 0.01)),
            "max_opening_gap": float(cfg.get("max_opening_gap", 0.05)),
            "min_current_return": float(cfg.get("min_current_return", 0.008)),
            "max_current_return": float(cfg.get("max_current_return", 0.06)),
            "min_amount": float(cfg.get("min_amount", 10_000_000)),
            "min_latest_vs_open": float(cfg.get("min_latest_vs_open", 0.995)),
            "max_snapshot_candidates": float(cfg.get("max_snapshot_candidates", 80)),
            "limit_up_return": float(cfg.get("limit_up_return", 0.095)),
        }

    def _resolve_completed_daily_date(self, trade_date: date) -> date:
        dates = self.data_source.get_trade_dates(trade_date - timedelta(days=30), trade_date)
        completed = [item for item in dates if item < trade_date]
        if completed:
            return completed[-1]
        return trade_date - timedelta(days=1)

    @staticmethod
    def _prefilter_quotes(quotes: list[IntradayQuote], filters: dict[str, float]) -> list[IntradayQuote]:
        out: list[IntradayQuote] = []
        for quote in quotes:
            if (
                quote.latest <= 0
                or quote.previous_close <= 0
                or quote.open <= 0
                or quote.volume <= 0
                or quote.amount <= 0
            ):
                continue
            opening_gap = quote.open / quote.previous_close - 1.0
            current_return = quote.latest / quote.previous_close - 1.0
            if opening_gap < filters["min_opening_gap"] or opening_gap > filters["max_opening_gap"]:
                continue
            if current_return < filters["min_current_return"] or current_return > filters["max_current_return"]:
                continue
            if current_return >= filters["limit_up_return"]:
                continue
            if quote.amount < filters["min_amount"]:
                continue
            if quote.latest < quote.open * filters["min_latest_vs_open"]:
                continue
            out.append(quote)
        out.sort(key=lambda item: (-item.amount, item.symbol))
        max_candidates = max(int(filters.get("max_snapshot_candidates", 80)), 1)
        return out[:max_candidates]

    def _score_quote(
        self,
        quote: IntradayQuote,
        trade_date: date,
        daily_end_date: date,
        filters: dict[str, float],
    ) -> AuctionPickResult | None:
        bars = self.data_source.get_daily_bars(quote.symbol, daily_end_date - timedelta(days=160), daily_end_date)
        df = add_indicators(bars_to_df(bars))
        if df.empty:
            return None
        latest_daily = df.iloc[-1]
        close = float(latest_daily["close"])
        ma20 = float(latest_daily["ma20"])
        ma60 = float(latest_daily["ma60"])
        if not (close >= ma20 or ma20 >= ma60):
            return None

        opening_gap = quote.open / quote.previous_close - 1.0
        current_return = quote.latest / quote.previous_close - 1.0
        gap_span = max(filters["max_opening_gap"] - filters["min_opening_gap"], 0.001)
        gap_mid = (filters["min_opening_gap"] + filters["max_opening_gap"]) / 2.0
        gap_score = max(1.0 - abs(opening_gap - gap_mid) / gap_span, 0.0) * 25.0
        return_score = min(max(current_return - filters["min_current_return"], 0.0) / 0.05, 1.0) * 25.0
        amount_score = min(quote.amount / 50_000_000, 1.0) * 25.0
        trend_score = 0.0
        if close >= ma20:
            trend_score += 12.5
        if ma20 >= ma60:
            trend_score += 12.5
        fade_penalty = max((quote.open - quote.latest) / quote.open, 0.0) * 100.0
        score = gap_score + return_score + amount_score + trend_score - fade_penalty
        return AuctionPickResult(
            trade_date=trade_date,
            quote=quote,
            score=score,
            opening_gap=opening_gap,
            current_return=current_return,
            reasons=[
                f"opening gap {opening_gap:.2%}",
                f"current return {current_return:.2%}",
                f"amount {quote.amount / 10000:.0f}w",
                "daily trend passes MA20/MA60 filter",
            ],
        )
```

- [ ] **Step 4: Run model and engine tests**

Run:

```bash
pytest tests/test_auction_pick.py -q
```

Expected: PASS for the five engine/model tests from Task 1, except parser wiring if Task 3 has not been added yet.

- [ ] **Step 5: Commit implementation**

Run:

```bash
git add app/auction_pick tests/test_auction_pick.py
git commit -m "feat: add auction pick engine"
```

Expected: commit succeeds with new package and updated passing tests.

---

### Task 3: Add CLI Parser and Command Output

**Files:**
- Modify: `tests/test_auction_pick.py`
- Modify: `app/main.py`
- Test: `tests/test_auction_pick.py`

- [ ] **Step 1: Add parser test**

Append this test to `tests/test_auction_pick.py`:

```python
from app.main import build_parser


def test_auction_pick_parser_accepts_date_count_and_output():
    args = build_parser().parse_args(["auction-pick", "--date", "2026-06-04", "--count", "3", "--output", "json"])

    assert args.cmd == "auction-pick"
    assert args.date == "2026-06-04"
    assert args.count == 3
    assert args.output == "json"
```

- [ ] **Step 2: Run parser test to verify it fails**

Run:

```bash
pytest tests/test_auction_pick.py::test_auction_pick_parser_accepts_date_count_and_output -q
```

Expected: FAIL with argparse invalid choice for `auction-pick`.

- [ ] **Step 3: Add parser branch**

In `app/main.py`, inside `build_parser()` after the `tail-pick` parser block, add:

```python
    p_auction = sub.add_parser("auction-pick", help="Pick opening auction candidates from one quote snapshot")
    p_auction.add_argument("--date", default=None, help="Run date YYYY-MM-DD; defaults to today")
    p_auction.add_argument("--count", type=int, default=None, help="How many auction candidates to show")
    p_auction.add_argument("--output", choices=["table", "json"], default="table")
```

- [ ] **Step 4: Add command branch**

In `app/main.py`, inside `main()` immediately after the `tail-pick` branch and before `recommend-adaptive`, add:

```python
    if args.cmd == "auction-pick":
        from app.auction_pick.engine import AuctionPickEngine

        _configure_network(base_cfg)
        ds = _build_data_source(base_cfg)
        trade_date = _parse_date(args.date)
        payload = AuctionPickEngine(ds, base_cfg).pick(trade_date, count=args.count)
        if args.output == "json":
            print(json.dumps(payload.as_dict(), ensure_ascii=False, indent=2))
            return
        print(
            f"[竞价] 日期={payload.trade_date.isoformat()} "
            f"扫描={payload.candidates_scanned} 入围={payload.candidates_passed}"
        )
        if not payload.selected:
            print("[竞价] 当前没有符合条件的竞价候选，建议空仓或只观察。")
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
        return
```

- [ ] **Step 5: Run parser test**

Run:

```bash
pytest tests/test_auction_pick.py::test_auction_pick_parser_accepts_date_count_and_output -q
```

Expected: PASS.

- [ ] **Step 6: Commit CLI wiring**

Run:

```bash
git add app/main.py tests/test_auction_pick.py
git commit -m "feat: wire auction pick cli"
```

Expected: commit succeeds with parser and command branch only.

---

### Task 4: Add Config Defaults and Isolation Regression Tests

**Files:**
- Modify: `config/default.yaml`
- Modify: `tests/test_auction_pick.py`
- Test: `tests/test_auction_pick.py`
- Test: `tests/test_tail_pick.py`
- Test: `tests/test_recommender.py`

- [ ] **Step 1: Add config defaults**

In `config/default.yaml`, add this section near the existing `tail_pick` section if present; otherwise add it after `filters`:

```yaml
# 开盘竞价候选策略。
# 这是独立命令 `auction-pick` 使用的配置，不参与 recommend-adaptive / tail-pick。
auction_pick:
  # 默认展示候选数量。
  count: 5

  # 开盘相对昨收高开的合理区间。
  min_opening_gap: 0.01
  max_opening_gap: 0.05

  # 当前价相对昨收的合理涨幅区间。
  min_current_return: 0.008
  max_current_return: 0.06

  # 最低成交额，单位：元。
  min_amount: 10000000

  # 当前价不能明显低于开盘价，用于过滤开盘后快速走弱。
  min_latest_vs_open: 0.995

  # 快照预筛后最多拉取多少只股票的日线，控制运行速度。
  max_snapshot_candidates: 80

  # 接近一字涨停的快照先过滤，避免不可成交候选。
  limit_up_return: 0.095
```

- [ ] **Step 2: Add config and isolation tests**

Append these tests to `tests/test_auction_pick.py`:

```python
from app.config import load_config


def test_default_config_contains_isolated_auction_pick_section():
    cfg = load_config("config/default.yaml")

    assert cfg["auction_pick"]["count"] == 5
    assert cfg["auction_pick"]["min_opening_gap"] == 0.01
    assert cfg["auction_pick"]["max_current_return"] == 0.06


def test_auction_pick_does_not_change_existing_parser_commands():
    parser = build_parser()

    tail_args = parser.parse_args(["tail-pick", "--date", "2026-06-04", "--output", "json"])
    adaptive_args = parser.parse_args(["recommend-adaptive", "--date", "2026-06-04", "--count", "1"])

    assert tail_args.cmd == "tail-pick"
    assert adaptive_args.cmd == "recommend-adaptive"
    assert adaptive_args.count == 1
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
pytest tests/test_auction_pick.py tests/test_tail_pick.py tests/test_recommender.py -q
```

Expected: PASS.

- [ ] **Step 4: Run broader regression tests**

Run:

```bash
pytest -q
```

Expected: PASS. If failures are unrelated to auction-pick, capture exact failing test names and inspect before changing shared code.

- [ ] **Step 5: Commit config and regression coverage**

Run:

```bash
git add config/default.yaml tests/test_auction_pick.py
git commit -m "test: protect auction pick isolation"
```

Expected: commit succeeds with config and tests only.

---

### Task 5: Manual Smoke Test and Final Review

**Files:**
- Read: `app/main.py`
- Read: `app/auction_pick/engine.py`
- Read: `config/default.yaml`

- [ ] **Step 1: Run JSON smoke test with limited universe if network/cache allows**

Run:

```bash
python3 -m app.main --config config/default.yaml auction-pick --date 2026-06-04 --count 3 --output json
```

Expected: command prints JSON with `trade_date`, `selected`, `candidates_scanned`, `candidates_passed`, and `filters`. If AkShare/network is unavailable, record the exact error and rely on fake-source tests.

- [ ] **Step 2: Run existing command parser smoke tests**

Run:

```bash
python3 -m app.main --help
```

Expected: help includes `auction-pick` and still includes `recommend-adaptive` and `tail-pick`.

- [ ] **Step 3: Inspect diff for unintended coupling**

Run:

```bash
git diff --stat HEAD~3..HEAD
git diff HEAD~3..HEAD -- app/engine app/strategy app/tail_pick app/main.py config/default.yaml
```

Expected: no changes under `app/engine` or `app/strategy`; `app/tail_pick` unchanged; `app/main.py` only has parser and command branch additions.

- [ ] **Step 4: Final status**

Run:

```bash
git status --short
```

Expected: clean worktree.

---

## Self-Review

Spec coverage:

- Isolated command and package: Tasks 2 and 3.
- Reuse quote snapshot and daily bars: Task 2.
- One snapshot run only: Task 3 command branch.
- No existing report writes: Task 3 prints table/JSON only.
- Config defaults under `auction_pick`: Task 4.
- No `recommend-adaptive` or `tail-pick` behavior changes: Task 4 tests and Task 5 diff inspection.
- Fake-source tests without network: Tasks 1, 2, and 4.

Placeholder scan:

- No `TBD`, `TODO`, or unspecified implementation steps are present.
- Every code-changing step includes concrete code.

Type consistency:

- `AuctionPickEngine.pick(trade_date, count=None)` returns `AuctionPickPayload`.
- `AuctionPickPayload.selected` is always `list[AuctionPickResult]`.
- CLI uses `payload.selected` as a list for both table and JSON output.
