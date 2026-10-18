# Kraken Funded — challenge-risk safety audit (18 October 2026)

**Status:** DRAFT PR #32 ONLY — not merged, NOT cleared for real-account trading.

This audit reconciles the paper risk overlay with the actual Kraken Funded
UI model, the frozen research source and Kraken's published product mechanics.
It is not a promise that a manual trader can protect the funded loss floor.

## Reference rules and strict scope

- Current source: `kraken_funded.py` (original, unchanged entry rules):
  $1,000 starting reference equity, $1,120 target, **fixed $970 loss floor**,
  $3 ordinary paper risk, $4 standard/A+ budget, maximum $5 per position,
  maximum **$10 combined planned open risk**, maximum **35% of current
  manually entered equity** in one position, and a **$10 safety buffer**
  above the fixed $970 floor.
- Published product guide: https://support.kraken.com/au/articles/funded
  (updated 22 September 2026). Kraken Funded consumer mobile app has
  **no advanced order types**, no protective automated stop-loss,
  no added leverage, and some 24/7-priced funded stock instruments that
  are not directly equivalent to Yahoo regular-session stock OHLCV.
  Kraken states hitting the fixed loss floor even briefly ends the challenge.
- Public pricing is NOT account pricing. Kraken describes a baseline 0.04%
  spread each side, but spread can be materially wider by asset. This paper
  overlay conservatively assumes 0.50% roundtrip costs for sensitivity.
  It cannot validate actual quotes, slippage or manual fill timing.
- All numbers are specifically for the **$1K** Funded challenge in the
  current CL Signal overlay. Never substitute Kraken Prop / Breakout rules.

## Safety issues audited

1. **Previously missing equity-buffer guard (fixed in this draft).** The
   original UI `_account_guard` checked new position risk against remaining
   equity and existing risk against the separate $10 aggregate cap, but did
   not subtract **existing planned open risk** from equity headroom above
   $970 + $10. For example, $985 paper/manual equity, $3 existing
   planned open risk and another proposed $3 risk would formerly pass:
   total $6 fits the separate $10 cap, but exceeds the **$5** equity
   headroom after reserving the $10 buffer. The draft UI and paper model
   now block that case. The **displayed remaining-risk allowance** uses
   the same combined calculation. This is a *safety tightening only*,
   not a new BUY signal or modified technical rule.
2. **Paper sizing consistency (fixed in this draft).** The paper model
   now uses the live UI's $3/$4/$5, $10, 35%, $1K/$1,120/$970
   constants verified by a source-code AST regression. A request for a
   full $3 budget cannot silently shrink to an ineffective smaller
   amount near the floor and still be represented as permitted.
   Both current equity headroom and planned aggregate open risk must
   permit the full requested budget *before* sizing.
3. **No automatically executable protective stop (stress sensitivity).**
   The draft models hypothetical simultaneous adverse 3%, 5%, 10%, 15%
   moves plus explicit model costs, without assuming stops execute.
   A $300 long with 10% adverse move and 0.5% roundtrip model cost
   marks a $1,000 proxy balance to **$968.50**, breaching the $970
   floor despite a much smaller planned technical stop. Synthetic,
   not an observed Kraken loss or forecast.
4. **Overlapping positions and adverse closes (fixed in this draft).**
   Repeated observations of the same still-open asset are not separate
   hypothetical trades; total planned risk and cash/notional exposure
   are capped. Crucially, if one of several overlapping cases closes
   at a hypothetical loss, the **remaining open positions** are
   stress-tested against the reduced realized-equity proxy, even if
   there is no new entry afterward. Previously only admission
   instants were stress-tested.
5. **Booked loss can exhaust the reserve on an EXISTING position (fixed in this
   draft).** The risk admission check only runs when a *new* hypothetical
   position is considered. A prior trade can subsequently close at an
   unfavorable price while another trade stays open, so the combined
   remaining planned risk can become too high for the now-lower modeled
   equity even if no new trade is requested. The draft now reports an
   explicit `remaining_position_buffer_breach_after_booked_loss` alert
   with dated evidence and the remaining assets, rather than accidentally
   implying the original entry-time approval remains sufficient.
   In the synthetic scenario the realized-only proxy stays above $970;
   the $10 safety reserve can nevertheless be consumed. This is
   **research evidence, not a claim of an observed broker breach**.
6. **Source/trade authorization integrity (tested).** Every admitted
   hypothetical position must reference an authentic full-universe,
   SHA-256-verified original Kraken Funded archive and a separate
   read-only outcome. Rejected Crypto `PASS`, WATCH, or a forged
   profitable outcome cannot become a recognized paper BUY.
   Incomplete histories and unverifiable provider data are not losses
   or winners, and no broker transaction is authorized.

## Remaining hard blockers — why this is NOT a live-trading approval

- The standard Funded app **cannot install the technical stop as a native
  protective order**. Even perfect OHLCV stop simulations cannot prove
  the real funded account will avoid touching its fixed intrabar loss
  floor during volatility, illiquidity or a period when the trader
  cannot watch and sell. No trading-risk clearance is possible here.
- The forward observations use Kraken public spot OHLCV for Crypto and
  Yahoo regular US-session shares for Stocks, NOT actual Funded
  instrument buy/sell quotes, 24/7 stocks, snapshots of account equity,
  earnings confirmations, fills, or continuous mark-to-market P&L.
- The stress bands are **what-if sensitivities**, not observed continuous
  adverse prices, correlated-loss forecasts, or actual floating drawdown.
  The `kraken_funded_challenge_pass_confirmed` field must always
  stay `false` without genuine account integration and verified rules.
- Several initial genuine archived Crypto captures found **zero**
  original BUY signals. Accordingly no win rate, positive strategy
  expectation, drawdown distribution, or challenge-success claim is
  empirically supported. A $1,000 paper balance with no positions is
  **untested**, not proof of profitability or safety.
- The staged `funded_challenge_shadow_risk.py` and the UI guard
  change remain in DRAFT #32 pending **specific owner merge approval**.
  No additional trade/scoring rule modifications or cross-module
  functionality are requested.

## Required release evidence

- Funded guardrail regression (including UI syntax/stock breakout checks)
  and challenge-risk regression both PASS on the **latest PR head**.
- Full original public-provider forward capture test and original
  production archive outcome journal PASS. The latter should report
  actual qualifying signals separately from synthetic stress tests.
- Review any UI comparison to the current merged `main` version
  (PR #33 manual-stop disclosure must not be removed by release),
  verify funded user input remains manually confirmed every day,
  and do not merge without explicit owner approval.
