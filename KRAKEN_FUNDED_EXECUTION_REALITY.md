# Kraken Funded — execution reality and signal-funnel research

**Verified 18 October 2026 against Kraken's official Kraken Funded information.
This document is about the consumer-app **Kraken Funded** program, not Kraken
Pro Prop or Breakout. No trading authorization.**

## Product restrictions directly relevant to CL Signal

Primary official sources:

- <https://support.kraken.com/au/articles/funded>
- <https://www.kraken.com/features/funded>

Kraken's Kraken Funded support article describes a **mobile-only**, separate
program with buy/sell trades and **no advanced order types** or added leverage.
It states that select stock-price instruments are available **24/7**, that
they are not ordinary shares owned by the user, and availability varies by
region and funded account. The app lists the specific instruments allowed.

**Therefore CL Signal MUST NOT claim its frozen breakout trigger, target,
or stop can be installed as a native protective or conditional Funded app order.**
Research long entry-stop fills and stop-loss exits recorded by the public
OHLCV paper engine are mathematical scenarios, *not* executable orders.
Without a native protective order, a trader might be unavailable or late
to react and suffer a much larger loss than the frozen paper stop suggests.
Do not claim reliable challenge risk compliance from those tests.

The published nominal Funded spread is 0.04% on each side, but assets can have
much wider spreads, particularly the synthetic 24/7 stock instrument. CL Signal
uses a **hypothetical** 0.5% roundtrip paper-cost scenario: it is neither a
live buy/sell quote nor proof that pricing for a specific instrument matches
Kraken Funded. Inspect the current buy and sell indications inside the actual
Funded tab. Kraken Funded and **Kraken Pro Prop** must not be conflated:
the latter advertises different order types, margin/funding and daily risk
rules, none of which can be substituted into the Funded product model.

Kraken's current $1,000 challenge is a 12% target ($1,120) with a **fixed
3% maximum loss floor ($970)** and no separate daily limit. Kraken says
hitting the loss line even **briefly** ends the challenge. Booking closed
paper profits and stop-loss-only losses is NOT sufficient to verify that
the live account never touched the floor while other positions were open.

## Original-signal observation funnel

The read-only `funded_signal_funnel.py` stages a separate, independent,
provenance-checked diagnostic:

1. Read only prior default-branch GitHub saved Funded research artifacts,
   verify each original snapshot's canonical SHA-256, expected 30/33 asset
   universe and UTC capture time, and never rewrite original observations.
2. Count original stock/crypto states and **overlapping** quality, proximity,
   score, liquidity and projected R:R blockers. The original crypto engine
   uses `PASS` to denote *rejection*, never successful entry.
3. Follow original `WATCH` statuses across successive real recordings.
   Count `WATCH -> BUY` only when the *original* Crypto engine itself
   emits `BUY` (or an approved original stock entry state). A high score
   or a researcher-calculated threshold is not permission to promote a row.
4. Measure observed gaps between actual crypto capture times; flag gaps
   longer than 5.5 hours as potential missing coverage given the intended
   4-hour collection schedule. GitHub scheduled jobs are best-effort and
   many missed scans could conceal real opportunities.
5. Report the number and provenance of source observations rather than
   claiming a statistically sound trade sample from 2-3 days of data.

### Recorded original sample (17–18 Oct 2026)

- Three authentic live Crypto captures (33 coins each); all 99 coin
  observations were archived in a real completed run, but the first
  forward outcome journal found **zero original entry BUYs** and
  correctly opened **zero hypothetical positions**.
- In the 18 October 05:04 UTC Crypto archive, 31 original `PASS`
  rejections and 2 `WATCH`: BTC with 65.6 score and HBAR with 52.6
  score; existing original Crypto funded quality requires 80+.
- The PR stock provider smoke had 28/30 valid OHLCV histories.
  SPCX and CBRS each lacked the required 252 completed daily bars;
  AAPL/MSFT were compression WATCH and four were developing pullback
  watches. These are *not* entry approvals.
- The research funnel does not loosen RSI/MACD, trend, shape, resistance,
  timing, liquidity, position sizing, quote freshness or any BUY criteria.

## Release gates and next evidence

- Keep all new changes in a **draft PR** until regression and
  provider-backed archived-signal audit pass and owner separately approves.
- Confirm sufficient honest completed capture cadence across weekdays,
  trading sessions and evolving WATCH/BLOCKED symbols before assessing
  strategy opportunity rate or technical false negatives.
- Review exact allowed Funded instruments, funded-app pricing, available
  controls and manual execution logistics before ever acting on a label.
- Continue read-only forward tracking, but do NOT infer live-funding
  survivability or an achieved 12% target from stop-first proxy candles.
- PR #32's realized-only risk model remains draft and has not proved
  intrabar/floating max-loss compliance. No merger with any other product
  section or brokerage automation is authorised.
