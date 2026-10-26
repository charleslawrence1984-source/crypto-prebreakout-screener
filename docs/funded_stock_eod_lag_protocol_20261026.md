# Stock EOD publication-lag audit protocol (research only)

## Existing verified observations

- 24 Oct 2026 01:09 UTC original scheduled stock archive: 28 of 30 public Yahoo histories ended on **Thu 22 Oct** while latest completed NYSE session was **Fri 23 Oct**. SPCX and CBRS were short of the original 252-session requirement. All 30 marked DATA UNAVAILABLE, **not** no setups.
- 24 Oct 2026 11:47:50 UTC independent read-only provider recheck: all 28 formerly stale Yahoo histories contained the **Fri 23 Oct** session. SPCX 93/252 and CBRS 113/252. This only bounds availability between those observations; exact first publication minute is **unknown**.
- Do not treat Yahoo NYSE EOD bars as equivalent to Kraken APP Funded's synthetic 24/7 stock quotes.
- GitHub Actions creation timestamps are **not** authenticated nominal cron trigger times.

## Collection and acceptance

1. For each observed session record capture UTC time, original frozen artifact run ID and archive digest verification provenance; use exchange-calendar expected NYSE session, not calendar-yesterday.
2. For each symbol record source, latest completed session, expected session, batch/retry path, provider error and history sufficiency; distinguish short history from stale bars and no technical setup.
3. For same-session early and later read-only observations record interval-censored first known publication time. Do **not** infer a precise publication time from a successful morning check.
4. Observe multiple distinct US trading sessions including DST boundary behaviour, market holidays and weekend transitions before comparing candidate UTC collection times. In late October the US and UK DST transitions occur on different dates; don't assume fixed 21:00 UTC close year-round.
5. Reject candidate collection times with incomplete/unknown provider freshness. Require explicit user approval for any production cron or provider change.

## What this draft does not do

The offline CSV audit script only reads **already extracted** evidence metadata. Its `archive_sha256_ok` column is a supplied verification result; it does **not** itself recompute a SHA-256 from GitHub ZIPs or validate provider OHLCV. Tests are deterministic synthetic cases, not real-world vendor latency measurements. No workflow dispatch, schedule change, new background monitor or trading action.
