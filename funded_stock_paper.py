"""Prospective, CSV-backed research journal for Kraken Funded STOCKS ONLY.

This is NOT an order manager, an executable quote feed, a broker ledger, or
tamper-proof evidence. Record *new* signals before the next US regular-session
open, export the CSV yourself, then import it on future visits. Paper outcomes
are recomputed from frozen signal-day levels using subsequently completed daily
bars. The production entry/stop/target rules are never changed.
"""
from __future__ import annotations

import io
import math
import pandas as pd

STOP_METHODS = (
    ("APPROVED STRUCTURAL (control)", "Reference stop"),
    ("RESISTANCE RETEST (research)", "Research retest stop"),
)
COSTS_PCT = (0.5, 1.0)
MAX_HOLD_SESSIONS = 20
ALLOWED_FAILURES = {"PROJECTED R:R BELOW 2", "STRUCTURAL STOP OUTSIDE 0.4–8%"}
SNAPSHOT_COLUMNS = [
    "Paper ID", "Created UTC", "Ticker", "Signal date",
    "Original technical state", "Original technical reason",
    "Stop method", "Roundtrip cost %", "Planned hold sessions",
    "Signal close", "Resistance", "Frozen stop", "Frozen target",
]
RESULT_COLUMNS = [
    "Status", "Next entry date", "Entry open proxy", "Net entry R:R",
    "Exit date", "Exit reason", "Realized R", "Last observed session",
]
COLUMNS = SNAPSHOT_COLUMNS + RESULT_COLUMNS


def empty_journal():
    return pd.DataFrame(columns=COLUMNS)


def _num(raw):
    try:
        value = float(raw)
        return value if math.isfinite(value) else math.nan
    except (TypeError, ValueError):
        return math.nan


def _date(value):
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).date()


def _time(now):
    timestamp = pd.Timestamp(now)
    if timestamp.tzinfo is None:
        raise ValueError("UTC-aware capture timestamp required")
    return timestamp.tz_convert("UTC")


def _candidate(row, now):
    """Accept only fresh, fully completed original signal plans.

    Never fabricate a signal from historical backtest exports, core WATCH,
    unavailable data, or an ordinary rejected breakout with other vetoes.
    """
    if str(row.get("Scan source")) != "LIVE":
        return None
    tick = str(row.get("Ticker") or "").upper().strip()
    if not tick or not tick.replace(".", "").isalnum():
        return None
    scanned = pd.to_datetime(row.get("scanned_at"), errors="coerce", utc=True)
    if pd.isna(scanned) or not 0 <= (now - scanned).total_seconds() <= 900:
        return None
    signal_day = _date(row.get("Breakout date"))
    bar_day = _date(row.get("Last completed bar"))
    if signal_day is None or signal_day != bar_day:
        return None

    ny_now = now.tz_convert("America/New_York")
    # Do not allow journal entries once the next regular US stock session
    # could have opened. BDay errs conservatively on US exchange holidays.
    next_regular = (pd.Timestamp(signal_day) + pd.offsets.BDay(1)).date()
    if ny_now.date() > next_regular or (
        ny_now.date() == next_regular and (ny_now.hour, ny_now.minute) >= (9, 30)
    ):
        return None
    if (ny_now.date() - signal_day).days < 0 or (ny_now.date() - signal_day).days > 5:
        return None

    state = str(row.get("Technical state") or "")
    reason = str(row.get("Gate") or "")
    reasons = {x.strip() for x in reason.split(";") if x.strip()}
    if state == "FUNDED BREAKOUT REVIEW":
        if not reasons or not reasons.issubset(ALLOWED_FAILURES):
            return None
    elif state != "FUNDED BREAKOUT PENDING OPEN":
        return None
    score = _num(row.get("Technical score"))
    if not math.isfinite(score) or score < 75:
        return None

    close = _num(row.get("Reference entry"))
    resistance = _num(row.get("Resistance"))
    target = _num(row.get("Reference target"))
    original = _num(row.get("Reference stop"))
    retest = _num(row.get("Research retest stop"))
    if not all(math.isfinite(x) for x in (close, resistance, target, original, retest)):
        return None
    if not (0 < original < close < target and 0 < retest < close and resistance > 0):
        return None
    return tick, signal_day, state, reason, close, resistance, target, original, retest


