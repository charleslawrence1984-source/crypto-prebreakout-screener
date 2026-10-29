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


## 29 October 2026 — independent main-archive and release-gate audit (research only)

Actual main scheduled run artifacts were downloaded as ZIPs and their frozen JSON rows independently inspected; canonical `payload_sha256` was recomputed using `json.dumps(payload_without_digest, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)` and matched the embedded digest for **all five** captures below. This proves the inspected payload's internal integrity, not vendor authenticity, broker quotes or the GitHub artifact ZIP's independent provenance.

| Main scheduled run / artifact | Actual capture UTC | Original completed source candle open (UTC) | Rows / status | Digest (canonical JSON) | Observational first-capture lag after candle close |
|---|---|---|---|---|---|
| [40642347810](../../actions/runs/40642347810) / 12796098341 | 28 Oct 06:14:14 | 28 Oct 00:00 | 33/33 crypto, all rows complete | inspected, matched | 134.2 min — **late** under candidate 90-min gate |
| [40715365202](../../actions/runs/40715365202) / 12828666066 | 28 Oct 15:25:43 | 28 Oct 08:00 | 33/33; 17 WATCH, 16 rejected PASS | `3bd4cad24ac17e3522b3f668fd233f8e593d6c2ca8880fdb15a01309d11c3838` | 205.7 min — **late** |
| [40770466625](../../actions/runs/40770466625) / 12852222986 | 28 Oct 21:18:30 | 28 Oct 16:00 | 33/33; 22 WATCH, 11 rejected PASS | `fe1ca10bb6b4bbe96b610b6e5a5b9943b5ebaf02a62043ed854950c3a8b9682e` | 78.5 min — within 90-min candidate window |
| [40828663060](../../actions/runs/40828663060) / 12877867185 | 29 Oct 06:23:44 | 29 Oct 00:00 | 33/33; 11 WATCH, 22 rejected PASS | `90f49b3d632ce0ad0062e9253472cf4afd92c8d5679f6ea6c53b9a7d67b660c9` | 143.7 min — **late** |
| [40803449379](../../actions/runs/40803449379) / 12866753029 | 29 Oct 01:57:50 | NYSE 28 Oct session | 30/30 stock rows, 28 usable, SPCX 96/252 and CBRS 116/252 short-history | `3c544f8e1d93727666f6f9e5400b3b55c3ed2e01ab6397f5fede9c5863c6a1bf` | Stock-only; separate NYSE session gate |

All four inspected crypto archives: **33/33** rows, **zero** unavailable/error rows, source timestamps internally uniform and from completed public Kraken four-hour candles; no original technical BUY and no funded READY. Stock: **28/30** valid completed 28 Oct NYSE bars, **two** DATA UNAVAILABLE for insufficient 252-session history; no verified stock READY. Green jobs are not interchangeable with 33/33 or 30/30 *usable* coverage.

**Observed source-candle gaps:** within the 28 Oct 00:00 through 29 Oct 00:00 UTC original source-bar-open sequence, the 28 Oct **04:00, 12:00 and 20:00** four-hour candles have **no archived main capture** in the examined scheduled-run sequence. Mark each UNKNOWN, not PASS and not a proven GitHub dropped trigger. Captures occurred in only four of seven completed source-candle intervals in this period. Main run creation time does not expose a reliable nominal cron-trigger ID. The 90-minute gate labels above are **retrospective diagnostics** only: draft #58's admission logic was **not deployed** and did not actually decide these main captures.

**Forward journal:** [read-only run 40808167954](../../actions/runs/40808167954), artifact 12868754246, recorded 42 crypto and eight stock snapshots, two original CRV BUY observations but only **one** distinct admitted and closed paper position, one paper stop, realized-only modeled balance $997 from $1,000; zero broker trades, zero funded READY. WATCH and rejected PASS must not be counted as orders or as independent completed trades.

**CI at prior PR heads:** #58 head `b0771a961890` had two green candidate validation jobs plus separate read-only capture research on 26 Oct; 13 deterministic admission tests and one genuine source-archive probe, but **no prospective backup schedule run**. #57 head `0c6c457c6af7` had three green source-identity, original archive and Yahoo recheck jobs on 26 Oct. #59 head `ec6d487ced11` had 13/13 deterministic episode tests green on 27 Oct. These do **not** prove an atomic distributed lease, future schedule punctuality, incident recovery or strategy expectancy.

**Approval gate is NOT met.** Before asking for merge/production schedule approval: (a) show prospective equal-window original-vs-backup admission evidence with source-bar IDs and SHA-valid skip/fail-closed counts, not estimated trigger attribution; (b) test simultaneous duplicate/race cases including manual dispatch vs scheduled events, artifact-upload latency, API pagination/outage, and failed/incomplete first capture; (c) rehearse an incident response and rollback to exactly the original `:35` research cron, unchanged stock `22:45` cron, with archived UNKNOWN windows preserved; (d) establish an independent timing source if GitHub remains unreliable; (e) request separate explicit owner approval. No dispatch, merge, workflow change, rule change or broker action was performed in this audit.
