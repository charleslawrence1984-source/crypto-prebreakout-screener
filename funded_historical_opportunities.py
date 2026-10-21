"""Point-in-time opportunity census for Kraken Funded RESEARCH ONLY.

Future closes determine an *outcome label*, NEVER a historic setup decision.
Only externally supplied, separately replayed historic signal/gate records
determine whether a pre-move opportunity was noticed by the real strategy.
Unreviewed assets are UNKNOWN, not fabricated false negatives.

This is neither an executable portfolio backtest nor proof that the currently
listed universe existed at every historical point. No orders or broker access.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import math

import pandas as pd


def _stamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        raise ValueError("missing timestamp")
    return ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo else ts


def _clean_close(frame: pd.DataFrame) -> pd.Series:
    if frame is None or frame.empty or "Close" not in frame:
        return pd.Series(dtype=float)
    data = frame[["Close"]].copy()
    data.index = pd.DatetimeIndex([_stamp(x) for x in data.index])
    if data.index.has_duplicates:
        # Duplicate OHLCV observations are an ambiguous research input.
        raise ValueError("duplicate historical bar timestamp")
    if not data.index.is_monotonic_increasing:
        data = data.sort_index()
    close = pd.to_numeric(data["Close"], errors="coerce")
    if close.isna().any() or not (close > 0).all() or not close.map(math.isfinite).all():
        raise ValueError("invalid or missing complete historical close")
    return close


def _lookup(events, *, symbol_key, date_key, label):
    found = {}
    for row in events:
        symbol = str(row.get(symbol_key) or "").strip()
        if not symbol:
            raise ValueError(f"{label}: missing ticker")
        key = (symbol, _stamp(row[date_key]))
        if key in found:
            raise ValueError(f"{label}: duplicate symbol and decision timestamp {key}")
        found[key] = dict(row)
    return found


def _fold(i, total, horizon):
    """Chronological 60/20/20; purge anchors whose future crosses next fold."""
    train_end = int(total * 0.6)
    valid_end = int(total * 0.8)
    if i < train_end:
        return "PURGED_TRAIN_TO_VALIDATION" if i + horizon >= train_end else "TRAIN"
    if i < valid_end:
        return "PURGED_VALIDATION_TO_HOLDOUT" if i + horizon >= valid_end else "VALIDATION"
    return "HOLDOUT"


def opportunity_census(
    histories: dict[str, pd.DataFrame],
    *, asset_type: str,
    signals=(),
    audit_trace=(),
    audited_symbols=(),
    windows=(5, 10, 20),
    thresholds_pct=None,
    min_history=None,
):
    """Full eligible anchor census with forward CLOSE outcomes and honest gates.

    For each eligible time t, classify the original technical decision using
    audit records computed at or BEFORE t. The future price path from t+1
    through t+h may only label the later outcome. Missing/no audit is UNKNOWN,
    never a missed trade. Non-movers are kept in the denominator.

    Overlapping anchors are correlated: results are descriptive research,
    NOT independent evidence or broker-executable performance.
    """
    if asset_type not in ("stocks", "crypto"):
        raise ValueError("asset_type must be stocks or crypto")
    windows = tuple(int(x) for x in windows)
    thresholds = tuple(float(x) for x in (thresholds_pct or
                          ((5, 10, 20) if asset_type == "stocks" else (10, 20, 30))))
    min_history = int(min_history or (252 if asset_type == "stocks" else 70))
    if min_history < 2 or not windows or not thresholds or any(x < 1 for x in windows):
        raise ValueError("invalid research windows or warmup")
    if any(not math.isfinite(x) or x <= 0 for x in thresholds):
        raise ValueError("invalid research thresholds")

    signal_map = _lookup(signals, symbol_key="Ticker", date_key="Signal date", label="signal")
    audit_map = _lookup(audit_trace, symbol_key="Ticker", date_key="Signal date", label="audit")
    eligible = set(audited_symbols)
    signal_symbols = {s for s, _ in signal_map}
    if not signal_symbols.issubset(eligible):
        raise ValueError("qualified signals require the symbol's point-in-time audit")
    if {s for s, _ in audit_map} - eligible:
        raise ValueError("audit trace contains a symbol not in audited_symbols")

    rows = []
    diagnostics = []
    for symbol in sorted(histories):
        close = _clean_close(histories[symbol])
        if len(close) < min_history + min(windows):
            diagnostics.append({"asset": symbol, "status": "INSUFFICIENT HISTORY",
                                "bars": len(close)})
            continue
        historical_stamps = set(close.index)
        unknown_decisions = []
        for (s, t) in set(signal_map) | set(audit_map):
            if s == symbol and t not in historical_stamps:
                unknown_decisions.append(str(t))
        if unknown_decisions:
            raise ValueError(f"{symbol}: replay decision timestamp not in history")

        diagnostics.append({"asset": symbol,
                            "status": "PREFILTER AUDITED" if symbol in eligible else "NOT AUDITED",
                            "bars": len(close),
                            "first_bar": str(close.index[0]),
                            "last_bar": str(close.index[-1])})
        dates = close.index
        values = close.to_numpy(dtype=float)
        for i in range(min_history - 1, len(close)):
            t = dates[i]
            key = (symbol, t)
            signal = signal_map.get(key)
            audit = audit_map.get(key)
            if signal is not None:
                state = "QUALIFIED_TECHNICAL_SIGNAL"
                gate = str(signal.get("Route") or "QUALIFIED; NO BROKER VALIDATION")
            elif symbol not in eligible:
                state = "UNKNOWN_NOT_AUDITED"
                gate = "NO POINT-IN-TIME ORIGINAL SIGNAL REPLAY"
            elif audit is not None:
                core = str(audit.get("Core state") or "UNKNOWN")
                state = ("WATCH_ONLY" if "WATCH" in core or "REVIEW" in core
                         else "PREFILTER_REJECTED")
                gate = "CORE: " + str(audit.get("Core reason") or core)
                if audit.get("Breakout trigger"):
                    brk_state = str(audit.get("Breakout state") or "UNKNOWN")
                    brk_reason = str(audit.get("Breakout reason") or "")
                    if brk_state.startswith("NOT EVALUATED"):
                        gate += " | BREAKOUT: NOT EVALUATED BY ORIGINAL CORE ROUTING"
                    elif brk_reason:
                        gate += " | BREAKOUT: " + brk_reason
                    else:
                        gate += " | BREAKOUT PREFILTER: true (actual rejection gate not supplied)"
            else:
                state = "NO_PREFILTER_TRIGGER"
                gate = "NO ELIGIBLE TECHNICAL PREFILTER; FULL ENGINE NOT EVALUATED"
            for horizon in windows:
                if i + horizon >= len(close):
                    # An unfinished full horizon is censored, never a negative.
                    continue
                forward = values[i+1:i+horizon+1]
                excursion = (float(forward.max()) / values[i] - 1) * 100
                downside = (float(forward.min()) / values[i] - 1) * 100
                fold = _fold(i, len(close), horizon)
                for threshold in thresholds:
                    is_mover = excursion >= threshold
                    earliest = next((int(j) for j, val in enumerate(forward, start=1)
                                     if (val / values[i] - 1) * 100 >= threshold), None)
                    audited = symbol in eligible
                    rows.append({
                        "asset_type": asset_type, "Ticker": symbol,
                        "Signal date": str(t), "Window bars": horizon,
                        "Threshold %": threshold, "Fold": fold,
                        "Original gate class": state,
                        "Original gate reason": gate[:220],
                        "Original technical signal": bool(signal is not None),
                        "Gate audited": audited,
                        "Starting close proxy": round(float(values[i]), 8),
                        "Max future close change %": round(excursion, 5),
                        "Min future close change %": round(downside, 5),
                        "Mover": bool(is_mover),
                        "First threshold reach bars": earliest,
                        "Missed-mover anchor (AUDITED ONLY)": bool(
                            is_mover and audited and signal is None
                        ),
                        "No actual Kraken account or fills": True,
                    })
    return pd.DataFrame(rows), pd.DataFrame(diagnostics)


def census_summary(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame()
    eligible = ledger[ledger["Fold"].isin(["TRAIN", "VALIDATION", "HOLDOUT"])
                      & ledger["Gate audited"]]
    summary = []
    for (asset, horizon, threshold, fold), grp in eligible.groupby(
            ["asset_type", "Window bars", "Threshold %", "Fold"], sort=True):
        mover = grp[grp["Mover"]]
        triggered = grp[grp["Original technical signal"]]
        caught = int(mover["Original technical signal"].sum())
        mover_count = len(mover)
        qualified_count = len(triggered)
        summary.append({
            "asset_type": asset, "Window bars": int(horizon),
            "Threshold %": float(threshold), "Fold": fold,
            "Eligible decision anchors": len(grp),
            "Future mover anchors": mover_count,
            "Original technical triggers": qualified_count,
            "Triggered and future mover": caught,
            "Missed mover anchors": mover_count - caught,
            "Triggered but not future mover": qualified_count - caught,
            "Observed mover recall %": (round(100 * caught / mover_count, 2)
                                        if mover_count else None),
            "Observed trigger precision %": (round(100 * caught / qualified_count, 2)
                                             if qualified_count else None),
            "Not audited anchors excluded": int(len(ledger[~ledger["Gate audited"]])),
            "Warning": "Correlated anchors; raw Yahoo/Kraken proxy outcomes, not trade returns",
        })
    return pd.DataFrame(summary)


def export_research(out_dir: str | Path, ledger, diagnostics, summary, metadata):
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(path / "all_anchor_outcomes.csv", index=False)
    diagnostics.to_csv(path / "universe_coverage.csv", index=False)
    summary.to_csv(path / "mover_vs_nonmover_by_fold.csv", index=False)
    (path / "assumptions.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path