def capture_new_breakouts(scan, existing, now):
    """Append immutable plans for currently fresh signals; never backfill."""
    time = _time(now)
    frame = existing.copy() if existing is not None else empty_journal()
    if frame.empty:
        frame = empty_journal()
    if scan is None or scan.empty:
        return frame, 0
    existing_ids = set(frame["Paper ID"].astype(str))
    additions = []
    for _, scan_row in scan.iterrows():
        result = _candidate(scan_row, time)
        if result is None:
            continue
        tick, signal_day, state, reason, close, resistance, target, original, retest = result
        for method, stop in ((STOP_METHODS[0][0], original), (STOP_METHODS[1][0], retest)):
            for cost in COSTS_PCT:
                paper_id = f"{tick}|{signal_day.isoformat()}|{method}|{cost:.2f}"
                if paper_id in existing_ids:
                    continue
                additions.append({
                    "Paper ID": paper_id,
                    "Created UTC": time.isoformat(),
                    "Ticker": tick,
                    "Signal date": signal_day.isoformat(),
                    "Original technical state": state,
                    "Original technical reason": reason,
                    "Stop method": method,
                    "Roundtrip cost %": cost,
                    "Planned hold sessions": MAX_HOLD_SESSIONS,
                    "Signal close": round(close, 6),
                    "Resistance": round(resistance, 6),
                    "Frozen stop": round(stop, 6),
                    "Frozen target": round(target, 6),
                    "Status": "AWAITING NEXT COMPLETE US SESSION",
                    "Next entry date": "",
                    "Entry open proxy": math.nan,
                    "Net entry R:R": math.nan,
                    "Exit date": "",
                    "Exit reason": "",
                    "Realized R": math.nan,
                    "Last observed session": signal_day.isoformat(),
                })
                existing_ids.add(paper_id)
    if additions:
        frame = pd.concat([frame, pd.DataFrame(additions, columns=COLUMNS)], ignore_index=True)
    return frame[COLUMNS], len(additions)


def import_journal_csv(content, allowed_tickers, now=None):
    """Fail closed on corrupt or structurally inconsistent user-maintained CSV."""
    current = _time(now) if now is not None else pd.Timestamp.now(tz="UTC")
    if not isinstance(content, bytes) or len(content) > 2_000_000:
        raise ValueError("Journal must be an uploaded CSV no larger than 2 MB")
    try:
        data = pd.read_csv(io.BytesIO(content), keep_default_na=False)
    except (pd.errors.ParserError, UnicodeError, ValueError) as exc:
        raise ValueError("Cannot parse journal CSV") from exc
    if len(data) > 10000 or not set(COLUMNS).issubset(data.columns):
        raise ValueError("Journal CSV has invalid or missing research columns")
    if data["Paper ID"].astype(str).duplicated().any():
        raise ValueError("Duplicate paper-trade IDs detected")
    valid_tickers = set(allowed_tickers)
    known_methods = {m[0] for m in STOP_METHODS}
    for _, row in data.iterrows():
        tick = str(row["Ticker"])
        signal = _date(row["Signal date"])
        captured = pd.to_datetime(row["Created UTC"], errors="coerce", utc=True)
        cost = _num(row["Roundtrip cost %"])
        hold = _num(row["Planned hold sessions"])
        method = str(row["Stop method"])
        close, resistance, stop, target = (
            _num(row[col]) for col in
            ("Signal close", "Resistance", "Frozen stop", "Frozen target")
        )
        if (
            tick not in valid_tickers or signal is None or pd.isna(captured)
            or captured > current + pd.Timedelta(minutes=2)
            or method not in known_methods or cost not in COSTS_PCT
            or hold != MAX_HOLD_SESSIONS
            or not all(math.isfinite(v) for v in (close, resistance, stop, target))
            or not (0 < stop < close < target and resistance > 0)
            or str(row["Paper ID"]) != f"{tick}|{signal.isoformat()}|{method}|{cost:.2f}"
            or str(row["Original technical state"]) not in (
                "FUNDED BREAKOUT REVIEW", "FUNDED BREAKOUT PENDING OPEN"
            )
        ):
            raise ValueError("Invalid or altered frozen paper signal in CSV")
        # A captured record must predate the next regular-session open.
        ny = captured.tz_convert("America/New_York")
        nxt = (pd.Timestamp(signal) + pd.offsets.BDay(1)).date()
        if ny.date() > nxt or (ny.date() == nxt and (ny.hour, ny.minute) >= (9, 30)):
            raise ValueError("Paper signal appears captured after next-session open")
    return data[COLUMNS].copy()


