# Kraken Funded — original BUY first-observation lag safety (26 October 2026)

**Research-only increment to [draft PR #57](../pull/57). NOT merged or deployed. No new cron, changed scoring rules, changed trading thresholds, Funded APP credentials, order execution, or automatically protected stops.**

## Why this is necessary
An as-of original 4h technical BUY may be correctly SHA-authenticated and source-candle-completed but first **observed** too late to support an honest prospective entry at the original trigger time. Multiple completed original snapshots may contain the same BUY for one coin and source candle. An old duplicate must not become a second independent opportunity.

The original 24 Oct source review found 26 actual crypto snapshot archives, two CRV original BUY observations referring to **one** 21 Oct 16:00 UTC candle (first capture 20:22 UTC, repeat 23:36 UTC); 18/40 four-hour capture bins were unobserved/UNKNOWN. One later CRV public-candle paper stop was recorded, zero Funded APP broker fills.

## New conservative research-only check
- Feed the output of the existing **SHA-validated** `funded_capture_candle_identity.py` audit into `funded_forward_signal_safety.py`. Both run on a dedicated PR-only GitHub Actions workflow, never production cron.
- Explicitly require the original identity schema; valid real capture count; ledger-and-summary parity; deduplicated unique coin+candle episode; real, not future, capture clocks; candle aligned and closed with +1-minute finality; original BUY state; identical source pair/state among observations.
- Take the **first real observed source capture**, not the nominal scheduler cron time and not a late second observation, and report lag in seconds after the original candle close. Keep duplicate captures descriptive, not new paper entries.
- A 60-minute research-quality cutoff flags `PROMPT_COMPLETED_CANDLE_RESEARCH_ONLY` vs `LATE_FIRST_OBSERVATION_RESEARCH_ONLY`; it is **a conservative operational diagnostic, not a validated live trading signal or new strategy rule**. Parameter is bounded 1–240 minutes and MUST NOT be optimized on examined historical holdout.
- Quarantined contradictory/missing-source episodes cannot become timely candidates. Unobserved UTC bins stay **UNKNOWN**; no historical entries are manufactured.
- **Always report zero Funded-ready signals**. No Funded APP instrument eligibility, bid/ask, ability to manually exit without automatic advanced stops, actual account equity or hard $970 breach protection have been proved. Public Kraken spot source freshness != executable Funded quote.
- Ten new deterministic safety tests, including the real observed CRV two-capture timing example, late first observation, duplicate replays, conflicting state/pair, missing bins, tampered aggregate counts, future capture and invalid/mutated source SHA.

## Next proof and release gates
1. The PR-head CI must pass the original 10 source candle tests **and** ten new latency tests and successfully download/check current main original archives. Inspect the retained JSON output, not just green job status.
2. Study whether new unique original BUY events occurred since 24 Oct and whether first-capture lag and 4h missing-bin rates improved or worsened. Never treat lack of data as no signals.
3. Separately design a reliable 4h capture scheduler, real provider freshness/unknown-slot monitor, idempotent research observation and rollback. **Production scheduler edits need explicit owner approval and dedicated independent testing.**
4. Obtain Funded APP account-specific allowed pair and executable quote/spread tests and manual stop-exit plan; only then explore fully broker-validated static -3% risk gate. Do not infer account profitability from spot proxy.

**Status: DRAFT research, unmerged.** Existing original indicator thresholds untouched. The Kraken APP Funded +12% target / fixed -3% starting-equity loss floor are distinct from Kraken Pro Prop program conditions.
