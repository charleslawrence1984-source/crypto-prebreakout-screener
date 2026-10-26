# Kraken Funded Phase 2 — authentic historical crypto rule replay (DRAFT)

## Objective and permitted scope

Reconstruct the **original** Kraken Funded crypto scoring logic on historical
completed 4h candles using only daily, weekly and BTC-relative candles whose
closing timestamps were available by the original decision time.

- Original `crypto_rule_engine.score_setup`, original
  `crypto_market_pipeline.flatten_deep_score`, original
  `funded_crypto_checks` candle-completion semantics. No trading rule edits.
- Exact frozen approved set (33 crypto) sourced from `kraken_funded.py`.
- Real **Kraken public spot** USD pairs, fallback USDT; last 720 public 4h,
  365 daily, 220 weekly requested by the PR-only provider pilot. This is a
  bounded RECENT historical experiment, not multi-year crypto reproduction.
  Kraken ordinary OHLCV may return fewer bars than requested and does not
  reconstruct all historical listed/delisted market membership.
- Each historical 4h bar's open t is scored at **t+4h+1 minute**. Use at most
  the 180 latest *completed as-of* 4h, 365 daily, 220 weekly, and aligned BTC
  4h/daily candles, as in original research. Reject insufficient history,
  gaps, out-of-alignment BTC bars, non-finite and structurally impossible OHLCV.
- No present-day `fetch_tickers()` snapshot may enter a past decision. The
  original `flatten_deep_score` needs `Execution liquidity pass`, so phase 2
  derives a clearly-labeled **24h volume proxy** from six previously completed
  Kraken 4h bars (`volume × close`). A resulting original `BUY` is exported
  ONLY as **PROXY_BUY_UNVERIFIED**, never a confirmed historic Kraken quote or
  funded BUY. Historic market eligibility, bid/ask, exact ticker quote volume,
  and filled orders are UNKNOWN, not inferred.
- Source OHLCV CSV snapshots, dates, symbol and SHA-256 fingerprints are
  preserved in each immutable PR Actions artifact. Later revisions to the
  provider's historical bars are not knowable solely from the current download.

## Missed big movers: both outcomes, episode de-duplication

For every as-of observation store the original full technical state, score,
entry stage, risk:reward, hard veto reason and price inputs. Independently
label subsequent **4h CLOSE** maximum gain and minimum downside over **5/10/20**
4h bars at +10/+20/+30%, including *non-movers*, with no future outcomes
passed back into the scorer.

A separate greedy 5/10/20-bar cooldown collapses repeated positive anchor
windows into **descriptive separated mover episodes**. These are not
statistically independent trading events. Non-mover controls and forward
drawdown must remain visible; do not maximise only positive setups.

For fully qualified **PROXY_BUY_UNVERIFIED** technical cases only, an
*illustrative* 4h next-open/bid/ask friction outcome is computed with
assumed 0.8% roundtrip friction, stop-priority for same-bar stop & target,
adverse gap fills, and 20-candle horizon. This model is NOT a verified
funded-account executable position, not official drawdown compliance, and
must not be used to claim strategy profitability.

## Verified 21 October 2026 Kraken research results

**Initial three-coin PR run [39612988732](../../actions/runs/39612988732):**
BTC / ETH / SOL, 90 decision points each, 270 audited; original version
contained a report-mapping error that incorrectly tagged genuine original
WATCH states as UNKNOWN (original scorer unaffected). That mapping is corrected
and regression-tested in the expanded run.

**Expanded five-coin PR run [39613624913](../../actions/runs/39613624913):**
BTC / ETH / SOL / NEAR / HBAR, **300 completed historical 4h decision clocks
each**, spanning 29 August–18 October 2026, with subsequent outcomes through
21 October. Both real-provider and no-lookahead tests passed. Independently
unzipped artifact **12350948571** and recomputed **15/15** source OHLCV SHA-256
digests, all matched the stored manifest. Five coins × three timeframes had
no missing-as-of decision clocks.

- **1,500** original scored as-of technical decisions, **816 WATCH**
  and **684 rejected PASS**, **0 proxy BUY**, **0 funded-broker READY**.
- Analysis retains **13,500** overlapping later-close gain/downside
  anchor/window/threshold comparisons. By threshold and horizon:
  +10% within 5 bars: **52** positive label rows; within 10: **131**;
  within 20: **238**. No BUY at any of these signal-day clocks.
