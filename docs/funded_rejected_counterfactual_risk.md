# Kraken Funded P1 — risk-aware historical WATCH counterfactual

**Draft research only, 22 October 2026.** No live rule, scheduled task, account permission or broker order changes.

## Frozen inputs

The experiment reuses original point-in-time 33-coin scorer decisions and original Kraken 4-hour historical source snapshots frozen by draft PR #48, Actions run 39622003287. Original 4,514 scored decisions comprise 11 BUY, 2,094 WATCH and 2,409 rejected PASS. The source snapshots are independently checked against SHA-256 manifest entries. Full aggregate Kraken archive publisher SHA and historical Kraken Funded broker eligibility remain unverified.

## Cohorts — no hindsight

- ORIGINAL_BUY: the original scorer's technical BUY. Not a verified funded trade.
- WATCH_READY_BYPASS: original WATCH, with eligible and timing-actionable true at the original historical clock, stage READY, RETEST or FRESH BREAKOUT, and frozen original entry/stop/target. This **hypothetically bypasses ALL residual WATCH objections at once**, NOT an isolated one-rule change.
- Original rejected PASS, EARLY, reclaim-required, extended, too-late, missing plans, below-$1m rolling OHLCV-proxy liquidity, and other WATCH stages cannot be promoted to this hypothetical entry cohort.

Every included hypothetical entry is at the **next four-hour open**, using only original at-decision plan parameters. Require 20 consecutive real price candles after decision; otherwise censor and report why. Stop-first on simultaneous stop and target. An adverse later gap through stop exits at the actual lower open. Test **0.4%, 0.8%, 1.6% round-trip friction**, plus a separate strict maximum **0.5% deviation from the ORIGINAL as-of entry plan**. The strict price-distance test is research sensitivity, not a trading-rule change.

Include all candidate outcomes and exclusions; report by original TRAIN, VALIDATION, UNTOUCHED HOLDOUT, without optimising on holdout. Correlated current survivors do not provide independent estimates. No challenge pass probability, actual fill, broker quotes, eligibility, equity tracking or causality from an unchanged single gate is established.

## Gates

- [ ] Synthetic original state, archived source SHA, stop-first, adverse gap, no-fabricated-candle and fee tests green
- [ ] Real frozen 33-coin archive simulation runs in a one-off PR-only workflow and archived outputs are independently inspected
- [ ] Confirm original BUY outcomes match prior PR #48 stop-first paper baseline, resolve discrepancies
- [ ] Assess WATCH bypass versus non-movers, stop losses, and untouched-holdout sample without rule tuning
- [ ] Confirm Kraken Funded real broker and challenge risk constraints before any deployment recommendation
- [ ] Keep PR #48 and this PR unmerged unless separately approved by owner

Issue #43 tracks the original historical rulebook and missed-mover analysis; PR #46 stock-freshness guard is already merged. No new recurring monitor is created.
