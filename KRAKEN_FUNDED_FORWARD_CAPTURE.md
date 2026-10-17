# Kraken Funded — prospective Stocks + Crypto research capture

**Implementation: DRAFT BRANCH ONLY. NOT ACTIVE ON THE LIVE PLATFORM.**

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

## Proposed collection schedule — UTC

- **Crypto:** every four hours, 00:35 / 04:35 / 08:35 / 12:35 / 16:35 / 20:35.
  Capture only completed 4h/daily/weekly Kraken public spot candles.
- **Stocks:** 22:45 UTC, Monday–Friday, after the regular US closing
  session. Capture validated completed Yahoo public daily price candles.
- Optional manual `workflow_dispatch` can run either or both.
- GitHub scheduling is **best effort**; skipped/delayed runs are not represented
  as scans, and failed runs do not indicate no opportunities.
- GitHub scheduled workflows run only when the workflow file is on the
  **default branch**. The unmerged PR is NOT yet capturing automatically.

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

## What remains before the first unattended run is trustworthy

1. Review PR and confirm it introduces no broker credentials/order path.
2. Run a controlled manual STOCK and CRYPTO collection against *current*
   public providers on the audit branch; inspect actual 30/33 instrument
   coverage, HTTP/CCXT failures, scores, start/end timestamps and artifacts.
3. Confirm the results correspond to what the Streamlit interactive engine
   reports on equivalent completed candles, and record any divergence.
4. Verify rate-limit behavior, retry/backoff and job execution time. A
   one-off mocked test does not prove public data sources will run reliably.
5. After all above checks, separately approve/merge the research-capture
   workflow; only then will its default-branch schedule become active.
6. Add prospective **outcome assessment** based on later market prices and
   pre-frozen entry/stop/target plans. Do not count WATCH, near-misses or
   missing quotes as actual executed winning trades.
7. Make drawdown, gaps, slippage, differences in Kraken Funded product quotes,
   24/7 stock execution, earnings and funded challenge account-loss rules
   explicit in the go/no-go analysis.

**No live funded trades or pension investments are authorised.**
