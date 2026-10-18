# CL Signal — staged release checklist (17 October 2026)

All modules must remain independently identifiable. A passing CI check confirms
software behavior on tested fixtures; it is NOT proof of profitable signals.

## Stage A — activate evidence collection BEFORE changing strategies

| Module | Verified | Remaining blocker | Release permission |
|---|---|---|---|
| Kraken Funded stock forward captures | 30 tickers from a real unattended Yahoo run on 17 Oct; 28 usable, SPCX/CBRS insufficient bars | Isolated read-only PR #21 must pass own provider integration / regression tests and be merged, then scheduled-run health verified | Research collection only |
| Kraken Funded crypto forward captures | 33 coins scanned, all public Kraken spot histories usable in the first run | Same PR #21, 2-3 live scheduled rounds, 24/7 coverage and failure reporting | Research collection only |
| Stocks Trading | Trade technical manifest updated 17 Oct; most technical markets complete | Trade fundamentals incomplete in LSE/NYSE/TSX and technical Gettex remains partial; rerun live UI parity / refresh checks | Preserve trade engine, do not alter rule weights |
| Crypto (separate from Funded) | Active `crypto-data` manifest on 17 Oct reports CURRENT, 169 eligible coins and 129 active candidates, no errors at observed snapshot | Run health trend checks, stale feed handling, duplicates and signal attribution, venue execution verification | Existing scanner only |
| Meme trading | Separate existing market pipeline present | Time-aware fresh listing scan, slippage/liquidity and price-manipulation resistant checks remain to audit | Do not deploy automated orders |
| Managed Pension | Research UI and synthetic historical-point-in-time, replay and return-accounting tests pass | Real historical fundamentals as filed, delisted price data, tax/FX/dividends, unchanged Investment model replay, holdout analysis | RESEARCH ONLY |
| Pension consolidation | Three old DC pensions identified, active Aviva / British Army deferred excluded | Provider contractual replies, comparable net-of-member-fee fund returns / risk, and transfer rights | NO TRANSFERS |

## Stage B — funded evidence-to-trade bridge

1. Store each original observation at time T with immutable capture ID.
2. Score later price movements with a separate outcome evaluator, without
   conditioning entry on already-seen later candles or assuming execution.
3. Show no-setup, data error, watch, research candidate and rejected/expired
   observations separately. Never infer 'strategy has no opportunities' from
   a stale or missing scan.
4. Preserve predeclared entry/stop/target/risk before the outcome window.
   Capture quote spread and friction, gap losses, and censored incomplete exits.
5. Compare genuinely prospective stock and crypto observations separately.
   Coin-liquidity, market access and quotes must be checked against Kraken
   Funded itself before ANY actual position.
6. Gate manual entry on verified account equity/open exposure and official
   stock earnings/event calendar, and require a human stop-loss plan because
   funded stock exits may be manual.
7. A trading decision requires a dated reviewed readiness report, explicit
   failures/uncertainty, and fresh broker price confirmation.

## Stage C — platform navigation and UI parity

- Stock **Trading** is distinct from Investment.
- **Managed Pension** owns Investment ten-years-to-forever strategy, with no
  buy/transfer buttons that bypass the historical validation gate.
- **Crypto** research and the specialized **Kraken Funded** overlays never
  overwrite each other's risk rules; **Memes** remain segregated too.
- Record baseline screenshots / functional behavior before migration.
  Compare stock counts, saved portfolios, watchlists and order-less navigation
  before/after. Keep old Investments screens until parity passes.

## Stage D — provider comparison

Compute each fund's same dated net-of-all-member-fees GBP total return and
drawdown on equivalent contribution-neutral basis. Include asset mix, lifestyle
path, safeguarded benefits, guaranteed rates or special protections. An Aviva
high-equity fund outperforming a lower-equity Standard Life managed fund is not
in itself evidence Aviva is a superior pension provider or of suitability.
Get quotes before considering a new SIPP, and exclude the active employer
and military pensions unless user explicitly reopens their scope.

## Release methodology

Record each gate as PASS / FAIL / AWAITING EVIDENCE with GitHub CI run and
source timestamp. Never call '100% accurate' or 'perfect' due to uncertain
future markets, provider outages and manual execution realities.

Separate deployment of the read-only funded collector in PR #21 from the
unfinished full-screen Investment/multi-module PR #20. **Do not merge PR #20
as a pension-ready release.**
