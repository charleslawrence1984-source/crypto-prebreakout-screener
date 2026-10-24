# Kraken Funded: research validation and backtest protocol (DRAFT)

This is a research design, not evidence of profitability or permission to trade.

## 1. Reliability and observation provenance
- Audit every intended crypto 4-hour slot and each weekday 22:45 UTC stock slot.
- Record actual GitHub trigger time, job start/end, asset-specific job outcome, capture timestamp, last completed OHLCV bar, artifact ID, SHA-256 verification, expected universe and available histories.
- A late run is NOT a historical snapshot at the missed scheduled timestamp. Never interpolate or fabricate missed observations.
- Distinguish scheduler delay, skipped event, job failure, provider failure, partial history and artifact failure. Require tests and historical replay before deploying a watchdog.
- Never let a watchdog launch replacement runs until separate authorization and deduplication/cooldown testing.

## 2. Point-in-time historical replay
- Preserve current strategy code/config as the frozen baseline. Record source commit, parameters, symbol universe, provider, fee/slippage assumptions, and data vintage.
- Replay completed candles only. At timestamp T compute signals from bars and information published by T; enter no earlier than the next executable bar with realistic spread, commissions and slippage.
- For stocks use survivorship-aware historical membership, splits/dividends, delistings, earnings publication timestamps, regular-market sessions and gaps. For crypto account for listing age, exchange pair availability and 24/7 trading.
- Do not use today's 33/30 survivors as proof of historical universe coverage. Flag unavoidable survivorship bias and unavailable histories.
- Record every gate's actual input and PASS/FAIL/UNKNOWN, reason, decision, entry, stop, target, R:R, holding period and exit rule. Crypto engine 'PASS' is a rejected technical state, NOT funded-ready.
- Public spot and Yahoo prices are proxies. Do not claim funded-broker executability, bid/ask, borrow, spreads or account eligibility.

## 3. Missed-mover study (separate from backtest)
- Define movers prospectively, e.g. crypto +10%, +20%, +30% and stocks +5%, +10%, +20% within predefined 5/10/20-bar windows. These are analysis thresholds, not revised trading rules.
- For each mover inspect only the pre-move snapshot; classify as never eligible, unavailable, detected WATCH, detected rejected, late detection, false negative, or valid signal missed by collection gap.
- Record which rule blocked it; compare the full set of similarly blocked NON-movers to avoid hindsight selection.
- Report precision, recall, false positives, opportunity cost and downside outcomes by rule. Do not optimize exclusively for catching winners.

## 4. Walk-forward evaluation
- Partition chronologically into development, validation and untouched final holdout. Purge overlapping outcome windows and apply embargo around folds.
- Compare frozen baseline against one controlled rule change at a time. Include parameter stability, trade count, expectancy, median outcome, profit factor, max drawdown, worst losing streak and performance by asset/time period.
- Bootstrap uncertainty with dependence-aware sampling; avoid conclusions from tiny samples. Evaluate fees, slippage, gaps and adverse fill assumptions.
- Forward frozen observations are a separate prospective validation stream, not interchangeable with historical replay.

## 5. Funded challenge simulation
- Confirm the provider's current exact profit target, maximum overall drawdown, daily loss accounting, resets, position/exposure constraints and permitted instruments BEFORE claiming rule compliance.
- Test intraday mark-to-market equity and realized P&L, correlated exposures, simultaneous stops, gaps and account-level limits, not only per-trade R:R.
- Run conservative scenario sensitivity and report pass/fail rate across distinct periods, not a guaranteed funded-challenge success rate.

## Acceptance gates
1. Unit tests execute and pass; replay known historical GitHub runs including midnight-crossing stock jobs and delayed crypto runs.
2. Frozen artifacts verified by content and SHA-256; expected 33 coins and 30 stock tickers accounted for; two known short-history exceptions explicitly reported.
3. No data leakage, invented missed observations or broker-confirmed trade claims.
4. Baseline and experimental strategies reproducible from commit, configuration, data provenance and cost assumptions.
5. No production merge, automatic workflow dispatch or live orders without separate review and approval.

## Current state
This protocol is committed only to draft PR #34. Historical performance metrics have NOT been calculated or validated.
