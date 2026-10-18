"""Prospective Kraken Funded PUBLIC-CANDLE shadow outcomes; never broker trades.

Only future completed candles after an already-frozen, integrity-checked signal
are eligible. Missing providers, WATCH/PASS and plans without a valid bracket
can never enter a simulated position. Intrabar ambiguity resolves against the
hypothetical long. The result is NOT Kraken Funded execution/performance.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math

from funded_forward_health import RESEARCH_CRYPTO_TRIGGER, RESEARCH_STOCK_TRIGGERS, validate_snapshot

STOCK_ENTRY_SESSIONS = 3
STOCK_HOLD_SESSIONS = 20
CRYPTO_ENTRY_BARS = 6  # completed 4h candles: up to 24 hours
CRYPTO_HOLD_BARS = 42  # completed 4h candles: up to 7 days
SHADOW_ROUNDTRIP_COST_PCT = 0.5  # scenario, NOT actual Kraken Funded spread/fees
MIN_REFERENCE_RR = 2.0


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _utc(value):
    raw = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if raw.tzinfo is None or raw.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    return raw.astimezone(timezone.utc)


def _bars_after_capture(kind, raw, observed, captured, evaluated):
    """Return complete, valid *future* bars, or a fail-closed error reason."""
    if raw is None:
        return [], "PROVIDER DATA UNAVAILABLE"
    if not isinstance(raw, list):
        return [], "PRICE INPUT INVALID"
    parsed = []
    for item in raw:
        if not isinstance(item, dict):
            return [], "PRICE BAR INVALID"
        try:
            when_text = str(item.get("timestamp") or "")
            if kind == "stocks":
                if len(when_text) != 10:
                    return [], "STOCK SESSION DATE INVALID"
                day = datetime.fromisoformat(when_text).date()
                if day.isoformat() != when_text:
                    return [], "STOCK SESSION DATE INVALID"
                if day <= observed.date():
                    continue
                # Yahoo daily OHLCV are not final during the US session.
                ny_now = evaluated.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
                if day > ny_now.date() or (day == ny_now.date() and (ny_now.hour, ny_now.minute) < (16, 15)):
                    continue
                instant = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            else:
                instant = _utc(when_text)
                # Crypto candle timestamps are OPEN times (4h).
                if instant < captured or instant <= observed:
                    continue
                if instant + timedelta(hours=4, minutes=1) > evaluated:
                    continue
                if instant.hour % 4 or instant.minute or instant.second:
                    return [], "CRYPTO 4H CANDLE OPEN MISALIGNED"
            numbers = [_number(item.get(k)) for k in ("open", "high", "low", "close")]
            if any(v is None or v <= 0 for v in numbers):
                return [], "PRICE OHLC INVALID"
            op, high, low, close = numbers
            if high < max(op, close, low) or low > min(op, close, high):
                return [], "PRICE OHLC INCONSISTENT"
            parsed.append({"time": instant, "open": op, "high": high, "low": low, "close": close})
        except (ValueError, OverflowError, TypeError):
            return [], "PRICE BAR TIMESTAMP INVALID"
    parsed.sort(key=lambda b: b["time"])
    if len({b["time"] for b in parsed}) != len(parsed):
        return [], "DUPLICATE FUTURE CANDLES"
    if kind == "crypto" and parsed:
        # The next candle OPEN must be the next 4h boundary after capture.
        floor = captured.replace(hour=captured.hour - captured.hour % 4, minute=0, second=0, microsecond=0)
        expected = floor if floor == captured else floor + timedelta(hours=4)
        if parsed[0]["time"] > expected + timedelta(minutes=1):
            return [], "CRYPTO FIRST FUTURE CANDLE MISSING"
        for before, after in zip(parsed, parsed[1:]):
            if after["time"] - before["time"] != timedelta(hours=4):
                return [], "CRYPTO INTERMEDIATE CANDLE MISSING"
    if kind == "stocks" and parsed:
        if (parsed[0]["time"].date() - captured.astimezone(
                __import__("zoneinfo").ZoneInfo("America/New_York")).date()).days > 7:
            return [], "STOCK FUTURE SESSION HISTORY GAP"
        for before, after in zip(parsed, parsed[1:]):
            if (after["time"] - before["time"]).days > 7:
                return [], "STOCK INTERMEDIATE SESSION HISTORY GAP"
    return parsed, None


def _result(asset, source_status, state, *, reason="", **extra):
    return {
        "asset": asset, "original_engine_status": source_status,
        "shadow_status": state, "reason": reason,
        "broker_trade": False, "research_only": True, **extra,
    }


def _shadow_long(row, kind, raw, captured, evaluated, *, cost_pct):
    asset = row["asset"]
    source_state = str(row["status"]).upper()
    qualifies = (source_state == RESEARCH_CRYPTO_TRIGGER if kind == "crypto"
                 else source_state in RESEARCH_STOCK_TRIGGERS)
    if not qualifies:
        return _result(asset, source_state, "NO RESEARCH ENTRY", reason="Original engine did not publish an entry trigger")
    entry, stop, target, score = (_number(row.get(k)) for k in ("entry", "stop", "target", "score"))
    supplied_rr = _number(row.get("rr"))
    if (any(v is None for v in (entry, stop, target, score, supplied_rr))
            or not 0 < stop < entry < target
            or min(supplied_rr, (target-entry)/(entry-stop)) < MIN_REFERENCE_RR):
        return _result(asset, source_state, "FROZEN PLAN VETO",
                       reason="Missing bracket, score or minimum original reward:risk")
    if kind == "crypto":
        meta = row.get("meta") or {}
        if (score < 80 or meta.get("swing_eligible") is not True
                or meta.get("entry_timing_actionable") is not True
                or meta.get("execution_liquidity_pass_public_spot") is not True):
            return _result(asset, source_state, "FROZEN PLAN VETO",
                           reason="Original Crypto score, timing, shape or Kraken public liquidity not verified")
    elif score < 75:
        return _result(asset, source_state, "FROZEN PLAN VETO",
                       reason="Original funded stock reference score below 75")
    original_bar = str(row.get("bar_timestamp") or "")
    try:
        if kind == "crypto":
            observed = _utc(original_bar)
        else:
            observed = datetime.fromisoformat(original_bar).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return _result(asset, source_state, "FROZEN PLAN VETO", reason="Original completed-bar date missing")
    series, issue = _bars_after_capture(kind, raw, observed, captured, evaluated)
    if issue:
        return _result(asset, source_state, "DATA UNVERIFIED", reason=issue)
    if not series:
        return _result(asset, source_state, "AWAITING FUTURE CANDLES")
    entry_window = CRYPTO_ENTRY_BARS if kind == "crypto" else STOCK_ENTRY_SESSIONS
    hold_window = CRYPTO_HOLD_BARS if kind == "crypto" else STOCK_HOLD_SESSIONS
    opened_at = None
    paid = None
    filled_bar = None
    for i, bar in enumerate(series):
        if paid is None:
            if i >= entry_window:
                return _result(asset, source_state, "EXPIRED WITHOUT ENTRY",
                               reason=f"No entry touch within {entry_window} completed bars")
            if bar["high"] < entry:
                continue
            paid = max(entry, bar["open"])  # upward open gap is an adverse fill
            if paid >= target or paid <= stop or (target-paid)/(paid-stop) < MIN_REFERENCE_RR:
                return _result(asset, source_state, "GAP / REWARD:RISK VETO",
                               reason="First available public-candle entry invalidates target or minimum R:R")
            opened_at = bar["time"].isoformat()
            filled_bar = i
        # A 4h/daily candle cannot establish whether high or low happened first.
        # The conservative stop wins if both are touched after possible entry.
        if bar["open"] <= stop and i > filled_bar:
            exit_price, label = bar["open"], "SHADOW STOP (GAP)"
        elif bar["low"] <= stop:
            exit_price, label = stop, "SHADOW STOP"
        elif bar["high"] >= target:
            exit_price, label = target, "SHADOW TARGET"
        elif i - filled_bar + 1 >= hold_window:
            exit_price, label = bar["close"], "SHADOW TIMEOUT"
        else:
            continue
        planned_risk = paid - stop
        net_r = ((exit_price - paid) - paid * cost_pct/100) / planned_risk
        return _result(asset, source_state, label,
                       entry_at=opened_at, exit_at=bar["time"].isoformat(),
                       hypothetical_entry=round(paid, 8), hypothetical_exit=round(exit_price, 8),
                       shadow_net_r=round(net_r, 5), scenario_roundtrip_cost_pct=cost_pct,
                       reason="Public OHLCV proxy; pessimistic intrabar sequencing; not a Kraken Funded fill")
    if paid is None:
        return _result(asset, source_state, "AWAITING ENTRY" if len(series) < entry_window
                       else "EXPIRED WITHOUT ENTRY",
                       reason=f"Observed {min(len(series), entry_window)}/{entry_window} entry-window bars")
    return _result(asset, source_state, "SHADOW OPEN", entry_at=opened_at,
                   hypothetical_entry=round(paid, 8),
                   reason="Hypothetical open position only; no account orders or claimed P&L")


def evaluate_snapshot(snapshot, candles_by_asset, *, evaluated_at, universe=None,
                      roundtrip_cost_pct=SHADOW_ROUNDTRIP_COST_PCT):
    """A new, independently timestamped report referencing an unmodified archive."""
    validate_snapshot(snapshot, universe=universe)
    if not 0 <= roundtrip_cost_pct <= 5:
        raise ValueError("Research cost scenario must be within 0–5%")
    clock = _utc(evaluated_at.isoformat() if isinstance(evaluated_at, datetime) else evaluated_at)
    captured = _utc(snapshot["captured_at_utc"])
    if clock < captured:
        raise ValueError("Cannot score a frozen future signal using pre-capture prices")
    kind = snapshot["asset_type"]
    rows = [_shadow_long(item, kind, candles_by_asset.get(item["asset"]),
                         captured, clock, cost_pct=roundtrip_cost_pct)
            for item in snapshot["rows"]]
    counts = Counter(x["shadow_status"] for x in rows)
    closed = [r for r in rows if r["shadow_status"].startswith(("SHADOW STOP", "SHADOW TARGET", "SHADOW TIMEOUT"))]
    return {
        "schema": "cl-signal-funded-forward-outcomes-v1",
        "source_run_id": snapshot["run_id"],
        "source_sha256": snapshot["payload_sha256"],
        "asset_type": kind,
        "evaluated_at_utc": clock.isoformat(),
        "research_only": True,
        "funded_account_connected": False,
        "broker_trades": 0,
        "funded_challenge_pass_claimed": False,
        "cost_scenario_roundtrip_pct": roundtrip_cost_pct,
        "hypothetical_closed_scenarios": len(closed),
        "hypothetical_net_r_sum": round(sum(r["shadow_net_r"] for r in closed), 5),
        "aggregate_is_not_account_equity_or_strategy_pnl": True,
        "statuses": dict(sorted(counts.items())),
        "rows": rows,
    }


def write_outcome_once(destination, result):
    """Never modify the archived original signal or overwrite an old evaluation."""
    from pathlib import Path
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(result, output, sort_keys=True, indent=2, allow_nan=False)
        output.write("\n")
    return path
