# Intraday Pick Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `auction-pick` more selective with two default candidates and make `tail-pick` stricter about executable intraday strength.

**Architecture:** Keep the existing isolated engine structure. Add focused filter defaults and helper calculations inside `app/auction_pick/engine.py` and `app/tail_pick/engine.py`, then update tests and `config/default.yaml`.

**Tech Stack:** Python dataclasses, pandas indicator frame, pytest, YAML config.

---

## File Structure

- Modify `config/default.yaml`: update explicit `auction_pick` defaults and add new auction filter keys.
- Modify `app/auction_pick/engine.py`: read new filter defaults, enforce daily overheat/trend filters, and adjust scoring.
- Modify `app/tail_pick/engine.py`: read new filter defaults, enforce quote-position and daily overheat/trend filters, and adjust scoring.
- Modify `tests/test_auction_pick.py`: update default config expectation and add filter coverage.
- Modify `tests/test_tail_pick.py`: update fake quote fixtures and add filter coverage.

### Task 1: Auction Defaults And Tests

**Files:**
- Modify: `tests/test_auction_pick.py`
- Modify: `config/default.yaml`
- Test: `tests/test_auction_pick.py`

- [ ] **Step 1: Write failing default config test**

Update `test_default_config_contains_isolated_auction_pick_section`:

```python
def test_default_config_contains_isolated_auction_pick_section():
    cfg = load_config("config/default.yaml")

    assert cfg["auction_pick"]["count"] == 2
    assert cfg["auction_pick"]["min_opening_gap"] == 0.012
    assert cfg["auction_pick"]["max_opening_gap"] == 0.04
    assert cfg["auction_pick"]["min_current_return"] == 0.012
    assert cfg["auction_pick"]["max_current_return"] == 0.055
    assert cfg["auction_pick"]["min_amount"] == 20_000_000
    assert cfg["auction_pick"]["min_latest_vs_open"] == 1.0
    assert cfg["auction_pick"]["limit_up_return"] == 0.09
    assert cfg["auction_pick"]["max_close_above_ma20_pct"] == 0.08
    assert cfg["auction_pick"]["max_rsi14"] == 75
    assert cfg["auction_pick"]["min_ma20_slope5"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_auction_pick.py::test_default_config_contains_isolated_auction_pick_section -q`

Expected: FAIL because current config still has `count == 5` and old thresholds.

- [ ] **Step 3: Update auction config**

Change `config/default.yaml` `auction_pick` block to:

```yaml
auction_pick:
  count: 2
  min_opening_gap: 0.012
  max_opening_gap: 0.04
  min_current_return: 0.012
  max_current_return: 0.055
  min_amount: 20000000
  min_latest_vs_open: 1.0
  max_snapshot_candidates: 80
  limit_up_return: 0.09
  max_close_above_ma20_pct: 0.08
  max_rsi14: 75
  min_ma20_slope5: 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_auction_pick.py::test_default_config_contains_isolated_auction_pick_section -q`

Expected: PASS.

### Task 2: Auction Engine Filters And Scoring

**Files:**
- Modify: `tests/test_auction_pick.py`
- Modify: `app/auction_pick/engine.py`
- Test: `tests/test_auction_pick.py`

- [ ] **Step 1: Add failing auction filter tests**

Add tests:

```python
def test_auction_pick_rejects_current_price_below_open():
    ds = FakeAuctionDataSource()
    ds.quotes = [
        IntradayQuote("000001", "Fade", 10.19, 10.00, 10.20, 10.25, 10.05, 2_000_000, 25_000_000, 2.0, datetime(2026, 6, 4, 9, 26))
    ]
    payload = AuctionPickEngine(ds, {}).pick(date(2026, 6, 4))

    assert payload.selected == []
    assert payload.candidates_passed == 0


def test_auction_pick_rejects_overextended_daily_candidate():
    ds = FakeAuctionDataSource()
    ds.daily_profile = {"000001": "overextended"}

    payload = AuctionPickEngine(ds, {}).pick(date(2026, 6, 4), count=1)

    assert payload.selected == []
```

Update `FakeAuctionDataSource.get_daily_bars` to support `daily_profile = {}` and an `overextended` profile that returns closes far above MA20 with high RSI behavior:

