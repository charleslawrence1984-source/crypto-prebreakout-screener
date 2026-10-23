# Kraken Funded Phase 3 — Official Historical OHLCVT Research, 21 October 2026

**Status: Research-only draft PR #48; never merged, executed or used to place trades.**

## Acquired data and source provenance

Kraken's official [downloadable historical OHLCVT guide](https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data) documents a complete multipart archive through **30 June 2026** and a later incremental **2026 Q3** archive. The official source CSV is headerless Unix timestamp, OHLC, base volume, trades. **Missing candles mean no recorded trades and must not be filled with invented OHLCV.**

- Kraken **FULL** five-part virtual ZIP: **9,546,553,626 bytes**, logically readable via five strictly checked HTTPS range sources without downloading or reassembling the complete ~9.55 GB. An observed five-coin extraction consumed **3,177,266 remote bytes**.
- **2026Q3** official ZIP: **576,348,633 bytes**; the same five-coin 240m/daily extraction consumed **694,063 range bytes**.
- [Read-only archive workflow 39618399899](../../actions/runs/39618399899) passed the bounded-range client tests, Q3 source catalog and five approved coin pair extraction from **both** generations; captured BTC/XBTUSD, ETH/ETHUSD, SOL/SOLUSD, NEAR/NEARUSD, HBAR/HBARUSD.
- Extracted raw OHLCVT member ZIP CRC-32 is checked by Python `zipfile`. Each selected source CSV has an independently recorded and later rechecked SHA-256. **The aggregate full ZIP's published SHA-256 cannot be verified via partial byte ranges and is explicitly NOT claimed.**
- Official Kraken [API symbols mapping](https://support.kraken.com/articles/360000920306-api-symbols-and-tickers) uses **XDG** for DOGE. After documenting that alias, the exact indexed member census found **33/33 present-day approved coins** have both 240-minute and 1440-minute members in FULL and Q3 archives. [Original 33-coin index run 39620532888](../../actions/runs/39620532888). This is *member name presence*, **not yet** verification of all 33 complete usable OHLCV histories or Kraken Funded broker membership.
- Only two sources joined: original **FULL through 30 June 2026** + **Q3 2026 through 30 September 2026**. No October 2026 candle is backdated into this frozen cohort.

The strict ZIP virtual seek/HTTP Range reader checks the documented official URL allowlist, each HEAD object length, HTTP 206 partial response, exact returned Content-Range, actual bytes received, cumulative transfer budget, path/member size and one-to-one symbol match. It rejects responses that silently return the entire archive.

## First completed five-coin multiyear point-in-time replay

[Workflow run 39620185161](../../actions/runs/39620185161): **all ingestion, source joins, original-rule replay and weekly-integrity jobs succeeded**. The archived research manifest is `funded-kraken-verified-multiyear-asof-5coins-39620185161` (artifact ID `12354875816`).

- Five real historical Kraken spot USD series included **BTC** (from Oct 2013), **ETH** (Aug 2015), **SOL** (Jun 2021), **NEAR** (Jun 2022), **HBAR** (Jul 2025), ending Sep 30 2026. Listing start is *first observed Kraken USD trade*, **not** original historical Kraken Funded eligibility.
- Original `crypto_rule_engine.score_setup` and `crypto_market_pipeline.flatten_deep_score` unchanged. 4h scoring at completed close+1m, with only completed as-of coin 4h, BTC 4h, daily, and Thursday-aligned complete seven-day weekly bars.
- Research sampled **250 candidate 4-hour clocks per symbol, spaced 42 bars (~1 week) apart**, and required **200 completed daily bars** before scoring. The spacing is greater than the longest tested 20-bar forward window, substantially reducing same-symbol label overlap. Missing/gapped or under-warmup samples are excluded explicitly.
- **957** valid original as-of technical decisions across five coins, **81** invalid/insufficient historical anchors excluded with explicit reasons. **512 WATCH**, **439 rejected PASS**, **6 original technical BUY** (ETH 2, SOL 4); **ZERO verified Kraken Funded broker BUY/filled positions**.
- Original BUY sample audit: ETH (26 Jun 2022, 24 Aug 2025), SOL (26 Jun 2022, 26 Nov 2023, 31 Mar 2024, 26 Oct 2025). All six produced a **modeled** stop-first exit with **0.8% round-trip friction**, between approximately **−1.05R and −1.16R**. Six simulated samples are far too small to infer expectancy, challenge failure likelihood, or which entry gates should change.
- Paired *descriptive future close* outcomes (no broker execution) at **+10% within 20 subsequent 4h bars**: **115** positive historical sampled anchors (BTC 13, ETH 22, SOL 38, NEAR 37, HBAR 5) and **842 non-movers**; none of the six proxy BUY scored +10% on that exact outcome measure. These labels are based on observed subsequent **CLOSE prices**, not stop-aware tradable returns or profitable positions.
- One original BUY occurred in the untouched holdout fold; this does **NOT** mean a validated trade or a signal edge. No rule optimization, live rule changes or parameter tuning is authorised by these retrospective labels.
- Saved `multi_year_original_asof_decisions.csv`, `multi_year_mover_vs_nonmover_controls.csv`, explicit per-clock error file, chronological 60/20/20 fold, descriptive price episodes, next-bar hypothetical stop-first outcomes and source/input manifest.

### Weekly price parity and volume caveat

The first strict weekly validation failed for BTC, SOL, NEAR and HBAR due to **isolated provider weekly *volume* discrepancies**. Independent offline comparison of source CSVs against the previously preserved actual Kraken native 1w feed showed **no weekly open/high/low/close divergences** in overlapping records; weekly volume had isolated differences: BTC ~0.000083%, NEAR ~0.000677%, HBAR up to ~0.002099%, SOL up to ~0.201297%, ETH none. Two affected weeks include 18 June / 10 Sept 2026 depending on coin; their exact upstream source cause is **not established**.

The follow-up research-only parity audit **still requires exact weekly OHLC price agreement**, at least 8 genuinely matched Kraken weeks, and records every weekly volume discrepancy. A **0.5% maximum relative weekly volume quarantine threshold** is applied; beyond it, the coin fails closed. The original frozen `score_setup` consumes weekly OHLC/timestamps, not weekly volume for swing eligibility. A regression test explicitly guards that distinction. The 0.5% research allowance is NOT a Kraken trading rule, license to fabricate volume, or proof of historic provider-vintage fidelity. All differences remain disclosed.

## Extended 33-coin research

`scripts/kraken_archive_33_coverage.py` checks **exact** full and Q3 archive member presence for all 33 currently approved bases. `scripts/kraken_archive_selective_extract.py --coins all` creates independently hashed raw USD 240m/daily source snapshots, preserving gaps and exclusions; current Q3/full archives are accessible without multi-gigabyte downloads.

The PR-only research workflow now has separate extraction jobs and an **independent 33-coin original-rule replay**, with mandatory per-source digest checks and a conservative minimum of 15 complete audited historical coin series. Until its actual artifact is inspected, **33-coin technical coverage and results remain unverified**. No trading rule, user order, funded broker account or live scheduled workflow was changed.

## Remaining pre-release safety and statistical evidence gaps

- No historical Kraken Funded instrument access, official bid/ask/fees/financing/fill data, actual leverage/margin constraints or official challenge equity reconstruction.
- Historic provider archive rows may reflect backfilled revisions rather than exact contemporary historical vintages; future OHLCV information is filtered out of the model input, but **point-in-time publication guarantees** cannot be established solely from an archive downloaded in 2026.
- Current 33-coin universe creates listing/survivorship bias; truly historical eligible/delisted market universe remains unavailable.
- Unequal lifetime histories, trade-less gaps and rule warmups create *different* sample sizes per coin and cannot be silently filled.
- 60/20/20 chronological folds are descriptive. Tuning must be restricted to the training segment, with untouched holdout genuinely unused for parameter decisions.
- Simulated assumed slippage, stop fills and risk exposure must be validated separately; a later upward closing price does not imply the stopped-out strategy could have captured it.
- **PR #48 stays DRAFT and UNMERGED.** Any merge, live rule tuning, recovery dispatch, broker access or orders require separate explicit authorisation.
