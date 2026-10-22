# Kraken Funded P1 — genuine prospective 4h coverage, not hindsight

Research-only draft work, 22 October 2026. No scheduled workflows added. No broker access, orders, original source modifications, technical signal changes or automatic deployment. Prior research PRs #48–#51 remain unmerged.

## Why
Historical Kraken study covered 33 current coins and 4,514 original as-of decisions, but these are not present-day funded account orders or prospective validation. The existing prospective 4-hour cadence sometimes showed scan gaps. Unobserved periods must never be counted as WATCH, PASS, no opportunity, or invented profitable missed BUYs. Repeat observations of the same original coin/candle must not count as independent positions.

## What this new read-only PR performs
- Restore actual default-branch original 33-coin and stock snapshots from GitHub within a bounded past-14-day interval, verifying the SHA-256 of every immutable original archived source.
- Bin actual crypto capture clocks into 4-hour UTC windows, between the earliest and latest true observed snapshot. Explicitly report observed, **unobserved UNKNOWN** and multiple-capture buckets plus scan source candle finality.
- A 00:35 UTC capture's latest completed 4-hour Kraken candle should have opened at 20:00 UTC on the prior day. Incorrect, missing, stale and future/uncompleted original provider bars remain audit warnings, never secretly made current or rescored as new BUYs.
- Count original technical BUY observations separately from distinct coin + exact source candle-open identities. WATCH-to-BUY transitions require genuinely observed consecutive states and must not bridge an unknown capture interval.
- PR-only original public future-candle shadow outcome runner reads previously frozen default-branch technical original BUY snapshots and independently fetches subsequently completed public Kraken / Yahoo OHLCV candles. Reconciliation checks capture-to-outcome journal clock order, original source counts, paper outcome censoring and any remaining missing observations.
- The test suite rejects duplicate tampered original run IDs, bad SHA-256s, future bars, naive local timestamp assumptions, synthesized missing captures and anything that presents paper outcomes as funded broker trades.

## Evidence and limits
The existing static Kraken APP Funded challenge has +12% target and fixed -3% starting-capital loss threshold, with no leverage or native advanced stop orders. Public Kraken spot OHLC and Yahoo NYSE prices are not verified account-specific Funded quote, bid/ask, synthetic stock or execution records. A truly unobserved 4h interval cannot establish what signals existed within it. History must not be retrospectively filled. An archived original technical BUY still needs account-specific verification and cannot authorize a position.

This is a new PR-only workflow. No added cron, changes to the one existing daily morning status automation, modifications of production rules, merge approvals or stock vendor auto-fallback.

## Gates
- [ ] Deterministic missing slot, repeated same-candle BUY, source candle staleness, original SHA, forward clock and broker isolation tests green
- [ ] Independent public-provider forward outcome evaluation completes and archived evidence manually inspected
- [ ] Record actual original coverage gap times and any truly closed later paper results with limitations on issue #36 and project #35
- [ ] Remain draft and unmerged without explicit user approval

Tracking: roadmap #35, Kraken Funded #36, historical research #43, funded risk #44 and stale NYSE vendor #45.
