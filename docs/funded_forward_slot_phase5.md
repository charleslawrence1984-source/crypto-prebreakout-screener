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


## Actual read-only forward research: 22 October 2026

[PR #52 original GitHub research Actions run 39764239730](../../actions/runs/39764239730) **BOTH JOBS SUCCESS** after a unit-test-discovered timestamp coercion bug was corrected. Real-artifact evidence **12419506421** independently downloaded/unzipped. Every row from the 21 archived main-branch scans (18 crypto, 3 stocks) was validated against immutable source SHA-256.

### Capture reliability and data truth

- The range BETWEEN the first and last observed crypto scan contained **28 consecutive possible UTC four-hour capture bins**. **16/28 bins captured** (57.14%), **12/28 unobserved** (42.86%). **Two bins had extra captures**, producing 18 distinct frozen crypto scan snapshots; *no outside-window coverage claim*.
- All **594** source-price row observations (18 x 33) showed the exact most recent **completed** UTC four-hour Kraken bar open for their actual observation clock; **zero** stale/misaligned/future/invalid source candle timestamps in those true captures. This validates bar timestamp finality, **not spot-liquidity coverage, Funded quote availability or broker eligibility**.
- Unobserved UTC bin starts: **18 Oct 00:00, 08:00; 19 Oct 00:00, 08:00, 16:00; 20 Oct 00:00, 08:00, 16:00; 21 Oct 00:00, 08:00, 16:00; 22 Oct 00:00**. UTC, 2026. **UNKNOWN market signal states**, not verified no opportunities or confirmed missed cron executions.
- Fresh GitHub schedule-run creation timestamps indicate material possible schedule lag: nominal crypto 04:35 UTC 22 Oct vs visible run **39726987720** created 05:15 (~40 min); nominal crypto 20:35 UTC 21 Oct vs visible **39694191860** created 23:33 (~2h59m). Original run **39669317990** was created 20:19 UTC on 21 Oct (compatible with ~3h44m delay from the *preceding* 16:35 crypto cron, but the exact intended cron slot is **NOT** exposed in run metadata). **Do not label these as proven scheduler misses without event-schedule identity**. Existing schedule-resilience issue #42 remains open.

### Actual newer prospective CRV paper outcome

- Two immutable original Kraken technical **CRV BUY** research observations at **21 Oct 20:22:23 UTC** and **21 Oct 23:36:29 UTC**, both referencing **the same original completed CRV 16:00 UTC four-hour candle**. Precisely **one distinct coin+bar signal episode**, two observed scans, **ZERO Kraken APP Funded broker-approved BUY or real trade orders**.
- The unchanged later Kraken public-candle shadow evaluator produced **2 shadow-stop observations**, but its original funded shadow risk account correctly deduplicated them into **1 eligible paper position**, vetoing the repeated setup as already open.
- Hypothetical entry: **22 October 00:00 UTC** at public OHLC proxy **$0.40268**. Hypothetical stop exit: **22 October 04:00 UTC** at **$0.37826457**. Modeled **0.5% round-trip costs**, approximately **−1.08246R**, **−$3** at the existing $1,000/$3 paper-risk reference. **Realized-only paper equity proxy = $997.00**, unchanged static loss line $970. Paper sample **1** is nowhere near 30 descriptive cases and no estimate of live performance is justified.
- Fresh read-only paper journal summary: **21 frozen research scan archives, 2 original technical BUY observations, 1 deduplicated closed loser, 0 funded broker fills, 682 other no-research-entry asset rows**. Neither public spot candle nor hypothetical stop-first ordering confirms genuine Funded in-app bid/ask or discretionary stop execution. Account continuous-equity risk remains unknown.

### What must happen before release

The immediate risk to CL Signal here is unreliable coverage, not evidence the current rules should be loosened. Preserve unobserved bins as UNKNOWN; inspect existing schedule-resilience drafts #34/#47 without merging, and propose an evidence-based scheduler/remote-job remediation only after independently validating original event timing, auth, duplicate snapshots, provider outage behavior, safe budget and operation. Do **not** create an extra recurring scan or alter original cron in this research-only PR. New independent closed forward cases and verified Funded app quote/account eligibility remain necessary to infer strategy edge.


## Real independent schedule-event forensics — final source-backed checkpoint

[Current GitHub PR head research run 39765413598](../../actions/runs/39765413598) **ALL THREE JOBS SUCCESS** (source-clock invariants + actual new public-candle paper evaluation + independent read-only GitHub scheduled-job audit). Exact source ZIPs: **12420795038** forward/capture and **12420495658** schedule. Both independently downloaded/unzipped and every ZIP member passed CRC integrity verification.

The authenticated GitHub REST metadata confirms **18 genuine completed scheduled crypto jobs** within this bounded historical period, and **18/18** map one-to-one to an actual original content-hash-verified crypto research archive. **Zero** scheduled jobs lacked an archived research capture among the examined records; zero archived captures lacked a matched examined scheduled job. All 18 jobs' recorded conclusion was SUCCESS.

**GitHub run creation delay proxy:** 11/18 occurred **more than 60 minutes** after the most recent preceding *nominal* crypto cron, and **7/18 more than 120 minutes**. The repo's crypto cron remains 00:35,04:35,08:35,12:35,16:35,20:35 UTC. For example 21 Oct run 39694191860 created 23:33 UTC against latest prior 20:35 nominal cron (~179 minutes). Run 39669317990 created 20:19 UTC against latest prior 16:35 nominal cron (~225 minutes). **The original intended scheduled occurrence is NOT available from GitHub REST run metadata**; a run may have been delayed from an EVEN EARLIER cron. These are *heuristic minimum nominal-creation-time offsets*, not guaranteed exact queue delays. The 12 unobserved 4h capture-clock bins do not themselves prove precisely which 12 scheduled invocations failed or were skipped.

The user-facing operational conclusion is nonetheless evidence-based: **the immediate reliability shortfall is 4h capture timing/coverage, not silent Kraken candle staleness in successful captures, not failed research jobs and not evidence to loosen BUY criteria**. GitHub best-effort cron starts are unsuitable as a proven clock-precise 4-hour signal capture service without further schedule hardening.

Next issue #42 development must propose **one** reliable primary cadence mechanism with proven run/event identity, idempotent immutable capture keys by intended market candle, provider failure transparency, safe manual dispatch recovery limited to NEW observations (never backfill a historical BUY), visibility of the existing schedule, and explicit owner approval for any production cron/source change. Do not add independent recurring monitoring from this read-only PR.

The actual CRV forward result is unchanged: one distinct original 4h BUY signal seen twice, one deduplicated stopped-out PAPER position at assumed 0.5% costs, -1.08246R/-$3 on $1k, $997 hypothetical closed equity, **no Funded account fills and no actual challenge outcome**.
