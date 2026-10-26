# Kraken Funded: four P1 research priorities, 22 October 2026

**Independent DRAFT / not merged. No trading-rule edits, account connections, deployment, new monitor or broker orders.**

## 1. Original crypto triggers and missed moves

Input: immutable original 33-coin research decision snapshots, all 4,514 historical signal anchors from PR #48, plus 20x4h/+10% future-close labels and unchanged cost-adjusted hypothetical stop-first scenarios from PR #49.

The reason-attribution report preserves both future movers and non-movers per original reason and fold, plus observed technical R:R, rolling public liquidity proxy and the presence/absence of stop-adjusted WATCH/BUY scenarios.

**Important: reason labels do NOT expose the original MACD cross, volume expansion or resistance-touch numeric gate series.** No individual gate causal impact or profitable threshold change may be claimed. New point-in-time per-gate trace and unseen dates are prerequisites. The previously inspected historical holdout is NO LONGER pristine for parameter tuning.

## 2. Official Kraken app Funded rules — not Kraken Pro Prop

Authoritative Kraken support page: https://support.kraken.com/articles/funded (checked 22 October 2026).

- Kraken APP Funded tiers $1k/$5k/$10k, challenge target **+12%** and static loss floor **3% below original starting balance**, including intrabar equity. At $1,000 the published thresholds are **$1,120 target**, **$970 floor**; no time limit. These are NOT Kraken Pro Prop's daily reset/MDD plans.
- Kraken app Funded supports standard dollar-denominated buy/sell with **no leverage** and **no advanced stop-loss order types**. Quoted spread **0.04% per side** is built into the price; additional spread can be larger, especially 24/7 stock synthetic quotes. Do not infer public Yahoo/Kraken spot OHLC fills are real Kraken Funded fills.
- Kraken Funded offers crypto and some stocks only in eligible user regions; app-specific available assets are the authoritative universe for that account. UK availability not guaranteed by general marketing. Separate Kraken Prop rules should NOT be applied.
- Kraken Funded's stocks are **synthetic price exposure with 24/7 Funded tab execution**, not ownership of exchange-traded shares. Ordinary 16:00 New York-close NYSE OHLCV cannot establish weekend/night funded buy/sell spreads or tradability.
- This research account simulator uses existing paper **net R**, a fixed original-equity stop floor, 12% profit goal, and 0.25% / 0.5% / 1.0% initial-equity planned risk per sequential single position, across historical 0.4/0.8/1.6% cost scenarios **already embedded** in original paper R. It never double-adds costs.
- **Critical limitation:** paper R series contains CLOSED trade outcomes, not mark-to-market intrabar account equity. Therefore simulator cannot certify that the 3% static loss floor was not touched mid-trade, nor can it certify a challenge pass. Stop-first is only a conservative candle heuristic; Funded app has no native protective stop order.

Official sources:
https://support.kraken.com/articles/funded
https://support.kraken.com/articles/kraken-prop-faq

## 3. Stock data reliability

One-time (PR event ONLY) compare completed NYSE session latest bar for 4 public US symbols across Yahoo Finance and Stooq. Preserve vendor failures as DATA UNAVAILABLE, any still-prior-session bars as STALE, future/uncompleted bars as FUTURE_OR_IN_PROGRESS. Do NOT switch the live scanner to Stooq, SPY index surrogate, or a paid provider without a separately verified compatibility/availability review. Do not weaken PR #46 freshness gate.

A vendor side-by-side on one morning does NOT prove its habitual after-hours publication latency; repeated paired same-close observations and close timestamp provenance are needed. Existing Oct 22 00:56 UTC scheduled stock scan failed correctly because Yahoo still served 20 Oct when 21 Oct was needed.

## 4. Genuine prospective forward test

Reconcile the original public frozen scan funnel (GitHub read-only snapshots) with the earlier no-broker paper outcome journal. A later funnel timestamp than the journal explicitly reports CENSORED/outcome newer than journal. Missing scheduled 4h captures are **unobserved**, not assumed WATCH/zero entries.

Never count two actual original research triggers as two distinct filled positions; NEVER promote a WATCH, PASS, stale-data row, shadow position, or zero closed paper cases to real Funded readiness. Existing Oct 22 morning observations: two CRV research-trigger observations, no verified broker fills and no distinct closed paper positions at older outcome timestamp.

## Gates

- [ ] Deterministic historical causal attribution, Funded static-floor and vendor/forward tests green
- [ ] Actual unchanged frozen PR #48 + PR #49 artifacts analysed by PR-only CI and archived
- [ ] Independent real-world stock vendor paired session report with explicit missing data
- [ ] Forward funnel/paper journal read from independent immutably archived evidence with age warnings
- [ ] Review outputs before recommending any live rule/scan timing changes
- [ ] Explicit owner approval before merge; PRs #48 and #49 remain draft

This PR is analysis only. Issue #43 historical P1, issue #45 stock freshness P0, #36 funded umbrella and #35 roadmap remain canonical.
