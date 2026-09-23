# Meme Coin Launch Research Specification v0.1

## Purpose

Measure which early-launch signals actually predict tradable meme-coin upside before any feature becomes a hard screener rule.

This research lane is deliberately separate from the existing established-meme screener. It is a measurement system, not a BUY engine.

## Initial cohort

Start with Solana new-launch pools, with Pump.fun / PumpSwap / Raydium-style launches identified separately when venue metadata is available.

A launch is eligible for the primary cohort when:

- the pool/token is first observed no later than 5 minutes after pool creation;
- a usable creation timestamp and price are available;
- the launch is not a stablecoin, wrapped major, obvious duplicate pool of an already-observed token, or non-meme utility asset when that can be determined reliably;
- the observation is stored whether the token succeeds or fails.

Do not select the research sample only from trending, boosted, graduated, surviving, or high-volume coins. Those surfaces can be tracked as additional features, but using them as the cohort would create survivor/attention bias.

Store a separate `discovery_source` field so we can quantify source bias.

## Observation clock

For every eligible launch, record cumulative point-in-time snapshots at:

- 30 seconds
- 60 seconds
- 120 seconds
- 180 seconds
- 300 seconds

The system may poll every 30 seconds, but raw trades should be deduplicated and retained where possible. One-minute OHLCV is useful context; raw trade timestamps are preferred for order-flow research.

Later outcome checkpoints:

- 15 minutes
- 30 minutes
- 1 hour
- 3 hours
- 6 hours
- 24 hours

The 30-second interval is a hypothesis to test, not an assumed optimum.

## Entry anchors

Maintain two distinct entry references:

1. `first_seen_price`: price when the research collector first detects the launch.
2. `checkpoint_price`: price at each 30s/1m/2m/3m/5m decision checkpoint.

All forward outcomes must be calculated from the price that was genuinely available at that checkpoint. Never label a 5-minute feature vector using a launch price that was available five minutes earlier.

## Tier-1 feature family: quality-adjusted participation / order flow

At every checkpoint record, where data supports it:

### Flow

- gross buy USD
- gross sell USD
- net buy USD
- net buy / total volume
- net buy / liquidity
- buy-volume share
- buy transaction count
- sell transaction count
- buy/sell transaction ratio
- median trade size
- 90th-percentile trade size
- trade-size dispersion

### Participant breadth

- unique buyers
- unique sellers
- unique traders
- buyer/seller ratio
- new unique buyers in the latest interval
- unique-buyer acceleration
- repeat-buyer share
- wallets with both buys and sells
- rapid round-trip wallet ratio

### Participation acceleration

Compare recent versus earlier intervals rather than only absolute totals:

- latest-30s volume / prior-30s volume
- latest-60s volume / prior-60s volume
- latest-interval net flow acceleration
- unique-buyer acceleration
- transaction-rate acceleration

Raw headline volume is not a standalone quality signal.

## Price / structure features

- return since first seen
- return since prior checkpoint
- high/low range
- realised volatility
- maximum favourable excursion so far
- maximum adverse excursion so far
- drawdown from launch high
- close/last-price position within observed range
- higher-low / lower-low progression when enough observations exist
- price acceleration
- price response per dollar of net inflow
- price response per dollar of gross volume

A strong flow signal with little or negative price response should be explicitly measurable as potential absorption/distribution rather than automatically bullish.

## Liquidity / tradability features

- pool liquidity USD
- liquidity change since first seen
- liquidity / estimated market cap
- cumulative volume / liquidity
- net buy flow / liquidity
- estimated slippage for fixed notional sizes where supported
- pool age
- number of active pools for the token
- dominant-pool share

## Manipulation / quality-control features

Where wallet/trade data permits:

- top-1, top-5 and top-10 trader volume share
- wallet-volume Herfindahl index
- wallet-volume Gini coefficient
- rapid same-wallet buy/sell round trips
- repeated identical-size trade ratio
- circular/zero-risk flow proxy
- unusually high volume with little price movement
- bundle/sniper indicators when a chain-specific source supports them
- creator/deployer holdings and top-holder concentration when a chain-specific source supports them
- common-funder clusters when a chain-specific source supports them

Missing advanced chain features must remain `UNKNOWN`; never assume they pass.

## Context features

These are explanatory/secondary variables and cannot rescue poor order flow:

