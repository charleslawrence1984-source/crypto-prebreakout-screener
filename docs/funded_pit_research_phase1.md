# Kraken Funded P1 — point-in-time historical opportunity research, Phase 1

**State: DRAFT, unmerged.** This is a prospective reproducible research
instrument; **not evidence of winning expectancy, a fundable signal, historical
Kraken broker execution, portfolio edge, or an authorised live order.**

## Frozen inputs and original decision rulebook

- Original production baseline (21 Oct 2026, after approved stock freshness
  release): `a1f1a37c3945743261cc4f7dd0ade0648f04cb62`.
- Stock pilot research uses **the original** `funded_stock_backtest.signal_candidates`,
  invoking `trade_rules.evaluate_price_setup` and
  `funded_stock_breakout.evaluate_funded_breakout`, using completed-bar
  information available at each t. Pre-filters and routing are the original
  engine's, including the possible suppression of breakout by core WATCH.
  No threshold or existing strategy signal rule has been changed.
- Public Yahoo Finance unadjusted 3y daily OHLCV and S&P 500 proxy; stock
  histories/benchmark must independently pass the last-completed-NYSE-session
  freshness gate released by PR #46. 252-bar indicator warm-up.
- Initial **five-symbol survivor-biased** smoke sample: AAPL, MSFT, NVDA, TSLA,
  PLTR from the presently approved 30. This selection is for *engineering*
  validation only, not outperformance. Historical membership, delistings,
  earnings news, splits/dividends and original broker liquidity are NOT
  reconstructed. Missing/unusable histories remain explicitly missing.
- The 33-coin original crypto universe will need separate authentic 4h, 1d,
  1w, Kraken market listing and BTC-relative data *as at each decision bar*.
  The generic outcome census can accept audited crypto signals but cannot
  manufacture them from the current WATCH/BUY snapshot. Thus no historical
  crypto signal accuracy is claimed by this phase.

## Signal versus subsequent mover: no future leakage

At time t (a completed bar close), original engine outputs are frozen
from `signal_candidates`: qualified technical setups, prefilter WATCH,
rejections, or no prefilter candidate. A symbol without a complete historical
engine evaluation is `UNKNOWN_NOT_AUDITED`, **not a missed trade**.

Independently label later **maximum future CLOSE gain** over t+1..t+h versus
CLOSE(t), for h=5/10/20 NYSE sessions. Compare research thresholds
+5%/+10%/+20% for stocks (generic crypto thresholds +10%/+20%/+30% over
5/10/20 crypto bars). These are analysis labels, NOT changes to Kraken rules.

Anchor-level ledgers retain **every fully observable eligible non-mover** and
every mover, with the same pre-event gate definition. A qualifying signal and
future mover is a *technical caught anchor*, NOT proof of a next-open entry or
Kraken Funded executable filled winner. WATCH-only, prefilter-rejected and
no-prefilter moments are distinguished; only audited anchors can be counted as
missed mover anchors. Full horizon missing = CENSORED, not a loser.

A separate existing funded-stock portfolio simulator, unchanged, estimates
next-open proxy fills/stops/targets under modeled 0.5% round-trip friction,
reserved risk and challenge-like constraints. This is **technical paper-only
and cannot enforce earnings events, real 24/7 broker quotes, official
challenge equity rules, split treatment or real trading costs**.

## Train/validation/holdout and dependence

For each symbol, train/validation/untouched holdout are chronological 60/20/20
based on available raw bars. **Purge** anchors if any of their forward horizon
crosses the following split. No signal changes or threshold tuning on the
holdout. Overlapping t anchor outcomes are correlated; these are NOT
statistically independent wins, precise rates, or a performance guarantee.

Reports include all-anchor CSV, original prefilter evaluation trace,
qualified original technical signal list, mover vs non-mover counts by fold,
availability and provider freshness, simulation output, and explicit JSON
assumptions / baseline commit. High price excursions from stock splits and
survivor bias **can contaminate the raw pilot**; review corporate action
metadata and historic universe before any numerical claim.

## Phase 1 safety gates

- [ ] Deterministic no-lookahead/missing-data/holdout tests pass in GitHub CI.
- [ ] One-time 5-stock real provider pilot archives usable CSV and JSON evidence.
- [ ] Audit event counts, actual gate state meaning, outcome denominators,
  economic and data-quality caveats. Do not call a 5-stock set representative.
- [ ] Produce *paired mover/non-mover gate analysis* with uncertainty and
  independent-episode counting before proposing any strategy parameter change.
- [ ] Phase 2: widen survivorship-aware stock history and build genuine
  historical Kraken 4h+daily+weekly multi-frame original-engine replay.
- [ ] Phase 3: add consistent next-open/higher timeframe crypto fill research,
  realistic asset-specific costs and official funded-provider risk rules.
- [ ] No merge or live order without separate owner approval.

Issue [#43](../issues/43) tracks completed versus outstanding gates.