```python
profile = getattr(self, "daily_profile", {}).get(symbol, "normal")
if profile == "overextended":
    if len(bars) > 130:
        close *= 1.04
else:
    close = close * (0.995 if symbol == "000004" else 1.002)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_auction_pick.py::test_auction_pick_rejects_current_price_below_open tests/test_auction_pick.py::test_auction_pick_rejects_overextended_daily_candidate -q`

Expected: FAIL because the old engine allows below-open by `0.995` and does not enforce overextension/RSI/slope filters.

- [ ] **Step 3: Implement auction filters**

In `_filters`, add:

```python
"max_close_above_ma20_pct": float(cfg.get("max_close_above_ma20_pct", 0.08)),
"max_rsi14": float(cfg.get("max_rsi14", 75)),
"min_ma20_slope5": float(cfg.get("min_ma20_slope5", 0.0)),
```

In `_score_quote`, read:

```python
rsi14 = float(latest_daily["rsi14"])
ma20_slope5 = float(latest_daily["ma20_slope5"])
distance_above_ma20 = close / ma20 - 1.0 if ma20 > 0 else 0.0
```

Then require:

```python
if close < ma20 or ma20 < ma60:
    return None
if distance_above_ma20 > filters["max_close_above_ma20_pct"]:
    return None
if rsi14 > filters["max_rsi14"]:
    return None
if ma20_slope5 < filters["min_ma20_slope5"]:
    return None
```

Adjust scoring:

```python
gap_center_score = max(1.0 - abs(opening_gap - gap_mid) / gap_span, 0.0) * 25.0
return_center = (filters["min_current_return"] + filters["max_current_return"]) / 2.0
return_half_width = max((filters["max_current_return"] - filters["min_current_return"]) / 2.0, 0.001)
return_score = max(1.0 - abs(current_return - return_center) / return_half_width, 0.0) * 25.0
amount_score = min(quote.amount / 80_000_000, 1.0) * 20.0
trend_score = 20.0 + min(max(ma20_slope5, 0.0) / 0.03, 1.0) * 10.0
fade_penalty = max((quote.open - quote.latest) / quote.open, 0.0) * 150.0
score = gap_center_score + return_score + amount_score + trend_score - fade_penalty
```

- [ ] **Step 4: Run auction tests**

Run: `python3 -m pytest tests/test_auction_pick.py -q`

Expected: PASS.

### Task 3: Tail Engine Filters And Scoring

**Files:**
- Modify: `tests/test_tail_pick.py`
- Modify: `app/tail_pick/engine.py`
- Test: `tests/test_tail_pick.py`

- [ ] **Step 1: Add failing tail filter tests**

Add tests:

```python
def test_tail_pick_rejects_quote_not_above_open():
    ds = FakeTailPickDataSource()
    ds.quotes = [
        IntradayQuote("000001", "NotAboveOpen", 10.9, 10.5, 11.0, 11.1, 10.5, 2_000_000, 22_000_000, 3.0, datetime(2026, 6, 4, 14, 45))
    ]

    payload = TailPickEngine(ds, {}).pick(date(2026, 6, 4))

    assert payload.selected is None
    assert payload.candidates_passed == 0


def test_tail_pick_rejects_late_fade_from_high():
    ds = FakeTailPickDataSource()
    ds.quotes = [
        IntradayQuote("000001", "Fade", 10.9, 10.5, 10.6, 11.4, 10.5, 2_000_000, 25_000_000, 3.0, datetime(2026, 6, 4, 14, 45))
    ]

    payload = TailPickEngine(ds, {}).pick(date(2026, 6, 4))

    assert payload.selected is None
    assert payload.candidates_passed == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tail_pick.py::test_tail_pick_rejects_quote_not_above_open tests/test_tail_pick.py::test_tail_pick_rejects_late_fade_from_high -q`

Expected: FAIL because the old tail engine does not require latest above open and does not filter high fade.

- [ ] **Step 3: Implement tail filters**

In `_filters`, update defaults:

```python
"min_intraday_return": float(cfg.get("min_intraday_return", 0.01)),
"max_intraday_return": float(cfg.get("max_intraday_return", 0.06)),
"min_amount": float(cfg.get("min_amount", 20_000_000)),
"stop_loss_pct": float(cfg.get("stop_loss_pct", 0.04)),
"max_snapshot_candidates": float(cfg.get("max_snapshot_candidates", 60)),
"min_latest_vs_open": float(cfg.get("min_latest_vs_open", 1.0)),
"min_close_position": float(cfg.get("min_close_position", 0.65)),
"max_fade_from_high": float(cfg.get("max_fade_from_high", 0.025)),
"max_close_above_ma20_pct": float(cfg.get("max_close_above_ma20_pct", 0.10)),
"max_rsi14": float(cfg.get("max_rsi14", 78)),
"min_ma20_slope5": float(cfg.get("min_ma20_slope5", 0.0)),
```

Add helper:

```python
@staticmethod
def _close_position(quote: IntradayQuote) -> float:
    span = quote.high - quote.low
    if span <= 0:
        return 0.0
    return (quote.latest - quote.low) / span
```

In `_prefilter_quotes`, require:

```python
if quote.open <= 0 or quote.high <= 0 or quote.low <= 0:
    continue
if quote.latest < quote.open * filters["min_latest_vs_open"]:
    continue
if TailPickEngine._close_position(quote) < filters["min_close_position"]:
    continue
if quote.high > 0 and quote.latest / quote.high - 1.0 < -filters["max_fade_from_high"]:
    continue
```

In `_score_quote`, add daily filters matching the spec:

```python
rsi14 = float(latest_daily["rsi14"])
ma20_slope5 = float(latest_daily["ma20_slope5"])
distance_above_ma20 = close / ma20 - 1.0 if ma20 > 0 else 0.0
if close < ma20 or ma20 < ma60:
    return None
if distance_above_ma20 > filters["max_close_above_ma20_pct"]:
    return None
if rsi14 > filters["max_rsi14"]:
    return None
if ma20_slope5 < filters["min_ma20_slope5"]:
    return None
```

Adjust score:

```python
close_position = self._close_position(quote)
return_center = (filters["min_intraday_return"] + filters["max_intraday_return"]) / 2.0
return_half_width = max((filters["max_intraday_return"] - filters["min_intraday_return"]) / 2.0, 0.001)
amount_score = min(quote.amount / 80_000_000, 1.0) * 25.0
return_score = max(1.0 - abs(intraday_return - return_center) / return_half_width, 0.0) * 30.0
position_score = close_position * 20.0
trend_score = min(max(distance_above_ma20, 0.0), 0.08) / 0.08 * 25.0
fade_penalty = max(1.0 - quote.latest / quote.high, 0.0) * 100.0 if quote.high > 0 else 0.0
score = amount_score + return_score + position_score + trend_score - fade_penalty
```

- [ ] **Step 4: Run tail tests**

Run: `python3 -m pytest tests/test_tail_pick.py -q`

Expected: PASS.

### Task 4: Full Verification

**Files:**
- Test: `tests/test_auction_pick.py`
- Test: `tests/test_tail_pick.py`

- [ ] **Step 1: Run focused test suite**

Run:

```bash
python3 -m pytest tests/test_auction_pick.py tests/test_tail_pick.py -q
```

Expected: PASS.

- [ ] **Step 2: Inspect diff for scope**

Run:

```bash
git diff -- app/auction_pick app/tail_pick config/default.yaml tests/test_auction_pick.py tests/test_tail_pick.py
```

Expected: only intraday pick engine/config/test changes.

- [ ] **Step 3: Commit implementation**

Run:

```bash
git add app/auction_pick/engine.py app/tail_pick/engine.py config/default.yaml tests/test_auction_pick.py tests/test_tail_pick.py docs/superpowers/plans/2026-06-04-intraday-pick-optimization.md
git commit -m "feat: tighten intraday pick filters"
```

Expected: one implementation commit.

## Self Review

- Spec coverage: auction default count, auction filters, tail filters, scoring, and test requirements are covered.
- Placeholder scan: no TBD/TODO placeholders remain.
- Type consistency: all referenced fields exist on `IntradayQuote` or indicator rows produced by `add_indicators`.