def _next_completed_bars(data, signal_day):
    if data is None or data.empty:
        return pd.DataFrame()
    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(data.columns):
        return pd.DataFrame()
    clean = data.sort_index().copy()
    clean = clean.loc[~clean.index.duplicated(keep="last")]
    for col in required:
        clean[col] = pd.to_numeric(clean[col], errors="coerce")
    if not clean[list(required)].notna().all(axis=None):
        # Never silently skip a missing OHLC row and shift the entry forward.
        return pd.DataFrame()
    return clean.loc[
        [pd.Timestamp(x).date() > signal_day for x in clean.index]
    ].copy()


def update_paper_outcomes(journal, completed_histories, now):
    """Recompute outcomes from frozen plans, without new/retroactive snapshots.

    Prices must be completed NY regular-session daily candles, validated by the
    UI data layer. No automatic Kraken orders and no booked cash P&L.
    """
    time = _time(now)
    if journal is None or journal.empty:
        return empty_journal()
    updated = journal[COLUMNS].copy()
    for i, row in updated.iterrows():
        ticker = str(row["Ticker"])
        signal_day = _date(row["Signal date"])
        created = pd.to_datetime(row["Created UTC"], utc=True, errors="coerce")
        price_bars = _next_completed_bars(completed_histories.get(ticker), signal_day)
        for field in RESULT_COLUMNS:
            updated.at[i, field] = (
                "AWAITING NEXT COMPLETE US SESSION" if field == "Status"
                else row["Signal date"] if field == "Last observed session"
                else "" if field in ("Next entry date", "Exit date", "Exit reason")
                else math.nan
            )
        if pd.isna(created) or signal_day is None or price_bars.empty:
            continue
        first_date = pd.Timestamp(price_bars.index[0]).date()
        if (first_date - signal_day).days > 7:
            updated.at[i, "Status"] = "INCOMPLETE ENTRY PRICE HISTORY"
            continue
        first_open = _num(price_bars.iloc[0]["Open"])
        # This protects prospective evaluation even for a malformed old CSV.
        open_time = pd.Timestamp(first_date).tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30)
        if created > open_time.tz_convert("UTC"):
            updated.at[i, "Status"] = "LATE SNAPSHOT — NO VALID PAPER ENTRY"
            continue
        if first_open <= 0 or not math.isfinite(first_open):
            updated.at[i, "Status"] = "INVALID NEXT OPEN"
            continue
        stop = _num(row["Frozen stop"])
        target = _num(row["Frozen target"])
        resistance = _num(row["Resistance"])
        signal_close = _num(row["Signal close"])
        cost = _num(row["Roundtrip cost %"])
        half = cost / 200
        updated.at[i, "Next entry date"] = first_date.isoformat()
        updated.at[i, "Entry open proxy"] = round(first_open, 6)
        if not stop < first_open < target:
            updated.at[i, "Status"] = "INVALID OPEN / STOP / TARGET"
        elif first_open > signal_close * 1.01:
            updated.at[i, "Status"] = "REJECTED — GAP ABOVE SIGNAL CLOSE"
        elif first_open > resistance * 1.02:
            updated.at[i, "Status"] = "REJECTED — ABOVE RESISTANCE LIMIT"
        elif first_open < resistance * .998:
            updated.at[i, "Status"] = "REJECTED — LOST RESISTANCE"
        else:
            ask = first_open * (1 + half)
            stop_bid = stop * (1 - half)
            target_bid = target * (1 - half)
            risk_unit = ask - stop_bid
            rr = (target_bid - ask) / risk_unit if risk_unit > 0 else math.nan
            updated.at[i, "Net entry R:R"] = round(rr, 4) if math.isfinite(rr) else math.nan
            stop_pct = (first_open - stop) / first_open * 100
            if not (0.4 <= stop_pct <= 8):
                updated.at[i, "Status"] = "REJECTED — STOP OUTSIDE 0.4–8%"
            elif not math.isfinite(rr) or rr < 2:
                updated.at[i, "Status"] = "REJECTED — NET R:R BELOW 2"
            else:
                updated.at[i, "Status"] = "OPEN PAPER PROXY"
                limit = int(_num(row["Planned hold sessions"]))
                forward = price_bars.iloc[:limit]
                for step, (bar_date, candle) in enumerate(forward.iterrows()):
                    op, hi, lo, close = (
                        _num(candle[k]) for k in ("Open", "High", "Low", "Close")
                    )
                    if not all(math.isfinite(x) for x in (op, hi, lo, close)):
                        updated.at[i, "Status"] = "INVALID FORWARD OHLC"
                        break
                    if op <= stop:
                        exit_bid, reason = op * (1 - half), "STOP GAP"
                    elif lo <= stop:
                        exit_bid = stop_bid
                        reason = (
                            "STOP (TARGET ALSO CROSSED)" if hi >= target else "STOP"
                        )
                    elif hi >= target:
                        exit_bid, reason = target_bid, "TARGET"
                    elif step == limit - 1:
                        exit_bid, reason = close * (1 - half), "TIME EXIT"
                    else:
                        continue
                    updated.at[i, "Status"] = "COMPLETED"
                    updated.at[i, "Exit date"] = str(pd.Timestamp(bar_date).date())
                    updated.at[i, "Exit reason"] = reason
                    updated.at[i, "Realized R"] = round((exit_bid - ask) / risk_unit, 4)
                    break
        updated.at[i, "Last observed session"] = str(pd.Timestamp(price_bars.index[-1]).date())
    return updated[COLUMNS]


