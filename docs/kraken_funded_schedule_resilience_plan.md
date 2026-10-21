# Kraken Funded schedule resilience — safe shadow design (draft)

This is issue #42's read-only diagnostic experiment, separate from draft PR #34.
It **does not deploy an independent monitor**, reschedule any live workflow, fire
alerts, dispatch missed jobs, backfill market observations, change investment
rules or claim that a funded opportunity was missed.

## Baseline and observed limitations

GitHub's default-branch `funded-forward-research.yml` is intended to start
public crypto research at `35 0,4,8,12,16,20 * * *` (UTC) and US-stock
research at `45 22 * * 1-5`. Recorded run creation times vary widely from
these scheduled clock times. Prior three-day replay of 18–20 Oct found
**7 of 17 mature crypto nominal windows without a matching run**, but GitHub
workflow-run REST metadata does not expose the exact original cron occurrence
for an exceptionally delayed job. Those 7 windows do **not** prove 7 events
were definitively skipped.

Job-level attribution must inspect the actual non-skipped stock or crypto job;
a successful all-workflow conclusion is insufficient. Monday 19 Oct stock
research only started Tuesday 20 Oct 01:17 UTC, crossing midnight. Runs that
start *before* a later nominal slot cannot be evidence for that future slot.
Even if a run exists, frozen ZIP **metadata** does not prove JSON integrity,
market-data freshness, broker-market availability, eligible BUY or a fill.

## Phase 1 — this PR: read-only technical feasibility

- A separate `scripts/funded_schedule_shadow.py` queries GitHub's scheduled
  workflow runs, real asset jobs and ZIP metadata, and emits JSON with per-slot
  inference labels. No mutation API is used.
- 90-minute crypto and four-hour stock grace periods suppress premature
  missing-window alarms. Missing job metadata / partial pagination / failed
  API calls are *unknown*, never green or confirmed scheduler misses.
- Single-scan output includes no alerts, no state storage and no scheduled
  `cron` on the default branch. A PR/manual regression workflow may execute
  tests and a three-day observational replay, without any job dispatch.
- Unit tests verify cross-midnight stock runs, future-slot boundaries, skipped
  asset jobs, expired/empty artifacts, lost metadata and incomplete history.

## Phase 2 — design-only minute-offset comparison

Keep the production schedule unchanged for now. Candidate **research**
comparison: original `:35` at UTC 4-hour intervals versus a `:17`
offset, evaluated across equal chronological windows *only if owner approves
a separately reviewed cron change*. This is not evidence that :17 improves
GitHub scheduling.

For each window record expected asset/slot, observed run and job outcomes,
relative start-delay distributions (median, P90, worst), no-run-observed
frequency, capture-to-last-completed-market-bar lag and verified ZIP contents.
Stratify by UTC hour and weekday; account for endpoint downtime, workflow
changes, sample size and the unknowable original cron occurrence. Compare
overall completeness and short-tail freshness, not merely created_at counts.
Avoid treating a moved cron time as a trade signal improvement.

## Phase 3 — proposed independent monitor, not authorised here

Propose a **separate** low-frequency, read-only default-branch status job,
if/when approved. It would query the latest captured asset-specific jobs and
send **diagnostic warnings only**, with no self-dispatch. Before enabling any
repeated notifications, specify cooldown/deduplication persistence, explicit
grace periods, API rate-budget tests, partial-history handling, and how this
would coexist with the user's existing daily ChatGPT status automation.
Do not create duplicate notifications without explicit owner approval.

An automated recovery system is a **different change** requiring separate
approval, careful backpressure/deduplication/permission review, and no
retroactive fabrication of missed frozen snapshots. No risk of order
execution may be introduced.

## Acceptance evidence

- Unit tests pass under PR GitHub Actions.
- Real-history shadow job returns only inferred/no-match labels, with no
  claim of exact trigger attribution, broker fill, data integrity or alpha.
- No active production schedule, new recurring alert, workflow dispatch,
  broker connection or code merge before separate user approval.
- Rule backtesting in issue #43 cannot treat an unobserved schedule as a
  real historical signal.
