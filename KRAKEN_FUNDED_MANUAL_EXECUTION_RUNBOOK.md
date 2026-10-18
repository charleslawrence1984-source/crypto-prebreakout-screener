# Kraken Funded — manual execution & emergency-exit procedure (research gate)

**18 October 2026 — DRAFT PR #32; NOT live-account execution authority.**

Official current product facts (confirm the Funded tab itself for account-specific details):
- https://support.kraken.com/au/articles/funded (last updated 22 Sep 2026)
- https://www.kraken.com/features/funded

This is **Kraken Funded in the standard Kraken app**, NOT Kraken Pro Prop.
The app supports basic buy/sell in USD, **NO advanced order types** and no
native protective stop-loss/stop-entry, no borrowed leverage. The $1,000
challenge reaches its target at **$1,120** and fails if the account's
valued balance touches **$970, even briefly**. The floor is always fixed
to the $1,000 starting balance, not a trailing high-water mark.
There is **no time limit**, no minimum trade count and no separate daily
loss limit. User risk in the challenge is the non-refundable fee, but
the challenge can fail and need repurchase. Some Funded stocks trade
24/7 at Funded quote prices, NOT Yahoo's normal-session fills.

**Decision right now: NO LIVE TRADE CLEARANCE.** Continue shadow-only,
until a non-zero, prospective, original strategy BUY sample is assessed,
the exact funded holdings and quote mechanics are checked in the
user's app, and manual execution practicality is accepted.
This procedure is a checklist and stop-trading policy, *not* a way to
guarantee a $970 floor that can be breached between app refreshes.

## Gate A — before even considering a real funded entry

1. **Confirm product and instrument.** In Kraken's dedicated Funded tab,
   verify Starter $1,000 (or recalculate ALL risk references for an
   alternate tier), **actual asset available**, Kraken Funded buy price
   AND immediate sell price, no unavailable-market warning, current
   marked account equity and existing open positions. A Kraken SPOT
   pair or Trading 212/Yahoo stock price is **not** that Funded quote.
   If any value is missing/stale, **PASS / NO TRADE**.
2. **Confirm original signal.** The correct asset must have a genuine
   frozen **original Crypto BUY** or original permitted Stock entry
   technical state; Crypto PASS means rejected. Meets existing scoring,
   prebreakout, valid structural invalidation, at least 2.0R, shape,
   public volume, quality and no-chasing tests. Do not buy on WATCH,
   confidence, a good paper backtest, or a recent price run.
3. **Manually verify business event and risk** for stocks; a Yahoo
   earnings estimate is NOT an official announcement. Verify any live
   quote spread or 24/7 market deviation against the actual Funded tab.
4. **Re-verify equity/risk on the same day and after every trade.**
   With manual current equity `E`, and **all** planned open risk `O`
   including spread/fee reserve, allowable additional planned loss is:
   `min(10 - O, E - 970 - 10 - O)`, floored at $0.
   This preserves $10 model buffer above the $970 failure floor
   *on planned-stop assumptions only*. Normal Funded READY reference
   risk is $3, A+ $4; absolute per-position max $5, total open
   planned risk <=$10, notional per asset <=35% of manually entered
   equity. The absolute per-position $5 is a ceiling, NOT an
   instruction to size every setup at $5.
   Do not use a smaller secret trade to bypass the full-budget gate.
   If planned risk fails even one check, **NO TRADE**.
5. **No automated stop**: can you watch and ACTUALLY use the app
   immediately, without meetings/driving/sleep, throughout intended
   holding time (for crypto overnight/weekends too)? If not,
   **do not open the position**. Do not treat a phone notification or
   stop price recorded in CL Signal as order execution.
6. **Stress test actual quote-based position before entry.**
   Compute hypothetical equity under simultaneous 3%, 5%, 10%, 15%
   adverse drops in ALL open assets plus wider stressed roundtrip
   spreads, with no promised stop. If any meaningful stress touches
   $970, that exposes challenge-failure risk; do not treat a green
   $3 planned stop as sufficient evidence of safety. There is no
   size that guarantees protection against an unbounded gap.
   These are scenarios, not probabilities.
7. **Funding economics:** the 12% challenge target is four times
   the 3% fixed loss allowance. There is no deadline requiring trade.
   If a setup needs close monitoring you cannot provide, it is a
   reject, **not** a reason to lower the strategy's standards.

## Gate B — hypothetical entry checklist (not an order instruction)

- Re-read actual Funded buy and sell indications *immediately* before
  any decision. Check spread, whether quoted BUY is above the
  original signal's planned maximum acceptable entry, and whether
  entry-to-target R:R remains >=2 using current cost assumptions.
- Calculate risk at the actual displayed executable BUY, and
  worst plausible sell/slippage, not merely the last candle close.
- Write down time, original source/run identifier, original
  frozen signal/invalidating price, display price, model risk,
  dollar amount, observed spread, current equity and other open
  risks. If any required field is unknown, **do not proceed**.
- In the Funded app orders are **manual buy/sell only**. Nothing
  submitted by the research scanner, GitHub Actions, email alert or
  simulated stop will enter or exit a Funded position.
- Avoid positions across **unattended intervals** since no native
  protective order can enforce the technical exit. The 4h/1d
  research hold horizon is NOT a proof that an unattended real
  funded position is safe.

## Gate C — manual monitoring / invalidation / emergency exit

1. While a position is open: continually confirm **total Funded
   marked-to-market equity including ALL floating P&L**, not just
   closed trade results. Record the displayed SELL quotes, not spot
   exchange midpoint. Recalculate all open risk after any fill,
   cancellation, adverse move or close. Do not add while quoted
   equity, fees or exit availability are unknown.
2. If the original thesis invalidates, if actual sell quotes or
   equity approach the internal reserve, or if another loss pushes
   remaining open planned risk above the available floor buffer,
   **prioritize reducing/closing exposed Funded positions manually**.
   Treat this as an urgent discretion decision; do not wait for a
   paper target or expect a fictitious stop to fire.
3. If the Funded app is unavailable, internet breaks, quotes become
   stale, or you cannot supervise: **do not open new trades**. If
   already exposed, seek to close through the normal Kraken Funded
   tab as soon as functionality returns. Unable to access the app
   means there is NO guarantee the fixed failure floor survives;
   no CL Signal action can place an emergency order.
4. If the account is close to $970: **do not add or average down**.
   A manual sell may execute too late or with a wider spread and the
   challenge may already have ended. Protect the account where
   practical rather than attempting to rescue a failing setup.
5. After close: verify actual Funded transaction history and
   account equity in the Funded tab, and reconcile all open risks.
   **Never import simulated fills as broker execution or performance.**
   If a breach / app error occurred, stop further real trades
   until the actual status is verified.

## Gate D — strategy evidence / authorisation

- Shadow forward testing currently does NOT have a closed sample of
  independently qualified real-original Funded BUY observations.
  Therefore real win rate, expectancy, confidence and
  challenge-survival probability are **UNKNOWN**.
- Original scan and outcomes must remain archived and SHA-checked.
  An observed WATCH-to-BUY, if one occurs, is a technical
  *research trigger*, not funded-app deal eligibility.
- Kraken Funded might be structurally unsuitable for a strategy
  needing guaranteed stop orders while the trader is away.
  If stop automation is mandatory, **avoid live Funded execution**
  rather than pretending paper stops solve that limitation.
- This is research-only. No automatic monitoring by the assistant,
  no placement of real orders, and no guarantee against gaps,
  outages, challenge failure or non-refundable fee loss.
