# Stocks Trade — initial historical validation protocol (23 October 2026)

**Owner decision:** CL Signal Stocks = Trade only (potentially up to 12 months); Pension = long-term 10-years-to-forever individual-share Investment strategy. Existing app mixes both and must not be represented as already separated. Related #54 and #55.

## Original rules and live authority

The current Trade technical engine is trade_rules.evaluate_price_setup, used by stock_app.approved_trade_market_scan and scheduled Trade caches. Confirmed-crossover setup rules include 180/200-day MA trend, RSI pullback/recovery, MACD crossover, support/target, modeled upside >=10%, R:R >=2, gap <=0.5 ATR and structural stop distance <=10%. The full scan also requires median traded value >= GBP500k, issuer-quality hard gates and verified event/news checks. A daily close with AWAITING NEXT OPEN status is WATCH, **not** an executable BUY.

Existing stock_backtest.py is an older trade-score/zone target-hit experiment, not a full replay of the current approved engine. Direct ticker two_strategy.trade_decision uses separate score, hold-quality and valuation gates: reconciliation is mandatory before live release.

## Read-only first pilot

The new trade_historical_research.py replays only the original completed-close confirmed MACD-crossover route: all indicators are derived using price history ending at the historical close, and candidate entry uses the following recorded session open. It checks opening gap, stop, MA-zone distance, remaining upside and R:R against the original engine. It applies conservative low-before-target same-bar precedence, actual adverse-gap openings, 50 bps round-trip assumed costs, cooldown and up to 252 trading-session exits. It censors incomplete full-horizon outcomes rather than presenting them as observed profitable trades.

**Not covered**: historical time-stamped fundamentals and company filings, inactive/delisted constituents, historical calendar/news verification, corporate action and venue-specific execution validation, historical GBP FX liquidity, pre-cross-ready path, overlapping positions/equity curve, actual broker fills or protective stops. Historical Yahoo adjusted OHLCV is an imperfect research proxy. The pilot universe is AAPL, MSFT, NVDA, selected among companies currently listed; results carry **survivorship bias**. This is **TECHNICAL ONLY**, no approved historical Trade BUY, performance guarantee or live order.

The companion manifest auditor flags outdated builds, old file timestamps and incomplete saved coverage. Manifest timestamp alone is **not** proof the last individual price candle is from the completed exchange session.

## Observed cache-manifest blockers from main (23 October)

- Nasdaq Trade technicals: 241 eligible / 241 covered; NYSE 279 / 279. Coverage of eligible rows is not coverage of the full market or historical universe.
- Gettex 338 eligible-technical rows covered out of 458; technical and fundamental manifests still from 30 September with older rulebook build.
- OTC caches also remain on the older 30 September build.
- Canadian TSX fundamental manifest 524/601; UK LSE 456/472. Individual OHLC freshness and release timestamps still unverified.

## Requirements before any usable-live claim

1. Freeze full Trade decision and UI action gate contract and test correct WATCH/READY/BLOCKED, completed daily session freshness, news/earnings verification, orderability and secondary listings.
2. Obtain point-in-time issuer, corporate action, delisting, historical constituency, FX and liquidity histories; never retrospectively use the current 23 October fundamentals cache.
3. Reconcile direct ticker trade_decision versus approved Trade engine and stale MACD display; no conflicting BUY labels.
4. Compare all historical opportunities **and non-movers**, classify missed winners and rejected candidates, technical gates, pre-cross and confirmed states, matched controls and outcome censorship.
5. Run walk-forward chronological training/validation/untouched holdout with overlap purges, trading costs/slippage sensitivity, gap-through stops, portfolio exposure/drawdown, expectancy in R and net %, trade frequency, confidence intervals and multi-regime/venue coverage.
6. Independently inspect CI artifacts and real market sources, then paper-forward-test sufficiently; require owner approval prior to any deployment or actual trading.

No order placement, live strategy change, scheduled production change or merge is authorized by this pilot.