def summarize_paper_journal(journal):
    columns = [
        "Stop method", "Roundtrip cost %", "Signals captured",
        "Next-open accepted", "Entry rejected", "Open or awaiting",
        "Completed", "Wins", "Mean R", "Total R",
    ]
    if journal is None or journal.empty:
        return pd.DataFrame(columns=columns)
    result = []
    for (method, cost), group in journal.groupby(["Stop method", "Roundtrip cost %"], sort=False):
        done = group[group["Status"] == "COMPLETED"]
        r = pd.to_numeric(done["Realized R"], errors="coerce").dropna()
        pending = group["Status"].isin(("OPEN PAPER PROXY", "AWAITING NEXT COMPLETE US SESSION"))
        rejected = group["Status"].astype(str).str.startswith(("REJECTED", "INVALID", "LATE", "INCOMPLETE"))
        result.append({
            "Stop method": method,
            "Roundtrip cost %": cost,
            "Signals captured": len(group),
            "Next-open accepted": len(done) + int(group["Status"].eq("OPEN PAPER PROXY").sum()),
            "Entry rejected": int(rejected.sum()),
            "Open or awaiting": int(pending.sum()),
            "Completed": len(done),
            "Wins": int((r > 0).sum()),
            "Mean R": round(float(r.mean()), 3) if len(r) else math.nan,
            "Total R": round(float(r.sum()), 3) if len(r) else math.nan,
        })
    return pd.DataFrame(result, columns=columns)
