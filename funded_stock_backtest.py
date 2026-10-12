"""Historical research backtest for Kraken Funded STOCKS only.

Never live execution and NEVER Kraken 24/7 broker quote history: Yahoo daily
OHLCV prices are proxies. No historical earnings/official event calendar, so
results are deliberately labelled *TECHNICAL ONLY*, not production-ready.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
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


def signal_candidates(histories, benchmark, first_date=None):
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
                    continue
                plan = {"reference_entry": ref, "reference_stop": stop,
                        "reference_target": target, "atr": atr,
                        "zone_top": zone_top, "signal_date": date}
            elif core.get("technical_state") == "BLOCKED" and bool(breakout_prefilter.iloc[i]):
                brk = evaluate_funded_breakout(past, past_benchmark)
                if brk is None or brk.get("technical_state") != "FUNDED BREAKOUT PENDING OPEN":
                    full_rule_rejections[
                        ("BREAKOUT: " + str(brk.get("technical_reason", "NOT DETECTED")))
                        if brk else "BREAKOUT: NOT DETECTED"
                    ] += 1
                    continue
                score = _finite(brk.get("technical_score"))
                if not math.isfinite(score) or score < 75:
                    full_rule_rejections["BREAKOUT QUALITY SCORE BELOW 75"] += 1
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
                full_rule_rejections[
                    "CORE: " + str(core.get("technical_reason", "NO CONFIRMED SETUP"))
                ] += 1
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
            "Liquidity rejections": liquidity_rejections,
            "Qualified core signals": qualified_core,
            "Qualified breakout signals": qualified_breakout,
            "Completed bars": len(data),
            "First bar": str(data.index[0].date()),
            "Last bar": str(data.index[-1].date()),
        })
    found.sort(key=lambda x: (x["Entry date"], -x["Score"], x["Ticker"]))
    return found, pd.DataFrame(diagnostics)


def summarize_backtest_funnel(diagnostics, result):
    """Make a one-row scenario summary self-explanatory when trades are zero."""
    metrics = dict(result["metrics"])
    if isinstance(diagnostics, pd.DataFrame) and not diagnostics.empty:
        metric_fields = (
            "Candidate sessions", "Core trigger sessions", "Breakout trigger sessions",
            "Full-rule rejections", "Liquidity rejections",
            "Qualified core signals", "Qualified breakout signals",
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
        return stop * (1 - half_cost), "STOP / INTRADAY AMBIGUITY"
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