- Greedy within-window cooldown: **77 descriptive price episodes** across
  distinct horizon/threshold combinations. Those **77 are not 77 distinct
  rallies** because the same rally appears at multiple thresholds/horizons.
  At the single +10% / 20-bar definition there are **19** cooldown-separated
  coin-specific positive episodes: BTC 1, ETH 1, SOL 1, HBAR 4, NEAR 12;
  still not statistically independent trades.
- The **largest later 20-bar closing-price excursions** (not entered trades):
  NEAR **+61.42%**, HBAR **+36.74%**, SOL **+17.97%**,
  BTC **+10.89%**, ETH **+10.76%**.
- At +10% / 10-bar, the 131 positive anchors include NEAR 104, HBAR 20,
  SOL 7, BTC and ETH 0. They share repeated overlapping 4h windows.
  Most frequent original explanations among mover anchors include
  *Too far below resistance* (57 / 587 similarly blocked anchors later
  moved), *reclaim required* (19 / 95), *bullish retest* (15 / 93),
  *pre-breakout credible target* (14 / 452), and *too late* (11 / 50).
  These are **descriptive later prices**, not filled trades or a basis
  to loosen any veto without comparable non-mover risk.
- Source 4h data has 720 rows per coin (23 June–21 Oct 2026);
  365 daily and native weekly rows also preserved. Initial 90-point
  experiment was narrower and had no +10% events; the expanded sample
  changes that result because a longer completed period is evaluated.
- With **zero historical proxy BUY**, next-open/stop-first trade-outcome
  statistics and expected R cannot be meaningfully estimated. Do not
  interpret no trades as safe funded profitability.

### Full-history source discovery (separate, NOT downloaded into CI)

Kraken officially documents that REST OHLC returns at most **720 recent
candles**, no older historical candles:
https://docs.kraken.com/api-reference/market-data/get-ohlc-data

Kraken separately publishes **historical OHLCVT archives** with 240-minute
and daily CSVs and quarterly updates (the public help page was updated
September/October 2026):
https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data

These large archives are **not automatically pulled** into GitHub workflows.
The new `funded_kraken_official_archive.py` offers a deliberately offline,
explicit ZIP-member reader, published-checksum verifier, overlap conflict
checks and 7-completed-daily-bar-to-weekly reconstruction with **native Kraken
weekly parity validation**. Live observed Kraken 1w candle opens align to
Thursday 00 UTC; any mismatch against native weekly OHLCV fails closed.
Tests use small synthetic ZIPs rather than multi-GB external downloads.
A successful reader test does **NOT** mean full archive acquisition or
multi-year rule backtesting is complete.

## Stage gates

- [x] All deterministic replay and original WATCH/PASS mapping tests pass: incomplete weekly/daily/4h, BTC
  alignment, future OHLCV changes never change past state, observed gaps,
  original proxy liquidity isolated, conservative stop-first exit. Offline
  official ZIP ingestion/week parity tests are independently tested.
- [x] Real PR-only BTC/ETH/SOL, then BTC/ETH/SOL/NEAR/HBAR public 4h/1d/1w pilot completed with source hashes verified **15/15** and 1,500 completed as-of decision clocks in the expanded five-coin test. No funded-ready entries or broker fills claimed.
- [ ] Reconcile experimental scorer outputs against original forward live
  decisions on overlapping completed bars as a **consistency** test, not
  backdate present-day quote ticker data.
- [ ] Broaden eligible historical Kraken markets with actual as-of symbol
  membership and delisting events, ensure longer-than-120-day market history
  from a vetted source rather than fabricating older candles.
- [ ] Expand to all 33 coins where trustworthy Kraken histories genuinely
  exist, retain exclusions and repeated-timeframe gap diagnostics.
- [ ] Compare false positives, vetoes, non-movers and unique episodes on
  chronological train/validation/untouched holdout, with appropriate embargo.
- [ ] Verify actual Kraken Funded challenge rules, risk caps, spread/costs and
  permissible instruments before any pass-rate simulation.
- [ ] Separate user approval before merging PR #48 or any live-rule change.

**This is a research-only addition to draft PR #48 and issue #43.**
Code merge, broker connection, live order routing, rule relaxation, automated
trade alerts, account changes and surprise cron dispatch are all out of scope.
