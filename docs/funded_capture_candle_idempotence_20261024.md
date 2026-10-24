# Kraken Funded P0 — completed-candle identity and source reliability (24 October 2026)

**Independent draft PR. Research-only. No new scheduled job, live indicator changes, provider switch, broker connection, trading orders, or PR merges.**

## New actual evidence checked on 24 October
- The last [scheduled funnel run 40076516428](../../actions/runs/40076516428) examined **26 genuinely archived crypto scans**, **5 genuine stock scans** and **two original technical BUY observations for the same CRV 4h bar**. This is still **one unique research episode**; the original public-price shadow paper evaluation recorded one stop, not two account trades. No broker-verified funded orders or closed real positions.
- Latest [forward paper run 40051905242](../../actions/runs/40051905242) succeeded, preserving **25 crypto / 5 stock archived snapshots**, **two original BUY observations**, **one distinct closed paper position**, assumed paper equity reference **$997** from $1k and **zero verified actual funded trades**. Daily forward paper can lag the later funnel scan; do not implicitly join a newer source cohort to older outcomes.
- 24 October stock research scan [40048440026](../../actions/runs/40048440026) correctly **failed closed** and uploaded **30/30 DATA UNAVAILABLE** evidence rather than producing a false clean watchlist. Public Yahoo delayed/unavailable end-of-day data is still an unresolved P0 (issue #45).
- GitHub's [official schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) explicitly says cron workflow runs can be delayed or dropped during load. The exact intended cron **slot timestamp is not exposed** by the public run record; the existing #52 forensics found real source capture bins at 57.14% for its original range. A delayed run may score the latest truly completed 4h source bar rather than the slot for which it was originally requested. The result cannot be backdated or assumed to represent a missing opportunity.

## The invariant studied by this PR

A 4-hour Kraken historical research signal is eligible for **deduplicated technical research recording only**, never broker execution, if:
1. Its original immutable main-branch snapshot SHA-256, approved 33-coin universe and RESEARCH ONLY status pass original validation;
2. Its own public Kraken bar timestamp is exactly 00:00, 04:00, 08:00, 12:00, 16:00 or 20:00 UTC and is the latest **completed** 4h bar at ACTUAL capture clock, with +1 minute candle-finality allowance;
3. Its original technical engine state was truly `BUY`, not WATCH/PASS/score subset;
4. Any repeated observation of the same coin+source candle has no contradictory original statuses or switched public spot pair. Contradiction ⇒ **QUARANTINE**, not a manufactured BUY;
5. Duplicate actual scans within a UTC capture bucket do not inflate the expected 4h coverage denominator. Missing UTC 4h capture buckets are explicitly **UNKNOWN**, never inferred as PASS/WATCH/NO SIGNAL.

The code does not rescore a signal or replace the original production cron. Genuine original states and all malformed-source warnings are preserved in an exported per-source/episode ledger. The unexposed historical holdout from previous studies has already been examined: no tuning on it is allowed.

## Proposed, NOT DEPLOYED, mitigation decision

Do **not** simply change the cron minute to :00 or bulk-replay missing hours: neither makes an already-completed source bar contemporaneously tradable. Future scheduler remediation requires an explicit separately approved deployment design with a reliably initiated four-hour capture, original provider data availability, immutable source-candle identity, at-most-once simulated trade per source asset/candle, account-specific Funded market membership and executable bid/ask, rate-limit safety, observable failures and rollback. An external scheduler/service might become necessary; none has been connected or scheduled in this PR.

## Current release gates
- [ ] Deterministic safety invariants pass: missing/duplicate 4h slots, inconsistent duplicate BUY/WATCH, pair switch, +1m candle finality, stale/future source candles, SHA tampering, 0 crypto-source evidence and absent funded broker authorization.
- [ ] A one-off PR-only job examines latest real default-branch crypto original snapshots and archives true 4h slot quality without adding another cron.
- [ ] Document how often originally published BUY signals are deduplicated/quarantined and whether all 33 rows remain current after slow GitHub scheduling.
- [ ] Keep previous research PRs #48–#52 draft; no merge or live strategy modifications absent user approval.

Tracking: [Funded schedule reliability issue #42](../issues/42), [Funded workstream #36](../issues/36), [stock source reliability #45](../issues/45), [master roadmap #35](../issues/35).


## Verified 24 October original source and GitHub CI results

- [PR-only original data run 40090811229](../../actions/runs/40090811229) passed both strict candle identity tests (10/10) and authentic source capture integration. Downloaded evidence ZIP SHA archive **12558459175** and examined its underlying JSON. For 17 Oct 16:00 through 24 Oct 04:00 UTC, **26 actual crypto source snapshots**, 22 observed of 40 possible UTC 4h capture bins (**55.00%**, 18 unknown unobserved bins, four duplicate-in-bin additional scans), plus five stock snapshots.
- All **858/858** raw 33-coin source rows had *actually latest completed* and correctly aligned Kraken public 4h source candles, ZERO malformed/stale/future public bar states; original outcomes **542 rejected PASS**, **314 WATCH**, **2 BUY**. Both BUY refer to the exact same **CRV/USD 2026-10-21 16:00 UTC candle**, observed at 20:22 and 23:36 UTC, with a consistent source pair and identical original engine state. **One consistent source-bar research episode**, zero source-status/pair conflicts, no Kraken Funded broker trades.
- Independently inspected [original Oct 24 stock artifact 12541914373](../../actions/runs/40048440026) with exact original rows. At **2026-10-24 01:09 UTC**, original unchanged source validation returned **28/30** Yahoo public stock OHLCV last completed **Thursday 22 October** while requiring **Friday 23 October**, despite both batch and individual retry. Two remaining names **SPCX and CBRS** have fewer than original required 252 bars. All 30 appropriately DATA UNAVAILABLE; **not 30 unsuccessful technical setups**. No changes to the actual NYSE session freshness guard.
- To narrow the source publication window without changing ANY screening or quotes, a separate **PR-only same-source recheck** now reads the frozen original 24 Oct failed stock archive and validates identical names against whichever previous NYSE session was actually completed at new research time. This does **not** reconstruct when Yahoo first published Friday bars, nor does it retroactively create missing Friday original BUY observations. Current recheck artifacts need to be inspected after CI.
