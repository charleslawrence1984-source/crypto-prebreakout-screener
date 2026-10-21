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

## First original-rule real data pilot — 21 Oct 2026 (preliminary)

[One-time PR-only CI run 39598388677](../../actions/runs/39598388677) passed 11/11
deterministic no-lookahead tests and executed the public provider pilot for
AAPL, MSFT, NVDA, TSLA and PLTR. Five stocks had 751 completed daily price
bars each (23 Oct 2023–20 Oct 2026), current-session price gate PASS.
The audit used 252-bar warmup, so this is **not** three full years of eligible
signals.

- Original engine prefilter checked **171** candidate symbol/sessions:
  **25 core**, **146 breakout** candidates.
- **0 original qualified technical signals**, **0 modeled next-open entries**,
  **0 completed simulated trades**. P&L $0 and no challenge pass/fail are
  **not** evidence of strategy safety, profitability, or challenge success.
- **171 full-rule rejections**. Top *overlapping* individual veto hits:
  projected breakout reward:risk below 2 (**142**), resistance not tested
  twice (**111**), approved structural stop-distance outside 0.4–8% (**104**),
  and volume below 1.35× (**90**). These counts are NOT independent cases
  or a list of rules to relax. Full per-event breakout reasons are enhanced
  in this draft, with follow-up CI still required.
- All-inclusive analysis recorded **21,975** 5/10/20-bar by threshold
  anchor/outcome rows across these five stocks, including extensive
  non-movers. The observations share overlapping windows; no independent
  sample size or recall precision claim is justified.
- Real ZIP artifact from that run: `funded-point-in-time-5-stock-pilot-39598388677`
  (artifact ID `12344946782`). The 11 CSV/JSON files were independently
  opened and sampled; include audited traces and original engine diagnostics.
- Current branch adds finer authentic breakout-veto trace and paired
  mover/non-mover counts **without changing the original decision path**.
  New CI/replay outputs may differ in diagnostic detail, but original signal
  decisions and frozen baseline should not.

**Interpretation:** this small stock subset produced no actionable technical
entries even while later upward closes occurred. This justifies diagnosis,
not looser gates. Non-survivor membership, corporate actions, earnings
calendar, broker execution and Kraken crypto multi-timeframe history remain
unverified.

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

Reports include all-anchor CSV, per-original-veto prospective mover/non-mover breakdown, original prefilter evaluation trace,
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
