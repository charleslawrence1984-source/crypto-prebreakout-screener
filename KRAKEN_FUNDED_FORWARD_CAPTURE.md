# Kraken Funded — prospective Stocks + Crypto research capture

**Status on 18 October 2026:** Original public-market **forward capture is already running on main** for funded Crypto, and weekdays for funded Stocks. The new **coverage-health and prospective outcome journal** are only staged in draft PR #31; their daily automated workflow is NOT yet deployed to main. No broker orders, funded account access, or strategy-performance claims.

## Scope
The scheduled prototype captures **all assets** from the currently approved
`kraken_funded.py` lists, not from Trading 212 or the broader cryptocurrency
discovery universe:

- 30 Kraken Funded stock tickers (from the existing source-of-truth list).
- 33 Kraken Funded cryptocurrencies (existing source-of-truth list).

**Original scoring rules are unchanged.** The runner uses the current
`trade_rules.evaluate_price_setup`, `funded_stock_breakout.evaluate_funded_breakout`,
the original `funded_trend_watch` function, and
`crypto_rule_engine.score_setup` plus `flatten_deep_score`.
Any existing user-driven account/quote/earnings confirmation is intentionally
omitted from the unattended job rather than faked.

The headless snapshot is a PUBLIC-MARKET research observation, **never** an
equivalent to a confirmed green Kraken Funded READY label or a trade signal
approved to execute.

## Active frozen capture schedule — UTC

- **Crypto:** every four hours, 00:35 / 04:35 / 08:35 / 12:35 / 16:35 / 20:35.
  Capture only completed 4h/daily/weekly Kraken public spot candles.
- **Stocks:** 22:45 UTC, Monday–Friday, after the regular US closing
  session. Capture validated completed Yahoo public daily price candles.
- Optional manual `workflow_dispatch` can run either or both.
- GitHub scheduling is **best effort**; skipped/delayed runs are not represented
  as scans, and failed runs do not indicate no opportunities.
- The capture workflow is already present on the default branch and has successfully
  stored three full 33-coin Crypto runs as of 18 October. Stock public-provider
  smoke on 17 October recorded 28/30 usable daily histories, with SPCX/CBRS
  under the required 252 bars. A green workflow alone does not mean BUY.
- The staged new read-only outcome journal is separate and is NOT yet scheduled
  on main. Its PR preview has audited three original production Crypto
  archives (99 coin observations), zero original BUYs and zero shadow fills.

## Frozen audit evidence

Every execution creates a uniquely named JSON file with:
- Actual scan end timestamp in UTC and GitHub run ID/attempt.
- All approved assets, original technical state and reason, the last
  completed market candle date/time, plan levels and scoring where returned.
- Original scoring engine ID and public market-data provider.
- Explicit `DATA UNAVAILABLE` or `SCAN ERROR` entries; no silent dropping.
- `research_only=true` and `broker_account_connected=false` markers,
  with SHA-256 over canonical payload for accidental modification detection.
- Only files created exclusively: no in-place overwrite or retrospective
  backfill of a prior scan ID.

One artifact is uploaded per completed job even when the provider reports an
error (if a valid diagnostic file was generated). GitHub artifact retention is
**90 days** at most as configured here, and artifacts are not an immutable
audit ledger in the forensic sense. Export a more durable archive later if
longer-term forward evidence is needed.

## Prospective frozen outcome testing (draft PR #31 only)

New modules: `funded_forward_health.py`,
`funded_forward_outcomes.py`, `funded_forward_outcome_runner.py`.

- Every source snapshot is checked for the original canonical SHA-256, exact
  approved 30/33 asset list, UTC provenance and research-only contract. A
  missing archive or broken signature FAILS the audit, rather than silently
  counting a no-trade.
- Only **original Crypto BUY** or a specified original funded Stock entry
  state qualifies for shadow-entry evaluation. A Crypto `PASS` means the
  original engine **rejected** the setup; WATCH is not a trade.
- Qualified rows still need immutable entry, stop, target, score and R:R >=2;
  Crypto also needs original timing, eligibility, 80+ score and public Kraken
  spot-liquidity confirmation. These are a **paper admission veto**, not a
  modification or bypass of the live funded rulebook. No broker confirmation
  is ever fabricated.
- Research entry convention: hypothetical **buy-stop at the frozen entry**,
  filled only if the **subsequently completed bar** trades up to it. If a
  later bar opens above the level, use the worse open price; veto entries with
  resulting R:R below 2. Entry may remain unfilled for 3 regular US stock
  sessions or 6 Kraken 4h candles. No entry means no trade.
- Shadow exits: frozen stop or first target; stop wins on a bar touching both.
  Open below stop on a subsequent bar is filled at the worse open. Still-open
  hypotheses can expire at completed-session/4h horizons of 20/42 bars and
  close at the final proxy close. **0.5% roundtrip** is a *sensitivity cost
  assumption*, NOT a confirmed funded spread, fee or financing charge.
- Uses only **future completed candles** after the already-recorded UTC
  snapshot. US stock Yahoo 1d after the regular market close; crypto public
  Kraken 4h OHLCV. Missing/interrupted observations remain DATA UNVERIFIED;
  incomplete current bars and the entry-signal candle can never affect returns.
- Read-only action proposed as `funded-forward-outcomes.yml`, 23:37 UTC
  daily *after separate approval to merge*. It reads up to 30 days of actual
  default-branch frozen source artifacts and writes a new, unique output
  artifact each run. GitHub schedule is BEST EFFORT; 30-day rolling history
  and 90-day GitHub artifact retention are not long-term archival guarantees.
  Existing snapshots are never rewritten.
- Repeated signals across successive snapshots can refer to the SAME asset
  and price move. They must NOT be compounded or presented as an independent
  portfolio or strategy P&L. The first edition intentionally has NO funded
  equity curve, challenge win-rate, claim of execution or broker fill data.

## Real PR preview baseline (18 Oct 2026)

The new prospective journal successfully read **three authentic production
Crypto frozen archives, 99 original coin observations, zero original entry
triggers and zero simulated fills**. This correctly means **no strategy P&L
evidence yet**. Only future qualifying original signals can create shadow
entries. Stock forward capture requires weekday regular-session data.

## Still blocked before declaring the funded challenge trade-ready

1. **Separately approve merging draft PR #31** only after latest regression
   and public-provider preview results; no changes are authorised to
   PR #20, #22 or #30 by this work.
2. Verify the outcome daily job succeeds over several independent scheduled
   runs, survives skipped GitHub schedules, and archives data beyond 90 days.
3. Inspect future original BUY/entry candidates for actual funded UI parity
   and account execution constraints. No signals currently exist to
   performance-validate.
4. Verify live funded account eligibility, exact product quotes, spreads,
   24/7 equities conventions, official earnings windows, financing fees,
   stop behaviour during gaps, and real permitted maximum account drawdowns.
5. Establish prospective, distinct-trade paper portfolio/risk accounting,
   including overlapping signals, maximum open risk, 12% target, and 3%
   failure limit. Do not infer a challenge pass from an unconnected,
   public-price-only research journal.

**No live Kraken Funded transactions or pension transfers are authorised.**
