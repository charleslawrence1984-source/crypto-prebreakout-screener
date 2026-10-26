# Kraken Funded P0 — Four-hour capture resilience candidate, 26 October 2026

**DRAFT ONLY. NOT DEPLOYED. The proposed extra scheduled event cannot run until this PR is merged to main, and any merge requires the user's separate express approval.**

## Observed root problem
The 26 Oct GitHub source audit run 40286484388 authenticated 34 original Kraken Funded research crypto snapshots (1,122 source rows). Only 30 of 52 UTC four-hour observation bins had a capture (57.69%). The other 22 are UNKNOWN, not no-signal. Every captured public Kraken source bar was completed and correctly timestamped. The two observed BUY rows were the same CRV source candle, not two entries. Actual GitHub event start times were unreliable; REST does not disclose original triggering cron identities.

## Staged candidate only
- Retain the current :35 UTC six-times-daily public research scan. Add one **backup :55 UTC** scan at the same six four-hour boundaries. Both depend on GitHub scheduling, so two events do NOT guarantee punctual capture.
- For a scheduled scan, calculate the last fully completed public Kraken four-hour candle from ACTUAL execution time (+1-minute finality). No replay of older candles and no backdating to a presumed cron time.
- Before a scheduled scan, fetch recent original main-branch GitHub artifact ZIPs via read-only API, inspect the frozen original JSON, verify SHA-256 and the approved 33-coin source candle metadata. If a full original source bar was already captured, skip the extra research scan, with an explicit admission record. If a prior scan has incomplete source data, a fresh authentic attempt is permitted within the operational deadline.
- Cap research scan start at 90 minutes after bar close. Older runs or GitHub API/ZIP verification failures fail closed, with an audit record (not false-green). This is an observational timing rule, not a revised BUY rule.
- Serialize scheduled runs using the workflow concurrency grouping. Retain existing standalone stock schedule and default manual workflow_dispatch research semantics.
- Retain exact original trading rules, public Kraken research-only data, immutable sources, and NO broker order/position/stop/quote connection.
- A read-only PR-only CI runs deterministic tests and independently probes genuine existing GitHub archives without scheduling anything.

## Caveats / remaining blockers
- GitHub can still delay/drop BOTH redundant scheduled events. The new candidate may improve reliability but cannot independently guarantee timing. If insufficient, an external independent scheduler must be explicitly configured separately, not silently installed.
- Workflow concurrency and snapshot-check-then-run are not a distributed atomic lease. Race risk remains if two scans start before the first has uploaded an archive. Downstream unique coin+source-bar paper admission in draft PR #57 remains mandatory.
- Missing observational source windows are UNKNOWN; do not create later fictional BUYs for them.
- A green workflow might represent an authenticated duplicate SKIP, not a fresh scan. Always inspect each admission decision record.
- No funded-account instrument eligibility, executable spread/quote, manual stop, intrabar risk or prospective strategy edge has been certified.

## Release gates
1. CI and PR-only real original archive source recheck pass; document counts of eligible/late/duplicate/missing evidence.
2. Review proposed new cron/permissions/concurrency changes and bounded API/runner costs.
3. Measure equal prospective windows and avoid declaring success based only on increased GitHub run count.
4. Explicit user permission before merging or enabling a second scheduled capture.
5. Rollback: revert only the candidate changes to funded-forward-research.yml and keep all original archived evidence. Never erase UNKNOWN.

Tracking: GitHub issue #42; previous source-idempotence PR #57, both unmerged.
