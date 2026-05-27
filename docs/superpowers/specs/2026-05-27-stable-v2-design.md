# Stable V2 Strategy Design

## Goal

Create a long-term default candidate strategy that improves robustness without overfitting to the most recent losing streak.

## Background

The current main strategy still performs best over the long window because `recommend-pullback` contributes most of the historical return. A fully defensive replacement improved recent selected samples but failed over `2025-03-24 -> 2026-05-27`: it reduced trades, lowered average returns, and increased drawdown.

Therefore stable-v2 should not globally replace pullback with a stricter profile. It should keep pullback in clearly favorable regimes and explicitly allow no-trade behavior in weak regimes.

## Design

Add an explicit `cash` entry for adaptive regime orders. When `cash` is reached, adaptive recommendation stops trying further strategies and records a no-recommendation day. This is different from an empty list accidentally falling back to pullback.

Create `config/default.stable-v2.yaml` as an experimental candidate:

- `bull`: use `recommend-pullback`, then `recommend`.
- `neutral`: use `cash`.
- `bear`: use `recommend-oversold`, then `cash`.
- `unknown`: use `cash`.

Set stricter bull detection in the candidate config:

- `bull_min_close_above_ma20_pct: 0.01`
- `bull_min_mom20: 0.04`

This keeps the main pullback strategy in genuinely strong market states, but avoids forcing pullback when the index is close to MA20 or losing momentum.

## Non-Goals

- Do not overwrite `config/default.yaml` until A/B evidence supports replacement.
- Do not remove existing defensive profile work.
- Do not add new stock-selection formulas in this pass.
- Do not tune dozens of thresholds at once.

## Acceptance Criteria

Stable-v2 is only a replacement candidate if it meets all of these:

- Long-window 3-day net return is not materially worse than current.
- Long-window max drawdown proxy is lower or similar.
- Recent-window results improve relative to current.
- Trade count reduction is acceptable and not simply eliminating most trades.

If it fails these, keep it as an experimental config only.
