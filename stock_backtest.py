from __future__ import annotations

import pandas as pd
import yfinance as yf


def run_trade_backtest(symbol, technical_func, thresholds=(80, 85, 90), horizon=20, cooldown=10, min_rr=0.0, zone_mode="either"):
    df = yf.Ticker(symbol).history(period="5y", interval="1d", auto_adjust=False)
    if df is None or len(df) < 300:
        return pd.DataFrame(), pd.DataFrame()

    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    events = []
    last_signal = {int(t): -9999 for t in thresholds}
    start = 220

    for i in range(start, len(df) - horizon - 1):
        hist = df.iloc[: i + 1].copy()
        tech = technical_func(hist)
        if not tech:
            continue

        entry = float(tech["price"])
        invalid = float(tech["invalidation"])
        in_preferred = bool(tech.get("in_preferred_zone"))
        in_strong = bool(tech.get("in_strong_zone"))
        if zone_mode == "preferred":
            entry_ok = in_preferred
        elif zone_mode == "strong":
            entry_ok = in_strong
        else:
            entry_ok = bool(in_preferred or in_strong)
        future = df.iloc[i + 1 : i + 1 + horizon]
        if future.empty:
            continue

        max_ret = (float(future["High"].max()) / entry - 1) * 100
        min_ret = (float(future["Low"].min()) / entry - 1) * 100
        close_ret = (float(future["Close"].iloc[-1]) / entry - 1) * 100

        def hit_before_stop(target_pct):
            target = entry * (1 + target_pct / 100)
            for _, row in future.iterrows():
                if float(row["Low"]) <= invalid:
                    return False
                if float(row["High"]) >= target:
                    return True
            return False

        invalid_hit = bool((future["Low"] <= invalid).any())

        for threshold in thresholds:
            threshold = int(threshold)
            if tech["trade_score"] < threshold:
                continue
            if not entry_ok:
                continue
            if float(tech.get("rr", 0)) < float(min_rr):
                continue
            if i - last_signal[threshold] < cooldown:
                continue

            last_signal[threshold] = i
            events.append({
                "Threshold": threshold,
                "Date": df.index[i],
                "Score": float(tech["trade_score"]),
                "Entry": entry,
                "R:R": float(tech.get("rr", 0)),
                "Zone": "Strong" if in_strong else "Preferred",
                "Max return %": max_ret,
                "Close return %": close_ret,
                "Max adverse %": min_ret,
                "Hit +5% before stop": hit_before_stop(5),
                "Hit +10% before stop": hit_before_stop(10),
                "Hit +20% before stop": hit_before_stop(20),
                "Invalidation hit": invalid_hit,
            })

    ev = pd.DataFrame(events)
    if ev.empty:
        return ev, pd.DataFrame()

    summary = []
    for threshold in thresholds:
        g = ev[ev["Threshold"] == int(threshold)]
        if g.empty:
            continue
        summary.append({
            "Threshold": int(threshold),
            "Signals": len(g),
            "Hit +5%": round(g["Hit +5% before stop"].mean() * 100, 1),
            "Hit +10%": round(g["Hit +10% before stop"].mean() * 100, 1),
            "Hit +20%": round(g["Hit +20% before stop"].mean() * 100, 1),
            "Invalidation hit": round(g["Invalidation hit"].mean() * 100, 1),
            "Median max return %": round(g["Max return %"].median(), 1),
            "Median close return %": round(g["Close return %"].median(), 1),
            "Median adverse %": round(g["Max adverse %"].median(), 1),
        })

    return ev, pd.DataFrame(summary)


def run_basket_backtest(symbols, technical_func, thresholds=(80, 85, 90), horizon=20, cooldown=10, min_rr=0.0, zone_mode="either"):
    all_events = []
    per_stock = []

    for symbol in symbols:
        symbol = str(symbol).strip().upper()
        if not symbol:
            continue
        try:
            events, summary = run_trade_backtest(
                symbol,
                technical_func,
                thresholds=thresholds,
                horizon=horizon,
                cooldown=cooldown,
                min_rr=min_rr,
                zone_mode=zone_mode,
            )
        except Exception:
            continue

        if not events.empty:
            events = events.copy()
            events["Ticker"] = symbol
            all_events.append(events)

        if not summary.empty:
            s = summary.copy()
            s["Ticker"] = symbol
            per_stock.append(s)

    if not all_events:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    events = pd.concat(all_events, ignore_index=True)
    stock_summary = pd.concat(per_stock, ignore_index=True) if per_stock else pd.DataFrame()

    aggregate = []
    for threshold in thresholds:
        g = events[events["Threshold"] == int(threshold)]
        if g.empty:
            continue
        aggregate.append({
            "Threshold": int(threshold),
            "Stocks": int(g["Ticker"].nunique()),
            "Signals": len(g),
            "Hit +5%": round(g["Hit +5% before stop"].mean() * 100, 1),
            "Hit +10%": round(g["Hit +10% before stop"].mean() * 100, 1),
            "Hit +20%": round(g["Hit +20% before stop"].mean() * 100, 1),
            "Invalidation hit": round(g["Invalidation hit"].mean() * 100, 1),
            "Median max return %": round(g["Max return %"].median(), 1),
            "Median close return %": round(g["Close return %"].median(), 1),
            "Median adverse %": round(g["Max adverse %"].median(), 1),
        })

    return events, pd.DataFrame(aggregate), stock_summary
