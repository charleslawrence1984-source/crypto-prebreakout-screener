"""Historical research backtest for Kraken Funded STOCKS only.

Never live execution and NEVER Kraken 24/7 broker quote history: Yahoo daily
OHLCV prices are proxies. No historical earnings/official event calendar, so
results are deliberately labelled *TECHNICAL ONLY*, not production-ready.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
import numpy as np
import pandas as pd

from funded_stock_breakout import evaluate_funded_breakout, validate_breakout_quote
from trade_rules import evaluate_price_setup, _wilder_rsi

START_BALANCE = 1000.0
PASS_BALANCE = 1120.0
FAIL_BALANCE = 970.0
RISK_BUFFER = 10.0
MAX_COMBINED_PLANNED_RISK = 10.0
MAX_TICKER_NOTIONAL_PCT = 0.35
MAX_TOTAL_NOTIONAL_PCT = 1.0
MIN_LIQUIDITY_GBPM = 0.5
MIN_HISTORY = 252


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else math.nan
    except (TypeError, ValueError):
        return math.nan


def signal_candidates(histories, benchmark, first_date=None, audit_trace=None):
    """Generate candidates with no forward-looking data whatsoever.

    Core uses the approved completed-bar MACD/RSI engine; breakout uses the
    funded-only completed-bar engine. WATCH and REVIEW never become entries.
    A cheap vectorised prefilter avoids recalculating full engines every day.
    """
    if benchmark is None or benchmark.empty or "Close" not in benchmark:
        raise ValueError("S&P 500 benchmark daily OHLCV is required")
    benchmark = benchmark.sort_index()
    first = pd.Timestamp(first_date) if first_date is not None else None
    found = []
    diagnostics = []
    for symbol, raw in sorted(histories.items()):
        if raw is None or raw.empty or not {"Open", "High", "Low", "Close", "Volume"}.issubset(raw):
            diagnostics.append({"Ticker": symbol, "Status": "NO VALID DAILY OHLCV", "Candidate sessions": 0})
            continue
        data = raw.sort_index().copy()
        data = data.loc[~data.index.duplicated(keep="last")]
        for name in ("Open", "High", "Low", "Close", "Volume"):
            data[name] = pd.to_numeric(data[name], errors="coerce")
        data = data.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        if len(data) < MIN_HISTORY + 1:
            diagnostics.append({"Ticker": symbol, "Status": "INSUFFICIENT PRICE HISTORY", "Candidate sessions": 0})
            continue

        close, high, volume = data["Close"], data["High"], data["Volume"]
        macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        crossed = (macd > macd_signal) & (macd.shift(1) <= macd_signal.shift(1))
        rsi = _wilder_rsi(close, 14)
        core_prefilter = crossed & (rsi.rolling(10, min_periods=10).min() <= 35)
        old_resistance = high.shift(1).rolling(60, min_periods=60).max()
        breakout_prefilter = (
            (close > old_resistance)
            & (close.shift(1) <= old_resistance)
        )
        plausible = core_prefilter | breakout_prefilter
        candidates_checked = 0
        core_trigger_count = 0
        breakout_trigger_count = 0
        qualified_core = 0
        qualified_breakout = 0
        liquidity_rejections = 0
        full_rule_rejections = Counter()
        individual_gate_hits = Counter()
        breakout_suppressed_by_core = 0
        breakout_plan_observations = []
        for i in np.flatnonzero(plausible.to_numpy()):
            if i < MIN_HISTORY or i >= len(data) - 1:
                continue
            date = pd.Timestamp(data.index[i])
            if first is not None and date < first:
                continue
            # The benchmark gets data through the SIGNAL session only.
            if len(benchmark.loc[:date]) < 252:
                continue
            candidates_checked += 1
            core_trigger_count += int(bool(core_prefilter.iloc[i]))
            breakout_trigger_count += int(bool(breakout_prefilter.iloc[i]))
            past = data.iloc[:i+1]
            past_benchmark = benchmark.loc[:date]
            core = evaluate_price_setup(past, past_benchmark)
            if audit_trace is not None:
                # Research ONLY: distinguish the original trigger from
                # subsequent core-first route suppression on exact dates.
                # Capture diagnostics; never change a live trading decision.
                audit_trace.append({
                    "Ticker": symbol,
                    "Signal date": str(date.date()),
                    "Breakout trigger": bool(breakout_prefilter.iloc[i]),
                    "Core trigger": bool(core_prefilter.iloc[i]),
                    "Core state": str(core.get("technical_state", "")),
                    "Core reason": str(core.get("technical_reason", "")),
                })
            route = None
            if core.get("technical_state") == "AWAITING NEXT OPEN":
                route = "CORE RSI/MACD"
                stop = _finite(core.get("stop"))
                target = _finite(core.get("target"))
                ref = _finite(close.iloc[i])
                score = _finite(core.get("technical_score"))
                atr = _finite(core.get("atr20"))
                zone_top = _finite(core.get("zone_high"))
                if not all(math.isfinite(v) for v in (stop, target, ref, atr, zone_top)):
                    full_rule_rejections["CORE PLAN NOT FINITE"] += 1
                    individual_gate_hits["CORE: PLAN NOT FINITE"] += 1
                    continue
                plan = {"reference_entry": ref, "reference_stop": stop,
                        "reference_target": target, "atr": atr,
                        "zone_top": zone_top, "signal_date": date}
            elif core.get("technical_state") == "BLOCKED" and bool(breakout_prefilter.iloc[i]):
                brk = evaluate_funded_breakout(past, past_benchmark)
                if brk is not None:
                    brk_reasons = {
                        x.strip() for x in str(brk.get("technical_reason", "")).split(";")
                        if x.strip()
                    } if brk.get("technical_state") != "FUNDED BREAKOUT PENDING OPEN" else set()
                    ref_entry = _finite(brk.get("reference_entry"))
                    ref_stop = _finite(brk.get("reference_stop"))
                    ref_target = _finite(brk.get("reference_target"))
                    stop_pct = (
                        (ref_entry - ref_stop) / ref_entry * 100
                        if math.isfinite(ref_entry) and ref_entry > 0 and math.isfinite(ref_stop)
                        else math.nan
                    )
                    upside_pct = (
                        (ref_target - ref_entry) / ref_entry * 100
                        if math.isfinite(ref_entry) and ref_entry > 0 and math.isfinite(ref_target)
                        else math.nan
                    )
                    breakout_plan_observations.append({
                        "Ticker": symbol,
                        "Signal date": str(date.date()),
                        "Technical state": str(brk.get("technical_state") or ""),
                        "Projected R:R": (
                            _finite(brk.get("reference_reward_risk"))
                            if math.isfinite(_finite(brk.get("reference_reward_risk")))
                            else None
                        ),
                        "Projected stop %": round(stop_pct, 3) if math.isfinite(stop_pct) else None,
                        "Projected upside %": round(upside_pct, 3) if math.isfinite(upside_pct) else None,
                        "Technical score": (
                            _finite(brk.get("technical_score"))
                            if math.isfinite(_finite(brk.get("technical_score")))
                            else None
                        ),
                        # Research-only signal-day levels. Outcomes are
                        # calculated separately using FORWARD bars and can
                        # never change the signal qualification decision.
                        "Reference entry": ref_entry if math.isfinite(ref_entry) else None,
                        "Reference stop": ref_stop if math.isfinite(ref_stop) else None,
                        "Reference target": ref_target if math.isfinite(ref_target) else None,
                        "Resistance": _finite(brk.get("resistance")),
                        "Support stop": _finite(brk.get("research_stop_support")),
                        "Retest stop": _finite(brk.get("research_stop_retest")),
                        "Other gates failed": len(brk_reasons - {
                            "PROJECTED R:R BELOW 2",
                            "STRUCTURAL STOP OUTSIDE 0.4–8%",
                        }),
                        "R:R failed": "PROJECTED R:R BELOW 2" in brk_reasons,
                        "Stop-distance failed": "STRUCTURAL STOP OUTSIDE 0.4–8%" in brk_reasons,
                        "Resistance-test failed": "RESISTANCE NOT TESTED TWICE" in brk_reasons,
                        "All technical gates passed": not brk_reasons,
                    })
                if brk is None or brk.get("technical_state") != "FUNDED BREAKOUT PENDING OPEN":
                    reason_text = (
                        str(brk.get("technical_reason", "NOT DETECTED"))
                        if brk else "NOT DETECTED"
                    )
                    full_rule_rejections["BREAKOUT: " + reason_text] += 1
                    # Each breakout evaluation can fail several independent
                    # safety gates; count EACH one, not their combined string.
                    for veto in {x.strip() for x in reason_text.split(";") if x.strip()}:
                        individual_gate_hits["BREAKOUT: " + veto] += 1
                    continue
                score = _finite(brk.get("technical_score"))
                if not math.isfinite(score) or score < 75:
                    full_rule_rejections["BREAKOUT QUALITY SCORE BELOW 75"] += 1
                    individual_gate_hits["BREAKOUT: QUALITY SCORE BELOW 75"] += 1
                    continue
                route = "FUNDED BREAKOUT"
                plan = {
                    "reference_entry": _finite(brk.get("reference_entry")),
                    "reference_stop": _finite(brk.get("reference_stop")),
                    "reference_target": _finite(brk.get("reference_target")),
                    "resistance": _finite(brk.get("resistance")),
                    "signal_date": date,
                }
            else:
                failure = "CORE: " + str(core.get("technical_reason", "NO CONFIRMED SETUP"))
                full_rule_rejections[failure] += 1
                individual_gate_hits[failure] += 1
                if bool(breakout_prefilter.iloc[i]) and core.get("technical_state") != "BLOCKED":
                    # Mirrors the LIVE priority path: an existing core WATCH
                    # can prevent a parallel breakout evaluation. Report this
                    # separately rather than incorrectly call it a failed gate.
                    breakout_suppressed_by_core += 1
                continue

            turnover = float((past["Close"] * past["Volume"]).tail(20).median())
            if not math.isfinite(turnover) or turnover * 0.75 / 1e6 < MIN_LIQUIDITY_GBPM:
                liquidity_rejections += 1
                continue
            if route == "CORE RSI/MACD":
                qualified_core += 1
            elif route == "FUNDED BREAKOUT":
                qualified_breakout += 1
            next_date = pd.Timestamp(data.index[i+1])
            found.append({
                "Ticker": symbol, "Route": route,
                "Signal date": date, "Entry date": next_date,
                "Score": score, "Plan": plan,
            })
        diagnostics.append({
            "Ticker": symbol,
            "Status": "SCANNED",
            "Candidate sessions": candidates_checked,
            "Core trigger sessions": core_trigger_count,
            "Breakout trigger sessions": breakout_trigger_count,
            "Full-rule rejections": sum(full_rule_rejections.values()),
            "Top rule blocker": full_rule_rejections.most_common(1)[0][0] if full_rule_rejections else "",
            "Top individual gate": individual_gate_hits.most_common(1)[0][0] if individual_gate_hits else "",
            "Individual gate hits JSON": json.dumps(dict(individual_gate_hits), sort_keys=True),
            "Breakouts bypassed due to core WATCH": breakout_suppressed_by_core,
            "Breakout plan observations JSON": json.dumps(
                breakout_plan_observations, allow_nan=False,
            ),
            "Liquidity rejections": liquidity_rejections,
            "Qualified core signals": qualified_core,
            "Qualified breakout signals": qualified_breakout,
            "Completed bars": len(data),
            "First bar": str(data.index[0].date()),
            "Last bar": str(data.index[-1].date()),
        })
    found.sort(key=lambda x: (x["Entry date"], -x["Score"], x["Ticker"]))
    return found, pd.DataFrame(diagnostics)


def summarize_individual_rule_gates(diagnostics):
    """Aggregate actual rejection gate hits rather than per-symbol modal labels.

    One rejected breakout can fail several gates, so totals can exceed the
    number of rejected candidate sessions. Core states report their FIRST
    blocker only, because the underlying core evaluator returns early.
    """
    if diagnostics is None or diagnostics.empty:
        return pd.DataFrame(columns=[
            "Route", "Failed condition", "Signal sessions affected",
            "% of all candidate sessions", "Stocks affected",
        ])
    total_sessions = int(pd.to_numeric(
        diagnostics.get("Candidate sessions", pd.Series(dtype=float)),
        errors="coerce",
    ).fillna(0).sum())
    totals = Counter()
    stocks = defaultdict(set)
    for _, item in diagnostics.iterrows():
        ticker = str(item.get("Ticker", ""))
        try:
            gates = json.loads(str(item.get("Individual gate hits JSON") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            gates = {}
        if not isinstance(gates, dict):
            continue
        for gate, count in gates.items():
            if not isinstance(gate, str) or not gate:
                continue
            try:
                hits = int(count)
            except (ValueError, TypeError, OverflowError):
                continue
            if hits <= 0:
                continue
            totals[gate] += hits
            stocks[gate].add(ticker)
    output = []
    for gate, count in totals.most_common():
        route, _, reason = gate.partition(": ")
        output.append({
            "Route": route,
            "Failed condition": reason or gate,
            "Signal sessions affected": count,
            "% of all candidate sessions": round(
                count * 100 / total_sessions, 2,
            ) if total_sessions else 0,
            "Stocks affected": len(stocks[gate]),
        })
    return pd.DataFrame(output, columns=[
        "Route", "Failed condition", "Signal sessions affected",
        "% of all candidate sessions", "Stocks affected",
    ])


def breakout_plan_research(diagnostics):
    """Historical diagnostic observations, not an alternative trading strategy.

    Uses only completed signal-date bars. Records separate stop/R:R failures
    and co-occurring vetoes without adjusting any actual entry conditions.
    """
    columns = [
        "Ticker", "Signal date", "Technical state", "Projected R:R",
        "Projected stop %", "Projected upside %", "Technical score",
        "Reference entry", "Reference stop", "Reference target", "Resistance",
        "Support stop", "Retest stop",
        "Other gates failed", "R:R failed", "Stop-distance failed",
        "Resistance-test failed", "All technical gates passed",
    ]
    rows = []
    if isinstance(diagnostics, pd.DataFrame) and not diagnostics.empty:
        for _, stock in diagnostics.iterrows():
            try:
                events = json.loads(str(stock.get("Breakout plan observations JSON") or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                events = []
            if isinstance(events, list):
                rows.extend(
                    {key: item.get(key) for key in columns}
                    for item in events if isinstance(item, dict)
                )
    return pd.DataFrame(rows, columns=columns)


def simulate_shadow_breakout_stops(histories, details, holding_sessions=20,
                                   friction_pct=0.5):
    """RESEARCH: same 24-like plan-only setups, fixed stop alternatives.

    The stop levels originate strictly from each signal's completed historical
    bars. Forward prices influence OUTCOMES only, never the candidates/plans.
    These are independent hypothetical entries, not portfolio fills or BUY.
    """
    columns = [
        "Ticker", "Signal date", "Entry date", "Stop method",
        "Original R:R", "Signal-day structural stop %",
        "Entry ask proxy", "Stop", "Target", "Net entry R:R",
        "Status", "Exit date", "Exit reason", "Realized R",
    ]
    if not 2 <= holding_sessions <= 90 or not 0.5 <= friction_pct <= 5:
        raise ValueError("Shadow assumptions outside supported safety range")
    if details is None or details.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    half_cost = friction_pct / 200.0
    variants = (
        ("APPROVED ORIGINAL (control)", "Reference stop"),
        ("RECENT SUPPORT (research)", "Support stop"),
        ("RESISTANCE RETEST (research)", "Retest stop"),
    )
    for _, event in details.iterrows():
        if int(event.get("Other gates failed", 999)) != 0:
            continue
        if not (bool(event.get("R:R failed")) or bool(event.get("Stop-distance failed"))):
            continue
        score = _finite(event.get("Technical score"))
        if not math.isfinite(score) or score < 75:
            continue
        ticker = str(event.get("Ticker") or "")
        prices = histories.get(ticker)
        signal_date = pd.Timestamp(event["Signal date"])
        ref = _finite(event.get("Reference entry"))
        target = _finite(event.get("Reference target"))
        resistance = _finite(event.get("Resistance"))
        if prices is None or prices.empty:
            continue
        prices = prices.sort_index()
        # YFinance daily indexes can be NY-localized or timezone-naive.
        # Match the US-session calendar DATE, not incompatible timestamps.
        locations = [
            i for i, date_value in enumerate(prices.index)
            if pd.Timestamp(date_value).date() == signal_date.date()
        ]
        if len(locations) != 1 or locations[0]+1 >= len(prices):
            continue
        start = locations[0] + 1
        entry_date = pd.Timestamp(prices.index[start])
        raw_open = _finite(prices.iloc[start]["Open"])
        for name, stop_key in variants:
            stop = _finite(event.get(stop_key))
            row = {
                "Ticker": ticker, "Signal date": str(signal_date.date()),
                "Entry date": str(entry_date.date()), "Stop method": name,
                "Original R:R": _finite(event.get("Projected R:R")),
                "Signal-day structural stop %": round(
                    (ref - stop)/ref * 100, 3,
                ) if ref > 0 and math.isfinite(stop) else math.nan,
                "Entry ask proxy": round(raw_open*(1+half_cost), 4)
                                   if math.isfinite(raw_open) else math.nan,
                "Stop": stop, "Target": target,
                "Net entry R:R": math.nan, "Status": "",
                "Exit date": "", "Exit reason": "", "Realized R": math.nan,
            }
            if not all(math.isfinite(v) for v in
                       (ref, target, resistance, raw_open, stop)) or ref <= 0 or raw_open <= 0:
                row["Status"] = "INVALID HISTORICAL PLAN"
            elif not stop < raw_open < target:
                row["Status"] = "INVALID PRICE / STOP / TARGET"
            elif raw_open > ref * 1.01:
                row["Status"] = "ENTRY GAP >1% ABOVE SIGNAL CLOSE"
            elif raw_open > resistance * 1.02:
                row["Status"] = "ENTRY >2% ABOVE RESISTANCE"
            elif raw_open < resistance * 0.998:
                row["Status"] = "ENTRY RESISTANCE LOST"
            else:
                entry_ask = raw_open * (1+half_cost)
                stop_bid = stop * (1-half_cost)
                target_bid = target * (1-half_cost)
                unit_risk = entry_ask - stop_bid
                net_rr = (target_bid-entry_ask)/unit_risk if unit_risk > 0 else math.nan
                row["Net entry R:R"] = round(net_rr, 3)
                stop_pct = (raw_open - stop)/raw_open*100
                if not 0.4 <= stop_pct <= 8.0:
                    row["Status"] = "STOP OUTSIDE APPROVED 0.4–8%"
                elif not math.isfinite(net_rr) or net_rr < 2:
                    row["Status"] = "NET REWARD/RISK BELOW 2R"
                else:
                    row["Status"] = "OPEN / INCOMPLETE OUTCOME"
                    forward = prices.iloc[start:start + holding_sessions]
                    for offset, (bar_date, candle) in enumerate(forward.iterrows()):
                        op, hi, lo, close = [_finite(candle[k]) for k in
                                             ("Open", "High", "Low", "Close")]
                        if not all(math.isfinite(v) for v in (op, hi, lo, close)):
                            row["Status"] = "INVALID FORWARD PRICE BAR"
                            break
                        if op <= stop:
                            exit_bid, why = op*(1-half_cost), "STOP GAP"
                        elif lo <= stop:
                            why = (
                                "STOP (TARGET ALSO CROSSED)"
                                if hi >= target else "STOP"
                            )
                            exit_bid = stop_bid
                        elif hi >= target:
                            exit_bid, why = target_bid, "TARGET"
                        elif offset == holding_sessions-1:
                            exit_bid, why = close*(1-half_cost), "TIME EXIT"
                        else:
                            continue
                        row["Exit date"] = str(pd.Timestamp(bar_date).date())
                        row["Exit reason"] = why
                        row["Realized R"] = round(
                            (exit_bid - entry_ask)/unit_risk, 3,
                        )
                        row["Status"] = "COMPLETED"
                        break
            rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def summarize_shadow_breakout_stops(outcomes):
    """Group identical independently predeclared stop variants fairly."""
    columns = ["Stop method", "Research observations", "Passed entry proxy",
               "Completed outcomes", "Censored open", "Stops hit",
               "Targets hit", "Wins (completed)", "Mean realized R",
               "Warning"]
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=columns)
    result = []
    for name, chunk in outcomes.groupby("Stop method", sort=False):
        done = chunk[chunk["Status"] == "COMPLETED"]
        open_rows = chunk[chunk["Status"] == "OPEN / INCOMPLETE OUTCOME"]
        eligible = len(done) + len(open_rows)
        rs = pd.to_numeric(done["Realized R"], errors="coerce").dropna()
        result.append({
            "Stop method": name,
            "Research observations": len(chunk),
            "Passed entry proxy": eligible,
            "Completed outcomes": len(done),
            "Censored open": len(open_rows),
            "Stops hit": int(done["Exit reason"].str.startswith("STOP").sum()),
            "Targets hit": int(done["Exit reason"].eq("TARGET").sum()),
            "Wins (completed)": int((rs>0).sum()),
            "Mean realized R": round(float(rs.mean()), 3) if len(rs) else math.nan,
            "Warning": "Research only; no historical earnings or Kraken execution model",
        })
    return pd.DataFrame(result, columns=columns)


def _qualifying_shadow_plan_keys(plan_details, overlap_start):
    """Dates of breakout-plan candidates eligible for shadow-stop *research*."""
    if plan_details is None or plan_details.empty:
        return set()
    data = plan_details.copy()
    dates = pd.to_datetime(data["Signal date"], errors="coerce")
    other = pd.to_numeric(data["Other gates failed"], errors="coerce")
    score = pd.to_numeric(data["Technical score"], errors="coerce")
    qualifies = (
        (dates >= pd.Timestamp(overlap_start))
        & other.eq(0)
        & score.ge(75)
        & (
            data["R:R failed"].fillna(False).astype(bool)
            | data["Stop-distance failed"].fillna(False).astype(bool)
        )
    )
    return set(
        zip(data.loc[qualifies, "Ticker"].astype(str),
            dates.loc[qualifies].dt.strftime("%Y-%m-%d"))
    )


def _compare_candidate_route_traces(full_trace, cropped_trace, overlap_start):
    """Find the earliest stage where equivalent historical candles diverge.

    Compare SAME ticker/dates, and distinguish preliminary trigger mismatch
    from downstream CORE-first routing. This is research-only reporting.
    """
    columns = [
        "Ticker", "Signal date", "Stage", "Full 10y observation",
        "5y-cropped observation",
    ]
    def comparable(records):
        if not records:
            return {}
        result = {}
        start_date = pd.Timestamp(overlap_start).date()
        for item in records:
            try:
                date = pd.Timestamp(item["Signal date"]).date()
                if date < start_date:
                    continue
                key = (str(item["Ticker"]), date.isoformat())
                result[key] = item
            except (TypeError, KeyError, ValueError):
                continue
        return result

    full = comparable(full_trace)
    cropped = comparable(cropped_trace)
    differences = []
    for symbol, session in sorted(set(full) | set(cropped)):
        older = full.get((symbol, session))
        shorter = cropped.get((symbol, session))
        if older is None or shorter is None:
            differences.append({
                "Ticker": symbol,
                "Signal date": session,
                "Stage": "PREFILTER TRIGGER PRESENCE",
                "Full 10y observation": "ABSENT" if older is None else "PRESENT",
                "5y-cropped observation": "ABSENT" if shorter is None else "PRESENT",
            })
            continue
        for field, name in (
            ("Breakout trigger", "BREAKOUT PREFILTER"),
            ("Core trigger", "CORE PREFILTER"),
            ("Core state", "CORE ROUTING STATE"),
        ):
            if older.get(field) != shorter.get(field):
                differences.append({
                    "Ticker": symbol, "Signal date": session,
                    "Stage": name,
                    "Full 10y observation": str(older.get(field)),
                    "5y-cropped observation": str(shorter.get(field)),
                })

    def count(table, field, expected=True):
        return sum(item.get(field) == expected for item in table.values())
    def routed(table):
        return sum(
            item.get("Breakout trigger") is True
            and item.get("Core state") == "BLOCKED"
            for item in table.values()
        )
    def bypassed(table):
        return sum(
            item.get("Breakout trigger") is True
            and item.get("Core state") != "BLOCKED"
            for item in table.values()
        )
    summary = [
        ("Preliminary candidate sessions — full history", len(full)),
        ("Preliminary candidate sessions — cropped history", len(cropped)),
        ("Breakout triggers — full history", count(full, "Breakout trigger")),
        ("Breakout triggers — cropped history", count(cropped, "Breakout trigger")),
        ("Breakouts reaching breakout engine — full history", routed(full)),
        ("Breakouts reaching breakout engine — cropped history", routed(cropped)),
        ("Breakouts bypassed due to CORE priority — full history", bypassed(full)),
        ("Breakouts bypassed due to CORE priority — cropped history", bypassed(cropped)),
        ("Prefilter trigger presence disagreements", sum(
            row["Stage"] == "PREFILTER TRIGGER PRESENCE" for row in differences
        )),
        ("Breakout prefilter disagreements", sum(
            row["Stage"] == "BREAKOUT PREFILTER" for row in differences
        )),
        ("Core route state disagreements", sum(
            row["Stage"] == "CORE ROUTING STATE" for row in differences
        )),
    ]
    return {
        "counts": pd.DataFrame(summary, columns=["Check", "Value"]),
        "differences": pd.DataFrame(differences, columns=columns),
    }


def audit_history_window_consistency(histories, benchmark, full_diagnostics,
                                     last_five_years_start, full_audit_trace=None):
    """Compare full history with a 5y crop OF THE SAME DOWNLOADED CANDLES.

    A direct invariant check on overlapping signal dates only, after allowing
    1 calendar year of indicator warm-up in the shorter series. The comparison
    cannot diagnose differences between two separately downloaded Yahoo batches
    and deliberately never affects the production/portfolio trade decisions.
    """
    cutoff = pd.Timestamp(last_five_years_start)
    if cutoff.tzinfo is not None:
        cutoff = cutoff.tz_localize(None)
    cutoff = cutoff.normalize()
    overlap_start = cutoff + pd.DateOffset(years=1)

    def recent_sessions(frame):
        if frame is None or frame.empty:
            return pd.DataFrame()
        keep = [
            pd.Timestamp(item).date() >= cutoff.date()
            for item in frame.index
        ]
        return frame.loc[keep].copy()

    cropped_histories = {
        ticker: recent_sessions(data)
        for ticker, data in histories.items()
        if data is not None and not data.empty
    }
    cropped_benchmark = recent_sessions(benchmark)
    if cropped_benchmark.empty or "Close" not in cropped_benchmark:
        raise ValueError("Cannot audit overlapping windows without benchmark history")
    shorter_trace = [] if full_audit_trace is not None else None
    shorter_signals, shorter_diagnostics = signal_candidates(
        cropped_histories, cropped_benchmark, first_date=overlap_start,
        audit_trace=shorter_trace,
    )
    stage_comparison = (
        _compare_candidate_route_traces(full_audit_trace, shorter_trace, overlap_start)
        if full_audit_trace is not None else None
    )
    original_plans = breakout_plan_research(full_diagnostics)
    shorter_plans = breakout_plan_research(shorter_diagnostics)
    original_keys = _qualifying_shadow_plan_keys(original_plans, overlap_start)
    trimmed_keys = _qualifying_shadow_plan_keys(shorter_plans, overlap_start)
    all_original = set(zip(
        original_plans.loc[
            pd.to_datetime(original_plans.get("Signal date"), errors="coerce") >= overlap_start,
            "Ticker"
        ].astype(str),
        original_plans.loc[
            pd.to_datetime(original_plans.get("Signal date"), errors="coerce") >= overlap_start,
            "Signal date"
        ].astype(str),
    )) if not original_plans.empty else set()
    all_trimmed = set(zip(
        shorter_plans["Ticker"].astype(str),
        shorter_plans["Signal date"].astype(str),
    )) if not shorter_plans.empty else set()

    original_by_stock = (
        full_diagnostics.set_index("Ticker")
        if isinstance(full_diagnostics, pd.DataFrame)
        and not full_diagnostics.empty and "Ticker" in full_diagnostics
        else pd.DataFrame()
    )
    cropped_by_stock = (
        shorter_diagnostics.set_index("Ticker")
        if not shorter_diagnostics.empty else pd.DataFrame()
    )
    coverage = []
    for ticker, data in sorted(histories.items()):
        if data is None or data.empty:
            continue
        recent = cropped_histories.get(ticker, pd.DataFrame())
        first_bar = pd.Timestamp(data.index[0]).date()
        last_bar = pd.Timestamp(data.index[-1]).date()
        original_qual = len([key for key in original_keys if key[0] == ticker])
        trimmed_qual = len([key for key in trimmed_keys if key[0] == ticker])
        orig_count = int(
            original_by_stock.loc[ticker, "Candidate sessions"]
        ) if ticker in original_by_stock.index else 0
        cropped_count = int(
            cropped_by_stock.loc[ticker, "Candidate sessions"]
        ) if ticker in cropped_by_stock.index else 0
        coverage.append({
            "Ticker": ticker,
            "Actual first daily bar": str(first_bar),
            "Actual last daily bar": str(last_bar),
            "Full download bars": len(data),
            "Bars in last 5y crop": len(recent),
            "Full-history preliminary sessions": orig_count,
            "5y-cropped preliminary sessions": cropped_count,
            "Full-history eligible plans (same overlapping window)": original_qual,
            "Cropped-history eligible plans": trimmed_qual,
            "Usable overlapping history": len(recent) >= 252,
        })

    mismatches = [
        {"Ticker": ticker, "Signal date": date,
         "Appears in": "FULL HISTORY ONLY"}
        for ticker, date in sorted(original_keys - trimmed_keys)
    ] + [
        {"Ticker": ticker, "Signal date": date,
         "Appears in": "5-YEAR CROP ONLY"}
        for ticker, date in sorted(trimmed_keys - original_keys)
    ]
    coverage_frame = pd.DataFrame(coverage)
    total_tickers = len(coverage_frame)
    earliest = (
        min(coverage_frame["Actual first daily bar"])
        if total_tickers else "UNAVAILABLE"
    )
    summary = pd.DataFrame([
            {
                "Check": "Earliest historical daily candle available",
                "Value": earliest,
            },
            {
                "Check": "Stocks with downloaded daily bars",
                "Value": total_tickers,
            },
            {
                "Check": "Stocks with >=252 bars in the overlapping 5y crop",
                "Value": int(coverage_frame["Usable overlapping history"].sum())
                         if total_tickers else 0,
            },
            {
                "Check": "Comparable-window start (warm-up excluded)",
                "Value": str(overlap_start.date()),
            },
            {
                "Check": "Full-history breakout evaluations in comparable window",
                "Value": len(all_original),
            },
            {
                "Check": "Five-year-cropped breakout evaluations",
                "Value": len(all_trimmed),
            },
            {
                "Check": "Full-history eligible shadow plan-only setups",
                "Value": len(original_keys),
            },
            {
                "Check": "Five-year-cropped eligible shadow plan-only setups",
                "Value": len(trimmed_keys),
            },
            {
                "Check": "Eligible setup date disagreements",
                "Value": len(mismatches),
            },
            {
                "Check": "WARNING",
                "Value": (
                    "Overlapping history gives different candidate dates; "
                    "do not use as validation without reviewing differences."
                    if mismatches else
                    "Eligible dates agree on the SAME downloaded candles; "
                    "this does not confirm separately downloaded 5y files match."
                ),
            },
        ])
    if stage_comparison is not None:
        summary = pd.concat([summary, stage_comparison["counts"]], ignore_index=True)
    return {
        "summary": summary,
        "stage_mismatches": (
            stage_comparison["differences"]
            if stage_comparison is not None else pd.DataFrame()
        ),
        "coverage": coverage_frame,
        "disagreements": pd.DataFrame(
            mismatches, columns=["Ticker", "Signal date", "Appears in"]
        ),
        "trimmed_diagnostics": shorter_diagnostics,
        "trimmed_signals": len(shorter_signals),
    }


def summarize_shadow_robustness(scenarios):
    """Descriptive, retrospective robustness cohorts for shadow-stop experiments.

    Scenarios map a descriptive cost label to the corresponding shadow stop
    outcome DataFrame. Every scenario is evaluated independently using that
    scenario's assumed costs and resulting entry approvals. Never treats
    hypothetical R as real funded P&L or an unseen validation sample.
    """
    columns = [
        "Cost scenario", "Historical cohort", "Stop method",
        "Observed plans", "Passed next-open entry", "Entry rejections",
        "Completed trades", "Censored trades", "Wins", "Stop exits",
        "Target exits", "Time exits", "Win rate % (completed)",
        "Mean R (completed)", "Sum R (independent trades)",
        "Statistical confidence",
    ]
    if not isinstance(scenarios, dict) or not scenarios:
        return pd.DataFrame(columns=columns)

    result = []
    for label, frame in scenarios.items():
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        required = {"Entry date", "Stop method", "Status", "Exit reason", "Realized R"}
        if not required.issubset(frame.columns):
            continue
        data = frame.copy()
        dates = pd.to_datetime(data["Entry date"], errors="coerce")
        data = data.loc[dates.notna()].copy()
        if data.empty:
            continue
        years = dates.loc[dates.notna()].dt.year
        data["_cohort"] = years.map(
            lambda year: (
                "Before 2020" if year < 2020
                else "2020–2022" if year <= 2022
                else "2023–2024" if year <= 2024
                else "2025 onward"
            )
        )
        for cohort_name, group in (
            [("All observed years", data)]
            + list(data.groupby("_cohort", sort=True))
        ):
            for method, chunk in group.groupby("Stop method", sort=False):
                done = chunk.loc[chunk["Status"].eq("COMPLETED")]
                censored = chunk.loc[chunk["Status"].eq("OPEN / INCOMPLETE OUTCOME")]
                passed = len(done) + len(censored)
                realized = pd.to_numeric(done["Realized R"], errors="coerce").dropna()
                wins = int((realized > 0).sum())
                n = len(realized)
                result.append({
                    "Cost scenario": str(label),
                    "Historical cohort": cohort_name,
                    "Stop method": method,
                    "Observed plans": len(chunk),
                    "Passed next-open entry": passed,
                    "Entry rejections": len(chunk) - passed,
                    "Completed trades": len(done),
                    "Censored trades": len(censored),
                    "Wins": wins,
                    "Stop exits": int(done["Exit reason"].fillna("").str.startswith("STOP").sum()),
                    "Target exits": int(done["Exit reason"].eq("TARGET").sum()),
                    "Time exits": int(done["Exit reason"].eq("TIME EXIT").sum()),
                    "Win rate % (completed)": round(100 * wins / n, 2) if n else math.nan,
                    "Mean R (completed)": round(float(realized.mean()), 3) if n else math.nan,
                    "Sum R (independent trades)": round(float(realized.sum()), 3) if n else math.nan,
                    "Statistical confidence": (
                        "NO EXECUTED PROXY TRADES" if n == 0
                        else "SMALL SAMPLE — INCONCLUSIVE" if n < 30
                        else "RETROSPECTIVE — NOT INDEPENDENT VALIDATION"
                    ),
                })
    return pd.DataFrame(result, columns=columns)


def summarize_breakout_plan_research(details):
    """Summarize veto co-occurrence; never infer improved profitability."""
    if details is None or details.empty:
        return pd.DataFrame(columns=["Measure", "Value", "Interpretation"])
    frame = details.copy()
    rr_fail = frame["R:R failed"].fillna(False).astype(bool)
    stop_fail = frame["Stop-distance failed"].fillna(False).astype(bool)
    other = pd.to_numeric(frame["Other gates failed"], errors="coerce").fillna(999)
    stopped = pd.to_numeric(frame["Projected stop %"], errors="coerce")
    reward = pd.to_numeric(frame["Projected R:R"], errors="coerce")
    upside = pd.to_numeric(frame["Projected upside %"], errors="coerce")
    def median_or_none(series):
        values = series.replace([np.inf, -np.inf], np.nan).dropna()
        return round(float(values.median()), 2) if len(values) else "N/A"
    observations = [
        ("Breakout plans evaluated", len(frame),
         "Completed-bar breakout checks actually evaluated; may exclude core-prioritised events"),
        ("R:R failed with no other gates failing", int((rr_fail & ~stop_fail & (other == 0)).sum()),
         "Would meet all other technical gates except projected R:R"),
        ("Stop-distance failed with no other gates failing", int((~rr_fail & stop_fail & (other == 0)).sum()),
         "Would meet all other technical gates except stop-distance"),
        ("Only R:R and/or stop-distance failed", int(((rr_fail | stop_fail) & (other == 0)).sum()),
         "Hypothetical research pool before quote, earnings or broker risk checks"),
        ("R:R failed alongside additional non-plan gates", int((rr_fail & (other > 0)).sum()),
         "Removing the projected R:R veto alone would not qualify these"),
        ("Stop wider than 8%", int((stopped > 8).sum()),
         "Wide structural stops (risk constraint)"),
        ("Stop tighter than 0.4%", int((stopped < 0.4).sum()),
         "Stops too close to entry"),
        ("Median projected R:R", median_or_none(reward),
         "Statistical description only; not a trade recommendation"),
        ("Median projected stop %", median_or_none(stopped),
         "Structural stop distance"),
        ("Median projected upside %", median_or_none(upside),
         "Measured-move target projection before costs"),
    ]
    return pd.DataFrame(observations, columns=["Measure", "Value", "Interpretation"])


def summarize_backtest_funnel(diagnostics, result):
    """Make a one-row scenario summary self-explanatory when trades are zero."""
    metrics = dict(result["metrics"])
    if isinstance(diagnostics, pd.DataFrame) and not diagnostics.empty:
        metric_fields = (
            "Candidate sessions", "Core trigger sessions", "Breakout trigger sessions",
            "Full-rule rejections", "Liquidity rejections",
            "Qualified core signals", "Qualified breakout signals",
            "Breakouts bypassed due to core WATCH",
        )
        for name in metric_fields:
            metrics[name] = int(
                pd.to_numeric(diagnostics.get(name, pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
            )
        statuses = diagnostics.get("Status", pd.Series(dtype=str))
        metrics["Stocks with usable history"] = int(statuses.eq("SCANNED").sum())
        blockers = diagnostics.get("Top rule blocker", pd.Series(dtype=str))
        blockers = blockers.fillna("").astype(str)
        blocker_counts = Counter(blockers[blockers.ne("")])
        metrics["Example full-rule blocker"] = (
            blocker_counts.most_common(1)[0][0] if blocker_counts else ""
        )
    skips = result["skipped"]
    if not skips.empty:
        reasons = Counter(skips["Reason"].dropna().astype(str))
        metrics["Most common skipped entry reason"] = reasons.most_common(1)[0][0]
        metrics["Skipped candidate detail"] = "; ".join(
            f"{row['Ticker']} {row['Date']} ({row['Route']}): {row['Reason']}"
            for _, row in skips.head(3).iterrows()
        )
    else:
        metrics["Most common skipped entry reason"] = ""
        metrics["Skipped candidate detail"] = ""
    metrics["Statistically evaluable"] = bool(metrics["Completed trades"] >= 30)
    return metrics


def _entry_plan(candidate, raw_open, roundtrip_cost_pct):
    """Next US open is a *proxy*, not a guaranteed Kraken Funded quote."""
    plan = candidate["Plan"]
    stop, target = _finite(plan.get("reference_stop")), _finite(plan.get("reference_target"))
    ref = _finite(plan.get("reference_entry"))
    if not all(math.isfinite(x) for x in (raw_open, stop, target, ref)) or raw_open <= 0:
        return None, "INVALID NEXT OPEN/PLAN"
    if candidate["Route"] == "FUNDED BREAKOUT":
        checked = validate_breakout_quote(plan, raw_open)
        if not checked.get("ok"):
            return None, str(checked.get("reason"))
    else:
        atr, zone = _finite(plan.get("atr")), _finite(plan.get("zone_top"))
        if not math.isfinite(atr) or atr <= 0 or not math.isfinite(zone):
            return None, "CORE ATR/ZONE UNAVAILABLE"
        if abs(raw_open - ref) > 0.5 * atr or raw_open > zone + atr:
            return None, "CORE GAP / EXTENSION"
        if raw_open <= stop or raw_open > ref * 1.01 or raw_open < ref * 0.98:
            return None, "CORE QUOTE TOO FAR FROM SIGNAL"
        if (raw_open - stop) / raw_open > 0.10:
            return None, "CORE STOP TOO WIDE"
        if (target - raw_open) / raw_open < 0.10:
            return None, "CORE UPSIDE BELOW 10%"

    # Model a combined half-spread/slippage both on entry and exit. A larger
    # configurable roundtrip cost is used for stress tests. This is NOT a
    # reconstruction of unavailable historical Kraken bid/ask quotes.
    half_cost = roundtrip_cost_pct / 200.0
    entry_ask = raw_open * (1 + half_cost)
    stop_bid = stop * (1 - half_cost)
    target_bid = target * (1 - half_cost)
    risk_unit = entry_ask - stop_bid
    net_reward = target_bid - entry_ask
    if risk_unit <= 0 or net_reward <= 0:
        return None, "NET PLAN INVALID"
    rr = net_reward / risk_unit
    if rr < 2.0:
        return None, "NET REWARD/RISK BELOW 2"
    stop_pct = (entry_ask - stop_bid) / entry_ask * 100
    if candidate["Route"] == "FUNDED BREAKOUT" and not 0.4 <= stop_pct <= 8.5:
        return None, "BREAKOUT NET STOP TOO WIDE"
    risk_usd = 4.0 if rr >= 2.5 and candidate["Score"] >= 75 else 3.0
    return {
        "entry_ask": entry_ask, "stop": stop, "target": target,
        "stop_bid": stop_bid, "target_bid": target_bid, "net_risk_per_share": risk_unit,
        "reference_rr": rr, "planned_risk": risk_usd,
        "half_cost": half_cost,
    }, "PASS"


def _exit_on_bar(position, bar, is_last_session, holding_sessions):
    """Same-bar stop+target chooses STOP. Downward stop gaps fill at OPEN."""
    op, hi, lo, close = (float(bar[k]) for k in ("Open", "High", "Low", "Close"))
    stop, target = position["stop"], position["target"]
    half_cost = position["half_cost"]
    if op <= stop:
        return op * (1 - half_cost), "STOP GAP"
    if lo <= stop:
        # Report ambiguity ONLY if daily HIGH also touched the target.
        why = "STOP (TARGET ALSO CROSSED)" if hi >= target else "STOP"
        return stop * (1 - half_cost), why
    if hi >= target:
        return target * (1 - half_cost), "TARGET"
    if holding_sessions >= position["max_hold"]:
        return close * (1 - half_cost), "TIME EXIT"
    if is_last_session:
        return None, "OPEN AT DATA END"
    return None, "HOLD"


def simulate_portfolio(histories, signals, roundtrip_cost_pct=0.5,
                       max_hold_sessions=20, start_balance=START_BALANCE):
    """Chronological position/risk simulation: excludes manual event gate.

    Entry at next day OPEN proxy, priced adversely. Candidate entry decisions
    precede same-day exits so future intraday closures do not free entry risk.
    """
    if not 0.5 <= roundtrip_cost_pct <= 5:
        raise ValueError("Roundtrip friction must be between 0.5% and 5%")
    if not 2 <= max_hold_sessions <= 90:
        raise ValueError("Maximum holding sessions out of permitted range")
    ordered = sorted(signals, key=lambda s: (s["Entry date"], -s["Score"], s["Ticker"]))
    by_entry = defaultdict(list)
    for signal in ordered:
        by_entry[pd.Timestamp(signal["Entry date"])].append(signal)
    closes = {}
    for symbol, frame in histories.items():
        if frame is None or frame.empty:
            continue
        closes[symbol] = frame.sort_index()
    days = sorted(set(day for df in closes.values() for day in df.index))
    holdings = {}
    ledger, skips, equity_points = [], [], []
    balance = float(start_balance)
    failed = passed = False
    peak = float(start_balance)
    max_drawdown = 0.0
    for day in days:
        # Evaluate the known OPEN prices, while old positions still count
        # against both the planned risk limit and total notional allowance.
        for candidate in by_entry.get(day, []):
            sym = candidate["Ticker"]
            if failed or passed:
                reason = "CHALLENGE HALTED"
            elif sym in holdings:
                reason = "SYMBOL ALREADY OPEN"
            elif sym not in closes or day not in closes[sym].index:
                reason = "MISSING EXECUTION BAR"
            else:
                op = _finite(closes[sym].loc[day, "Open"])
                plan, reason = _entry_plan(candidate, op, roundtrip_cost_pct)
                if plan is not None:
                    open_risk = sum(x["risk_reserved"] for x in holdings.values())
                    open_notional = sum(x["notional"] for x in holdings.values())
                    allowed_risk = min(
                        plan["planned_risk"],
                        MAX_COMBINED_PLANNED_RISK - open_risk,
                        balance - FAIL_BALANCE - RISK_BUFFER - open_risk,
                    )
                    if allowed_risk <= 0:
                        reason = "FUNDED RISK BUFFER"
                    else:
                        max_notional = min(
                            balance * MAX_TICKER_NOTIONAL_PCT,
                            balance * MAX_TOTAL_NOTIONAL_PCT - open_notional,
                        )
                        notional = min(
                            max_notional,
                            allowed_risk * plan["entry_ask"] / plan["net_risk_per_share"],
                        )
                        if notional <= 0:
                            reason = "NO POSITION CAPACITY"
                        else:
                            units = notional / plan["entry_ask"]
                            risk = units * plan["net_risk_per_share"]
                            holdings[sym] = {
                                **plan, "Ticker": sym,
                                "Route": candidate["Route"],
                                "Score": candidate["Score"],
                                "Signal date": candidate["Signal date"],
                                "Entry date": day,
                                "units": units, "notional": notional,
                                "risk_reserved": risk, "max_hold": max_hold_sessions,
                                "bars_held": 0,
                            }
                            reason = "ENTERED"
            if reason != "ENTERED":
                skips.append({
                    "Ticker": sym, "Date": str(day.date()),
                    "Route": candidate["Route"], "Reason": reason,
                })

        for sym, trade in list(holdings.items()):
            frame = closes[sym]
            if day not in frame.index or day < trade["Entry date"]:
                continue
            trade["bars_held"] += 1
            exit_price, reason = _exit_on_bar(
                trade, frame.loc[day], day == frame.index[-1], trade["bars_held"],
            )
            if exit_price is None:
                continue
            pnl = trade["units"] * (exit_price - trade["entry_ask"])
            balance += pnl
            ledger.append({
                "Ticker": sym, "Route": trade["Route"],
                "Signal date": str(trade["Signal date"].date()),
                "Entry date": str(trade["Entry date"].date()),
                "Exit date": str(day.date()), "Exit reason": reason,
                "Entry ask proxy": round(trade["entry_ask"], 4),
                "Stop": round(trade["stop"], 4),
                "Target": round(trade["target"], 4),
                "Exit bid proxy": round(exit_price, 4),
                "Notional $": round(trade["notional"], 2),
                "Modeled initial risk $": round(trade["risk_reserved"], 2),
                "P&L $": round(pnl, 3),
                "Realized R": round(pnl / trade["risk_reserved"], 3)
                             if trade["risk_reserved"] > 0 else math.nan,
                "Holding sessions": trade["bars_held"],
            })
            del holdings[sym]

        # Mark remaining open trades to the daily close, priced adversely.
        floating = sum(
            trade["units"] * (
                float(closes[sym].loc[:day]["Close"].iloc[-1])
                * (1 - trade["half_cost"]) - trade["entry_ask"]
            )
            for sym, trade in holdings.items() if len(closes[sym].loc[:day])
        )
        equity = balance + floating
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak * 100 if peak > 0 else 0)
        equity_points.append({
            "Date": str(day.date()), "Balance $": round(balance, 3),
            "Equity $": round(equity, 3), "Open positions": len(holdings),
        })
        if equity <= FAIL_BALANCE:
            failed = True
        elif balance >= PASS_BALANCE and not holdings:
            passed = True

    # Final-day open holdings are CENSORED, NOT treated as realized trades.
    unclosed = []
    for sym, trade in holdings.items():
        unclosed.append({
            "Ticker": sym, "Route": trade["Route"],
            "Entry date": str(trade["Entry date"].date()),
            "Notional $": round(trade["notional"], 2),
            "Modeled initial risk $": round(trade["risk_reserved"], 2),
            "Reason": "OPEN / OUTCOME NOT OBSERVED",
        })
    trades = pd.DataFrame(ledger)
    skipped = pd.DataFrame(skips)
    curve = pd.DataFrame(equity_points)
    finished = len(trades)
    wins = int((trades["P&L $"] > 0).sum()) if finished else 0
    gross_profit = float(trades["P&L $"].clip(lower=0).sum()) if finished else 0
    gross_loss = float(-trades["P&L $"].clip(upper=0).sum()) if finished else 0
    return {
        "trades": trades, "skipped": skipped, "equity_curve": curve,
        "unclosed": pd.DataFrame(unclosed),
        "metrics": {
            "Candidates": len(signals),
            "Completed trades": finished,
            "Open / censored": len(unclosed),
            "Skipped": len(skipped),
            "Win rate %": round(100 * wins / finished, 2) if finished else math.nan,
            "Mean R": round(float(trades["Realized R"].mean()), 3) if finished else math.nan,
            "Profit factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else math.nan,
            "Realized P&L $": round(balance - start_balance, 2),
            "Ending marked equity $": round(float(curve["Equity $"].iloc[-1]), 2)
                                      if len(curve) else float(start_balance),
            "Max daily equity drawdown %": round(max_drawdown, 2),
            "Touched $970 fail line": bool(failed),
            "Reached $1120 pass balance": bool(passed),
            "Modeled roundtrip friction %": round(roundtrip_cost_pct, 2),
            "Holding limit sessions": max_hold_sessions,
            "Historical earnings blackout": "NOT RECONSTRUCTED",
            "Kraken 24/7 spreads / fills": "NOT RECONSTRUCTED",
        },
    }
