# Kraken Funded reliability evidence — 20 October 2026 (UTC)

**Research only; no broker connectivity, trading authority, workflow recovery, or deployment.**
This is a frozen review checkpoint of GitHub evidence, not a future monitoring promise.

## Deterministic CI and actual GitHub replay

- Draft PR [#34](../pull/34) head `b67f8916c015bfd664281433767e4bb42c7feb64`.
- Read-only GitHub Actions run [39505119426](../../actions/runs/39505119426) **passed**. Actual job log confirms **11 schedule-audit tests + 6 freshness-watchdog tests = 17 tests passed**; Python compile passed.
- The same CI executed the three-day authenticated read-only GitHub API replay, completed successfully with `history_complete: true` and **zero job metadata errors**.
- In mature crypto slots **18 Oct 00:35 through 20 Oct 16:35 UTC**, **7 of 17 intended 4-hour slots had no matching asset-specific scheduled run observed**. Ten had successful crypto jobs assigned by bounded timestamp inference.
- Successful US-stock job at **20 Oct 01:17:52 UTC** was associated with the **Monday 19 Oct 22:45 UTC** stock slot, not with Tuesday's 00:35 crypto slot.
- Crypto run **20 Oct 20:05:59 UTC** was assigned to **16:35 UTC**; it cannot prove the future **20:35 UTC** cron event occurred.
- **Critical uncertainty:** GitHub returns actual creation times and job outcomes, *not* the original scheduled cron event ID. An exceptionally delayed run could belong to an earlier slot and be inferred into a later slot. Therefore `no_matching_run_observed` is not a proven scheduler-drop count; never reconstruct missing market observations or treat these as retrospective technical scans.
- Full CI report and individual slot/run IDs are in the [read-only replay job log](../../actions/runs/39505119426).

## Frozen JSON integrity and coverage (raw artifact bytes inspected)

Each row below was unzipped and independently checked: JSON parses, SHA-256 canonical payload digest recalculates to the stored `payload_sha256`, row counts match expected universe, symbols are unique, `broker_account_connected=false`, and associated health metadata examined. These are *public research snapshots*, not executable Kraken Funded prices.

| Asset | Scheduled GitHub run ID | Captured UTC | Valid histories | Original research statuses | Stored SHA-256 payload |
| --- | --- | --- | --- | --- | --- |
| Crypto | 39280869232 | 19 Oct 15:52:26 | 33/33 | 9 WATCH, 24 PASS | `5872ba0dccfa44b00df1cfd07dc806ef6763adfe6e5b4653f01af1a1069be8a9` |
| Crypto | 39325615547 | 19 Oct 21:14:58 | 33/33 | 11 WATCH, 22 PASS | `7c92444101a099bb1abc4c1ee3245398e015698730c77f3fa9ab47dca0423381` |
| Crypto | 39373444977 | 20 Oct 05:12:58 | 33/33 | 17 WATCH, 16 PASS | `abee6bddfe522ff6ba59b80b0628497cf053b55af4ecabcfc9e3cfff1bc49369` |
| Crypto | 39433577230 | 20 Oct 13:51:02 | 33/33 | 12 WATCH, 21 PASS | `b8a673db29221a2cfe28abd4be86c034ceaa7f0bf638bba2f5bdd9fcf9cf62c7` |
| Crypto | 39489082117 | 20 Oct 20:08:45 | 33/33 | 19 WATCH, 14 PASS | `4ac40c904bc2846e9afded2a9ae52e3744d6157cf39a691ff199b1553184a468` |
| Stocks | 39352642351 | 20 Oct 01:18:35 | 28/30 | 22 BLOCKED, 4 WATCH, 2 FUNDED TREND WATCH, 2 DATA UNAVAILABLE | `861c9344a7fae43e7c3494f957259632fb2c1145f11e6194b0b0ef1bc12cd973` |

**All six archived snapshots passed the canonical SHA-256 integrity check.** In the reviewed interval, only **one** stock scheduled archive is available; do not assert five stock-run consistency.

### P0 follow-up: verify stock-bar staleness before stock readiness

- The stock snapshot was captured at **20 Oct 01:18 UTC**, corresponding to the Monday 19 Oct late-evening New York trading period. Yet **all 28 usable stock histories reported last daily bar = Friday 16 Oct**, not Monday 19 Oct.
- This could be provider publishing latency, cache behaviour, dataset session semantics, or stale provider data. It is **not confirmed as an ordinary healthy stock capture** merely because the workflow succeeded and the SHA-256 is valid.
- **SPCX** had only **88/252** completed historical bars and **CBRS 108/252**; both are explicitly marked DATA UNAVAILABLE and are excluded from usable histories. There were no archived health bar warnings, despite the apparent last-date lag.
- Need inspect stock history retrieval and per-market close/freshness criteria, compare next available captures, and add a failing-safe per-asset bar-date requirement before any funded-stock readiness claim.

### Tradeability / risk limitations

- None of the six archived health results confirmed any funded trade-ready candidate; WATCH is not an order and crypto `PASS` is a rejected original technical state.
- Artifact integrity and asset accounting are necessary but do **not** validate broker quotes, fills, costs, funded-account daily loss rules, protected exit availability, profitability or strategy success.
- **#40:** CI and read-only history replay proven; schedule-source attribution remains fundamentally uncertain.
- **#41:** Archived crypto 5-run coverage and all available stock research ZIPs checked; fresh stock price bar dating remains open.
- **#42–#44:** No monitoring deployment, backtest result, strategy rule change, funded risk simulation, or trade authorization performed.
