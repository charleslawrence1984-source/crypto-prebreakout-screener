# Point-in-time historical data sourcing — CL Signal Managed Pension

**Status: DATASET NOT YET ACQUIRED; LONG-TERM INVESTMENT BACKTEST NOT VERIFIED.**
This is an evidence-sourcing plan, not a return claim or live trading data product.

## Candidate authoritative fundamental filings

**US listed companies: SEC EDGAR filings and XBRL**

Official: https://www.sec.gov/search-filings/edgar-application-programming-interfaces

- Company filings are available through the SEC submissions endpoint with accession
  identifiers; structured financial facts are exposed by companyfacts and companyconcept.
- For historical 2015 decisions, the research engine must use only the **specific
  filed accession and reported facts then published**. Fetching today's convenient
  historical fact time series by itself is not enough: later amendments,
  restatements and later-filing context can contaminate a simulated older date.
- Save the original record, accession, earliest-publication timestamp, amendment
  timestamp, metric/tag/context mapping, financial units and content hash.
- Model selection must run with an explicit *as-of* cutoff, never Yahoo's current
  company.info or today's balance sheet when replaying past periods.
- SEC offers financial filing history, **not a survivorship-complete security-level
  total-return database or Kraken/Trading 212 execution history**.

**UK companies: Companies House public filing history and XBRL products**

Official:
- https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference/filing-history
- https://www.gov.uk/guidance/companies-house-data-products

- Filing-history requests require a Companies House developer API key.
- The free electronically filed XBRL accounts data product covers only a
  subset of company accounts; many older and non-electronic accounts are not
  included, and formats/mappings vary. Public historical filings are not a
  ready-to-use complete stock-fundamentals backtest dataset.
- Additional work will be needed for financial-account harmonization,
  release timestamps, consolidated/unconsolidated statements and UK listed
  corporate-action history.

## Essential non-fundamental data — still missing

1. Historical **investable universe on each trade date**, including securities
   subsequently delisted, bankrupt, acquired, renamed or suspended.
2. Survivorship-complete OHLCV/total-return series with delisting proceeds/losses,
   cash dividends, splits, stock rights, spin-offs, and relevant market taxes.
3. Historical GBP FX rates and symbol/ISIN exchange mapping to the actual
   primary line accessible from the intended broker at the relevant time.
4. Historical market/industry classifications, earnings release dates and a
   verified cost/spread model; especially important for small and foreign stocks.
5. Original CL Signal Investment model frozen/versioned and independently
   reconstructed decisions. The current live engine reads recent Yahoo annual
   financial statements, and **cannot be used as a historical point-in-time
   fundamentals engine unchanged**.

**Decision:** a recent-survivor price-only backtest (e.g., Yahoo 5y candles
for today's constituents) is *not* a valid 10-years-to-forever pension test.

## Implementation already available on this audit branch

- `pension_point_in_time.py`: reject future filings/amendments and unavailable
  historic quote/universe evidence; this does not authenticate source claims.
- `pension_portfolio_replay.py`: replay already-scored signals at next historical
  session opens under whole-share, 5% stock and 20% sector ceilings; dividend
  ex-date entitlement and trading costs. Uses **synthetic fixtures only**.
- `pension_return_accounting.py`: compare externally flow-adjusted, dividend-
  inclusive GBP strategy NAV to exactly date-matched GBP total-return benchmarks.
- `pension_fund_comparison.py`: refuse misleading rankings when provider funds
  have different return windows or fee bases.

## Proof required to continue with a genuine backtest

- Acquire lawful, sufficiently broad and documented PIT filings and market
  datasets, with delisted coverage and all adjustment events.
- Confirm 10+ years of usable historical snapshots at required score inputs
  and document coverage/missingness instead of filling gaps with future data.
- Run unchanged strategy scoring on each frozen historical date; make a
  **development period** and an untouched **holdout period**, with fully
  time-aware simulation and actual historical market availability.
- Show observed **number of unique eligible companies**, number of buys, years
  and market regimes, net returns/costs, volatility/drawdown and realistic
  uncertainty. Compare against diversified low-fee fund benchmarks matched
  by risk and currency.

Never label this branch "pension-ready" based on source contracts and
synthetic unit tests alone.
