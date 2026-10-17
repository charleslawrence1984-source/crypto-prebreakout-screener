# CL Signal — Independent Pension and Kraken Funded Readiness Audit

Status: **NOT APPROVED FOR PENSION TRANSFERS, PENSION SHARE PURCHASES, OR NEW FUNDED LIVE TRADE RULES**.

This document describes a staged, reproducible audit. Passing a software unit test does
not prove that an investment strategy will earn positive returns. Retain the exact
Investment and Funded trade scoring models until a separate proposal has been tested.

## Page and strategy ownership

| Module | Intended role | Decision rules |
|---|---|---|
| Stocks | Swing/shorter-term stocks trade engine | Existing Trade scoring, unchanged |
| Managed Pension | Separate *research only* area for ten-years-to-forever shares | Existing `strategy_scores_v3.py` and `two_strategy.py`, unchanged |
| Crypto | Standalone spot/accumulation research | Existing crypto engines, unchanged |
| Kraken Funded | Separate $1,000 fixed-floor challenge research and (later) manually verified setups | Existing Funded stocks rules, unchanged; crypto research remains separate |

The original Stocks page still contains legacy Investment views during migration.
Do **not** hide/remove them until the new pension page has equivalent features and
tests. This is a UI-route separation milestone, not completed business-logic migration.

## Pension strategy release gates

1. **Provider contract:** Obtain written confirmation of transfer eligibility,
   protected benefits, charges, loss of discounts, and whether existing providers
   accept other pension transfers. Never instruct transfers from the current
   employer plan or military scheme as part of this project.
2. **Comparison methodology:** Request the same cut-off date and identical trailing
   periods (1, 3, 5 years when possible) for fund-level **total returns in GBP**,
   with reinvested distributions and net/gross fee basis clearly labelled.
   Show volatility/drawdown, asset mix, lifestyle de-risking, platform/scheme fees
   and whether historical returns correspond to a member's actual fund series.
   If one observation is missing, mark **NOT COMPARABLE** instead of ranking.
   Personal pot growth minus contributions is *not* a valid time-weighted fund return.
3. **Data:** Acquire *point-in-time* fundamentals and publication/revision dates,
   historical index membership, dead/delisted companies, corporate actions,
   FX, all dividends and corporate events. Never score a 2017 company using its
   2026 balance sheet or today's survivors.
4. **Engine:** Replay *unchanged* Investment selection, initial BUY, HOLD,
   BUY MORE, REASSESS and SELL actions on realistic available-at-the-time
   snapshots; implement calendar, deal timestamps, total-return portfolio
   accounting and uninvested cash.
5. **Portfolio constraints:** Verify 5% maximum company weight and 20% sector
   ceiling, including cash, currencies and prices. The old Stocks app currently
   *displays warnings* rather than enforcing blocked orders.
6. **Costs:** Include spread/slippage, FX conversion, applicable market taxes,
   corporate actions, transfer out-of-market time and fund/provider fees.
   Model contributions only when specifically included in the scenario.
7. **Performance:** Compare *like-for-like risk* with global equity and a
   balanced portfolio. Report CAGR, annual returns, max drawdown, volatility,
   Sharpe/Sortino as supporting metrics, time in cash, turnover, worst windows,
   FX exposure, and how many picks are independently validated.
8. **Validation:** Development period / genuinely withheld holdout period,
   walk-forward analysis, independent paper portfolio, and adverse-condition
   sensitivity. Avoid optimizing the rule thresholds on holdout results.
9. **Execution:** Verify that every proposed instrument can be held in the
   intended UK SIPP, that quotes are fresh and that the human review has
   completed. No automation submits pension orders.
10. **Go/no-go:** Review a dated report, explicit pass/fail criteria, unresolved
    limitations, and written provider evidence before proposing any transfer.

## Kraken Funded: retain previously agreed stock-first work

The currently programmed starter challenge has a fixed $1,000 initial balance,
$1,120 target, and $970 fail level (3%); **the official source is authoritative
if rules change**. No requirement to trade before a date.

The funded scanner currently relies on manually entered account equity and
other open-risk exposure. **It is not linked to Kraken's real-time funded
balance.** A READY label must not be treated as a broker-verified executable
order. Price gaps, spreads and manual exits can exceed modeled stop losses.

Keep the earlier prospective stock-only paper journal and historical
robustness investigation. Test newly observed READY/entry/rejection cases in
the prospective journal without altering live rules. In particular:

- Verify completed candles, no same-bar future information, and valid
  order-side bid/ask/spread checks.
- Verify confirmed earnings-event data, entry/stop/target ordering,
  minimum risk-reward, gap-down and slippage behavior.
- Verify an account balance source from the *current challenge*, including
  existing positions, open risk and a conservative reserve above $970.
- Report entry funnel counts and false positives for both qualifying
  and rejected examples across market regimes.
- Never reclassify an untested breakout/retest research stop as approved.
- Do not modify Kraken Funded Crypto rules until stocks validation is complete.

## What is implemented on the audit branch

- Dedicated Managed Pension research route and visible home card.
- Standalone fail-closed screening and allocation ceiling functions, with
  regression tests.
- Separate regression tests for the current Kraken Funded account-risk contract.
- PR workflow to run tests and syntax checks before merge.
- Point-in-time financial and historical investable-universe **data contracts**
  with synthetic regression tests preventing future information leakage.
- Next-session portfolio execution **mechanics replay prototype**, enforcing
  portfolio allocations, cash dividends, and modeled GBP friction. Only
  synthetic test histories have been checked; NO live fund performance claims.
- External-cashflow-neutral performance and same-period GBP total-return
  accounting, with explicit rejection of missing fees and unmatched providers.
- Defensive Kraken Funded guards for impossible proposed risk, negative
  verified spread, and future-dated stock scans. The current stock entry
  strategy and crypto trade strategy remain otherwise unchanged.
- Source feasibility review in PENSION_PIT_DATA_SOURCING.md.

**Remaining work:** Full investment page migration, a REAL acquired/verified
historical dataset including dead/delisted securities and usable publication
timestamps, replaying the unchanged scoring engine on those past snapshots,
independent holdout and live forward investment validation, risk-matched fund
returns, current pension fund transfer evidence, and prospective funded stock
stop/entry validation. None are falsely labelled as complete or profitable.