- X present
- Telegram present
- Discord present
- website present
- social breadth count
- DexScreener profile/boost/community-takeover presence
- narrative category
- discovery source
- SOL/BTC market regime
- launch-hour / day-of-week
- current broad meme-market activity

## Forward outcome labels

For every checkpoint entry price, calculate at each outcome horizon:

- final return %
- maximum favourable excursion %
- maximum adverse excursion %
- maximum drawdown from post-entry high
- time to 2x
- time to 3x
- time to 5x
- hit 2x / 3x / 5x
- hit 2x before -50%
- hit 3x before -50%
- hit 5x before -50%
- adverse excursion before each successful target
- liquidity change %
- minimum liquidity %
- trading activity remaining at horizon

### Failure / rug labels

Do not collapse these immediately into one arbitrary rug definition. Store separate labels:

- price drawdown <= -50%
- price drawdown <= -70%
- price drawdown <= -90%
- liquidity collapse >= 50%
- liquidity collapse >= 80%
- effectively inactive / no meaningful trading
- honeypot / transfer restriction where externally verified
- known rug/manual incident label where verifiable

A later composite fraud/rug label may be trained only after the individual failure modes have been measured.

## Primary research questions

1. Does net buy flow outperform gross volume?
2. Does unique-buyer growth outperform transaction count?
3. Does buyer acceleration add information beyond cumulative buyers?
4. Does net flow normalised by liquidity outperform raw net flow?
5. Does price response to flow separate organic demand from churn/manipulation?
6. Do wallet-concentration and round-trip measures materially improve failure/rug detection?
7. Which checkpoint (30s, 1m, 2m, 3m, 5m) gives the best trade-off between early entry and signal reliability?
8. Are the same signals stable across launch venues and market regimes?

## Evaluation protocol

### No look-ahead

Every feature row contains only data timestamped at or before its checkpoint.

### Time-based validation

Use chronological train/validation/test splits. Do not rely on a random shuffle because adjacent launches share market regime and platform conditions.

### Baselines

Compare at least:

- price-only
- raw-volume-only
- order-flow/participation-only
- liquidity-only
- manipulation-quality-only
- context-only
- combined model

The combined model must beat simple baselines out of sample before complexity is justified.

### Univariate research

For every feature:

- quantile-bin hit rates
- median winner vs failure values
- rank correlation with forward MFE
- relationship with MAE / failure labels
- sample count and missing-data rate

### Multivariate research

Use interpretable tree-based models first, then evaluate:

- out-of-sample ROC-AUC / PR-AUC for failure labels
- Brier/calibration for probabilities
- precision/recall for 2x-before-50% labels
- feature permutation importance / SHAP when available
- feature-family ablation

### Trading relevance

A feature is not promoted because it predicts eventual upside alone. It should improve one or more of:

- probability of 2x/3x/5x before severe drawdown
- median MFE
- lower MAE before target
- earlier reliable identification
- lower rug/failure rate
- better expected value after realistic slippage/fees

## Promotion standard

Do not turn an observed threshold into a hard screener rule from one sample.

A feature can become a meaningful score input only when:

- direction is reasonably stable across multiple chronological test periods;
- result survives at least one venue/regime split;
- sample size is adequate;
- effect is not explained entirely by liquidity/market-cap cohort;
- missing data does not create obvious selection bias.

A hard gate requires materially stronger evidence than a score feature.

## Data-source notes

Current public DEX sources already provide useful pool price, liquidity, volume, transaction and buyer/seller counts. CoinGecko/GeckoTerminal also exposes on-chain trades with wallet addresses, direction, size and value; these can be aggregated into our own 30-second and 1-minute research buckets. Pool endpoints support short-window transaction fields such as 5-minute buys/sells/buyers/sellers. citeturn686187search7turn686187search2

Published research supports prioritising order flow as a predictive crypto variable and treating artificial volume as a major contamination risk. citeturn933896search0turn933896search4

Recent Solana research also shows that the first five minutes can contain substantial rug-pull information, which is why the initial research window is deliberately concentrated there. citeturn933896academia25

## First implementation milestone

Build the data model and offline label/feature functions first.

Do **not** alter the live Meme Coin BUY/SHORTLIST rules until we have collected and analysed a meaningful launch cohort.
