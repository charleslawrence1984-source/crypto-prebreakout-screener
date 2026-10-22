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


## First real frozen 33-coin replay result — 22 October 2026

[PR-only GitHub Actions run 39751661912](../../actions/runs/39751661912) passed **8/8 synthetic safety tests** and the one-off historic-source/decision replay. Exported ZIP **artifact 12413593075** was independently opened and all five evidence files inspected.

- The original 4,514 decisions remain immutable: **11 original BUY**, **2,409 PASS excluded**, **502 WATCH EARLY excluded**, **761 WATCH not originally eligible excluded**, **831 nominal ready/retest/fresh-breakout WATCH counterfactual candidates**. Among the latter 831, **395** have insufficient original rolling Kraken OHLCV USD liquidity proxy; **436** survive as valid *hypothetical* next-open plans at 0.8% cost. These aren't real brokerage or funded eligibility checks.
- At 0.8% assumed roundtrip friction, **11 original BUY** outcomes agree exactly with original PR #48 modeled net-R results: **2 target hits, 9 stop-outs; mean −0.4465R**.
- The hypothetical broad ready-stage WATCH bypass yields **436** modeled outcomes: **71 target hits, 317 stop-outs, 48 time exits, mean −0.3181R**. Across original chronological folds: TRAIN **255** observations mean **−0.2239R**; VALIDATION **99** mean **−0.5330R**; UNTOUCHED HOLDOUT **82** mean **−0.3514R**. This negative descriptive cohort is not a rule-causal effect or independent-trade expectation.
- WATCH cost sensitivity: **0.4%** roundtrip mean **−0.1386R**, **0.8%** mean **−0.3181R**, **1.6%** mean **−0.5723R**. Requiring next open within **0.5%** of original frozen planned entry excludes another 2 cases and leaves **434** WATCH plans, mean **−0.3290R** at 0.8%.
- Frozen original WATCH reason-strata at 0.8%: PREBREAKOUT CREDIBLE TARGET **277** modeled plans mean **−0.2927R**; BULLISH RETEST **139** mean **−0.3319R**; FRESH RESISTANCE BREAKOUT **20** mean **−0.5736R**. No other original technical reason was hypothetically executed. All three observed strata have negative average results in this retrospective cohort.
- Simulated stop-first fills, original entry/stop/target levels, and future-missing exclusions are explicitly exported; all past WATCH states remain WATCH in the source. **No rule change, trade or live execution**.

**Conclusion:** A broad bypass of eligible WATCH restrictions did not improve the observed mean historic R after assumed costs, including in the supposedly untouched historical fold. The natural next investigation is *not* to loosen the rules, but to establish which of the remaining gating/trigger conditions explains original historical BUY scarcity, validate broker quote/execution feasibility, and prospectively test unexposed dates before considering a controlled rule change. The holdout has now been inspected descriptively and must not be reused as pristine evidence for a later tuned model.

All conclusions remain conditional on selected current-survivor coins, potential provider data vintage changes, imperfect liquidity proxy, correlated crypto regimes, technical original plan fields, and unverified actual Kraken Funded instruments and account constraints.
