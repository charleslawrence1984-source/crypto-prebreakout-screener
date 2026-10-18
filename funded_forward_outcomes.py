"""Prospective hypothetical funded outcome measurement, never broker P&L.

The original observation must have been frozen BEFORE each later completed
price bar. An outcome is NOT a trade: no proof of Funded broker quote,
fill, holdings, borrow/financing costs, execution availability or manual exit.
Only explicit prospective original technical candidates are simulated.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
import json
from datetime import datetime, timezone
from math import isfinite
from typing import Mapping, Sequence


STOCK_PAPER_STATES = frozenset({"ENTRY READY", "FUNDED BREAKOUT PENDING OPEN"})
CRYPTO_PAPER_STATES = frozenset({"BUY"})


@dataclass(frozen=True)
class CompletedBar:
    opened_at_utc: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class HypotheticalOutcome:
    asset: str
    asset_type: str
    assessment: str
    entry_price_proxy: float | None
    exit_price_proxy: float | None
    exit_reason: str
    realized_r_proxy: float | None
    completed_future_bars: int
    caveat: str = "UNEXECUTED RESEARCH — NEVER ACTUAL KRAKEN FUNDED PROFIT/LOSS"


def _stamp(raw: str) -> datetime:
    value = datetime.fromisoformat(str(raw).replace("Z","+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("snapshot timestamp requires timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _safe_positive(value: object, label: str) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not isfinite(number) or number <= 0:
        raise ValueError(f"invalid {label}")
    return number


def assess_frozen_outcome(
    snapshot: Mapping[str, object],
    row: Mapping[str, object],
    later_completed_bars: Sequence[CompletedBar],
    *,
    holding_bars: int = 20,
    roundtrip_friction_pct: float = 0.5,
    max_entry_gap_pct: float = 1.0,
) -> HypotheticalOutcome:
    """Conservative post-snapshot proxy at next completed candle OPEN.

    Stops take precedence when stop and target touch the same OHLC candle.
    An uncompleted outcome stays CENSORED; it is never booked as a win.
    """
    if snapshot.get("research_only") is not True or snapshot.get("broker_account_connected") is not False:
        raise ValueError("only frozen research-only, broker-disconnected snapshots supported")
    if snapshot.get("schema") != "cl-signal-funded-forward-snapshot-v1":
        raise ValueError("unsupported frozen research snapshot schema")
    expected_hash=str(snapshot.get("payload_sha256") or "")
    raw={key:value for key,value in snapshot.items() if key!="payload_sha256"}
    canonical=json.dumps(raw,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False)
    actual_hash=sha256(canonical.encode("utf-8")).hexdigest()
    if not compare_digest(expected_hash,actual_hash):
        raise ValueError("frozen prospective snapshot content hash mismatch")
    if row not in snapshot.get("rows",[]):
        raise ValueError("outcome source row is not part of the frozen snapshot")
    kind = str(snapshot.get("asset_type") or "")
    if kind not in {"stocks","crypto"} or str(row.get("asset_type")) != kind:
        raise ValueError("mismatched funded asset kind")
    asset = str(row.get("asset") or "")
    state = str(row.get("status") or "")
    if not asset.strip():
        raise ValueError("asset identifier missing")
    candidate_states = STOCK_PAPER_STATES if kind=="stocks" else CRYPTO_PAPER_STATES
    if state not in candidate_states:
        return HypotheticalOutcome(asset,kind,"NOT A RESEARCH ENTRY",None,None,"NO ENTRY",None,0)

    captured = _stamp(str(snapshot.get("captured_at_utc") or ""))
    if not isinstance(holding_bars,int) or not 1 <= holding_bars <= 240:
        raise ValueError("invalid prospective holding horizon")
    if not 0.05 <= roundtrip_friction_pct <= 5:
        raise ValueError("unverified or invalid roundtrip trading cost")
    if not 0 <= max_entry_gap_pct <= 5:
        raise ValueError("invalid predeclared entry-gap tolerance")

    try:
        reference = _safe_positive(row.get("entry"),"frozen entry")
        stop = _safe_positive(row.get("stop"),"frozen stop")
        target = _safe_positive(row.get("target"),"frozen target")
    except ValueError:
        return HypotheticalOutcome(asset,kind,"INCOMPLETE FROZEN PLAN",None,None,"NO ENTRY",None,0)
    if not stop < reference < target:
        return HypotheticalOutcome(asset,kind,"INVALID FROZEN PLAN",None,None,"NO ENTRY",None,0)
    bars = list(later_completed_bars)
    if not bars:
        return HypotheticalOutcome(asset,kind,"AWAITING FUTURE BARS",None,None,"NO ENTRY",None,0)
    last = captured
    for bar in bars:
        t = bar.opened_at_utc
        if not isinstance(t,datetime) or t.tzinfo is None or t.utcoffset() is None:
            raise ValueError("future completed bar timestamps must have timezone")
        t = t.astimezone(timezone.utc)
        if t <= last:
            raise ValueError("future bars must be strictly later than frozen capture time")
        last = t
        prices = (bar.open,bar.high,bar.low,bar.close)
        if not all(isfinite(v) and v>0 for v in prices) or bar.high<max(bar.open,bar.low,bar.close) or bar.low>min(bar.open,bar.high,bar.close):
            raise ValueError("invalid future completed OHLC values")

    observed = bars[:holding_bars]
    opening = observed[0].open
    if not stop < opening < target:
        return HypotheticalOutcome(asset,kind,"NEXT OPEN OUTSIDE PLAN",None,None,"NO ENTRY",None,1)
    if abs(opening/reference-1)*100 > max_entry_gap_pct:
        return HypotheticalOutcome(asset,kind,"NEXT OPEN OUTSIDE PREDECLARED GAP",None,None,"NO ENTRY",None,1)

    halfcost = roundtrip_friction_pct/200
    entry_ask = opening*(1+halfcost)
    stop_bid = stop*(1-halfcost)
    target_bid = target*(1-halfcost)
    risk = entry_ask-stop_bid
    if risk <= 0 or target_bid <= entry_ask:
        return HypotheticalOutcome(asset,kind,"NONPOSITIVE NET REWARD/RISK",None,None,"NO ENTRY",None,1)
    for n,bar in enumerate(observed,1):
        if bar.open <= stop:
            exit_at = bar.open*(1-halfcost)
            why = "STOP GAP (OPEN)"
        elif bar.low <= stop:
            exit_at = stop_bid
            why = "STOP (SAME-BAR STOP FIRST)" if bar.high>=target else "STOP"
        elif bar.high >= target:
            exit_at = target_bid
            why = "TARGET"
        else:
            continue
        return HypotheticalOutcome(asset,kind,"COMPLETED RESEARCH PROXY",round(entry_ask,6),round(exit_at,6),why,round((exit_at-entry_ask)/risk,4),n)

    if len(observed)<holding_bars:
        return HypotheticalOutcome(asset,kind,"CENSORED — MORE BARS NEEDED",round(entry_ask,6),None,"INCOMPLETE WINDOW",None,len(observed))

    exit_at=observed[-1].close*(1-halfcost)
    return HypotheticalOutcome(asset,kind,"COMPLETED RESEARCH PROXY",round(entry_ask,6),round(exit_at,6),"TIME EXIT",round((exit_at-entry_ask)/risk,4),len(observed))
