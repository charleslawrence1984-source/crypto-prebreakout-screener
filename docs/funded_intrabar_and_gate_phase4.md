# Kraken Funded research phase 4 — intrabar 3% floor and individual indicator diagnostics

**Status: separate draft research PR. Zero live rule changes, scheduled scans, orders, merges or broker connection.** This study consumes only archived read-only artifacts from earlier draft PRs #48, #49 and #50; it never merges those PRs.

## Historical baseline

33 current approved spot coins, 4,514 originally scored Kraken 4h/daily/weekly/BTC-relative point-in-time signal decisions, 11 technical BUY, 2,094 WATCH and 2,409 rejected PASS. Previously studied stop-first positions include 2 hypothetical targets / 9 stop-outs at 0.8% modeled total trading friction, average -0.45R. A broad WATCH bypass was also negative.

The original Funded account-level scenario inspected *closed* equity, not account drawdown during the trade. This can give false reassurance. [Official Kraken APP Funded documentation](https://support.kraken.com/articles/funded) confirms +12% target, fixed -3% original capital floor, no added leverage and no advanced protective stop orders. **Not Kraken Pro Prop.**

## Scope A — additional public-candle intrabar risk bound

- Original at-time technical BUY or eligible, previously documented WATCH counterfactual ONLY, never manufacture a new original BUY.
- SHA-256 checked original public Kraken USD spot 4h OHLCVT CSVs, from immutable research archive.
- Historical next-open price, original risk invalidation, observed original stop-first holding length and previously modeled R. Sensitivity risk allocations 0.25/0.5/1% of INITIAL balance; original paper roundtrip friction scenarios 0.4/0.8/1.6%.
- No leverage: cash-limited position sizing strictly caps quantity to account balance / cost-adjusted entry.
- Calculate minimum estimated account marked equity using actual public 4h candle low within the original modeled trade's lifetime, assuming NO auto-executed stop. Identify possible 3% static starting-capital floor touches that a closed-P&L analysis misses.
- This is NOT a genuine Kraken Funded bid quote/account valuation; intrabar chronology, actual availability, manual stop fills and broker simulation still unverified. A 4h low that touches the floor is only a *possible price-exposure stress*, not confirmed Kraken Funded liquidation.
- Missing candles, changed original source next open, unsupported costs or invalid stop structure must fail closed. Do not claim probability of passing challenge.

## Scope B — individual *observed feature* stratification

The source-verified original numeric technical trace [PR #50](../pull/50) has 4,514 observations with 4h MACD histogram slope, positive/negative status, resistance tests, RSI, original timing and score. Join exact original as-of coin/timestamp to archived original 0.4/0.8/1.6% stop-aware risk outcomes from [PR #49](../pull/49).

Compare original as-of single feature PASS versus FAIL while preserving other technical engine gates and whole observed decision class. Include nonwinning controls, sample denominator, per-cohort and chronological fold. Original rejected PASS or ineligible WATCH cases do not become BUY.

**This is a conditional/observational selection sensitivity study, NOT causal single-indicator gate removal**. Original holdout was already examined and is NOT pristine out-of-sample evidence for tuning. Genuine prospective never-before-analyzed dates, broker asset eligibility and verified Funded quote/spread/stop behavior are necessary before suggesting rule changes.

## Acceptance gates

- [ ] Deterministic tests validate no-leverage quantity, fixed floor, worst public 4h low risk, future-gap censoring, source next-open parity, feature source class fidelity
- [ ] Original frozen source artifacts fetched in PR-only GitHub CI; whole source manifest SHA validated for each coin before simulated 4h lows read
- [ ] All historical results archived and audited with source/quote caveats, denominators and fail-closed gaps
- [ ] No merges, automatic monitoring changes, signal rule tuning, broker actions or live orders


## Verified results from 22 October 2026 (PR-only)

**[Current-head research GitHub Actions run 39762237753](../../actions/runs/39762237753): BOTH jobs SUCCESS**, including synthetic cash-only/static-floor and immutable source replay. Independently downloaded research ZIP **artifact 12419041026**, verified ZIP integrity and five output objects. **4,514/4,514 original numeric trigger decisions remain intact; 1,341 joined original stopped/targeted hypothetical entry-cost observations** (= 447 at each of three cost scenarios); 150 conditional-gate/fold/cost summary strata, 18 account-cash-risk scenarios.

**Original BUY at 0.5% of initial $1k as nominal risk, 0.8% modeled roundtrip costs:** all **11** source research entries appear in serial order, account closed-equity proxy **$975.443325**, modeled public 4h unprotected price lows produced **zero hypothetical static $970 crossing** in the modeled holding intervals. No +12% goal, and NO verified continuous Funded quote history.

**Broad WATCH_READY_BYPASS at the same 0.5% and 0.8% costs:** **six** hypothetical serial entries before reaching modeled closed-equity **$964.053605**, below static $970, with **two possible 4h public-low crossing observations**. Ten overlapping alternative WATCH candidate start times were skipped while a previous hypothetical position remained open. Across all three WATCH paper cost levels x three assumed risk allocations, the serial closed-equity model crossed $970; **not evidence that any real Funded account breached**.

**Risk sensitivity / unlevered cap:** Original BUY at 0.25% initial risk / 0.4–1.6% costs remained above $970 in this small sample, while original BUY at 1% / any tested costs fell below $970 in the *paper* account model. The current model caps notional position size at 100% of pre-trade cash. These are static historical examples, NOT recommended account position sizing or funded challenge pass estimates.

**Single-feature gate evidence (observation only):** Among the original eligible WATCH hypothetical cohort at 0.8% modeled cost:
- MACD histogram positive in original TRAIN: **172 cases mean +0.02934R**, vs histogram nonpositive: **83 cases mean −0.74865R**.
- VALIDATION: positive **67 cases −0.48515R**, nonpositive **32 cases −0.63314R**.
- Previously examined HOLDOUT: positive **56 cases −0.57455R**, nonpositive **26 cases +0.12927R**.
The apparent favorable TRAIN association **reverses in historical holdout**, demonstrating precisely why cherry-picking an indicator from retrospective winners is unsafe. Historic fold has been consulted and is not a fresh test-set for changing live rules. **Do not increase trading frequency or change MACD/resistance rules** on these comparisons.

### Still not demonstrated

- Real Kraken Funded app quote vintage, asset list available for this user, quote spreads, in-app execution latency or stop fills
- Timestamped intra-candle execution sequence or current Funded account equity trajectory, even though public four-hour lows are checked
- Statistical independence across correlated altcoin moves, reproducible genuine out-of-sample returns, equity-level probability of passing the challenge
- A **causal** per-indicator historical ablation (only original as-of observed feature / original risky entry controls tested)
- Repair of overnight US Yahoo stock data publication latency or actual prospective closed paper positions

**Decision: Keep live rules unchanged.** Continue evidence gathering from NEW prospectively archived 4h candles and official app instrument/quote availability before any trigger proposal.
