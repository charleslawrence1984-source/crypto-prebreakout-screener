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

## Stage gates

- [ ] 100% deterministic tests pass: incomplete weekly/daily/4h, BTC
  alignment, future OHLCV changes never change past state, observed gaps,
  original proxy liquidity isolated, conservative stop-first exit.
- [ ] Real PR-only BTC/ETH/SOL public 4h/1d/1w pilot completes with source
  hashes and enough independently completed decision clocks for at least
  two symbols. Inspect ZIPs, do not infer health from just a green Actions run.
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
