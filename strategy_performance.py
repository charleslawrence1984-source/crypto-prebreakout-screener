from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd
import yfinance as yf


EXIT_METHODS = {
    "Technical target": "technical",
    "2R target": "2r",
    "+10% target": "10pct",
    "+15% target": "15pct",
    "SMA20 trend exit": "trend",
}


def _clean_history(symbol: str, period: str = "5y") -> pd.DataFrame:
    df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def _entry_ok(tech: dict, zone_mode: str) -> bool:
    preferred = bool(tech.get("in_preferred_zone"))
    strong = bool(tech.get("in_strong_zone"))
    if zone_mode == "preferred":
        return preferred
    if zone_mode == "strong":
        return strong
    return preferred or strong


def _generate_candidates(
    symbol: str,
    df: pd.DataFrame,
    technical_func,
    threshold: float,
    min_rr: float,
    zone_mode: str,
    cooldown: int,
    max_hold: int,
) -> list[dict]:
    if df.empty or len(df) < 300:
        return []

    out = []
    last_signal = -9999
    start = 220

    for i in range(start, len(df) - max_hold - 1):
        if i - last_signal < cooldown:
            continue

        hist = df.iloc[: i + 1].copy()
        tech = technical_func(hist)
        if not tech:
            continue
        if float(tech.get("trade_score", 0)) < float(threshold):
            continue
        if float(tech.get("rr", 0)) < float(min_rr):
            continue
        if not _entry_ok(tech, zone_mode):
            continue

        entry = float(tech["price"])
        invalid = float(tech["invalidation"])
        risk = max(entry - invalid, 0.01)
        technical_target = float(tech["swing_target"])

        out.append({
            "Ticker": symbol,
            "Entry idx": i,
            "Entry date": df.index[i],
            "Entry": entry,
            "Invalidation": invalid,
            "Risk": risk,
            "Technical target": technical_target,
            "Score": float(tech["trade_score"]),
            "R:R": float(tech.get("rr", 0)),
            "Zone": "Strong" if bool(tech.get("in_strong_zone")) else "Preferred",
        })
        last_signal = i

    return out


def _simulate_one_trade(
    candidate: dict,
    df: pd.DataFrame,
    method: str,
    max_hold: int,
    min_trend_days: int = 5,
) -> dict | None:
    i = int(candidate["Entry idx"])
    entry = float(candidate["Entry"])
    invalid = float(candidate["Invalidation"])
    risk = float(candidate["Risk"])

    if method == "technical":
        target = float(candidate["Technical target"])
    elif method == "2r":
        target = entry + 2 * risk
    elif method == "10pct":
        target = entry * 1.10
    elif method == "15pct":
        target = entry * 1.15
    else:
        target = np.nan

    future = df.iloc[i + 1 : i + 1 + max_hold].copy()
    if future.empty:
        return None

    close = pd.to_numeric(df["Close"], errors="coerce")
    sma20 = close.rolling(20).mean()

    exit_date = future.index[-1]
    exit_price = float(future["Close"].iloc[-1])
    exit_reason = "Max hold"

    below_sma_count = 0

    for j, (dt, row) in enumerate(future.iterrows(), start=1):
        low = float(row["Low"])
        high = float(row["High"])
        close_px = float(row["Close"])

        # Conservative same-day assumption: invalidation is checked before target.
        if low <= invalid:
            exit_date = dt
            exit_price = invalid
            exit_reason = "Invalidation"
            break

        if method != "trend" and not np.isnan(target) and high >= target:
            exit_date = dt
            exit_price = target
            exit_reason = "Target"
            break

        if method == "trend" and j >= min_trend_days:
            sma = float(sma20.loc[dt]) if dt in sma20.index and not pd.isna(sma20.loc[dt]) else np.nan
            if not np.isnan(sma) and close_px < sma:
                below_sma_count += 1
            else:
                below_sma_count = 0
            if below_sma_count >= 2:
                exit_date = dt
                exit_price = close_px
                exit_reason = "Trend break"
                break

    ret = (exit_price / entry - 1) * 100
    hold_days = int(df.index.get_loc(exit_date) - i)

    return {
        **candidate,
        "Exit date": exit_date,
        "Exit": exit_price,
        "Return %": ret,
        "Hold days": hold_days,
        "Exit reason": exit_reason,
        "Method": method,
    }


def _spy_return(spy: pd.DataFrame, entry_date, exit_date) -> float:
    if spy.empty:
        return np.nan
    s = spy.loc[(spy.index >= entry_date) & (spy.index <= exit_date)]
    if len(s) < 2:
        return np.nan
    return (float(s["Close"].iloc[-1]) / float(s["Close"].iloc[0]) - 1) * 100


def _profit_factor(returns: pd.Series) -> float:
    pos = returns[returns > 0].sum()
    neg = abs(returns[returns < 0].sum())
    if neg == 0:
        return float("inf") if pos > 0 else np.nan
    return float(pos / neg)


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return np.nan
    peak = equity.cummax()
    dd = equity / peak - 1
    return float(dd.min() * 100)


def _portfolio_simulation(
    trades: pd.DataFrame,
    histories: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    max_positions: int = 5,
    start_capital: float = 100000.0,
) -> dict:
    if trades.empty or spy.empty:
        return {}

    trades = trades.sort_values(["Entry date", "Score"], ascending=[True, False]).copy()
    start_date = max(trades["Entry date"].min(), spy.index.min())
    end_date = min(trades["Exit date"].max(), spy.index.max())
    calendar = spy.loc[(spy.index >= start_date) & (spy.index <= end_date)].index
    if len(calendar) < 2:
        return {}

    entries = defaultdict(list)
    for _, tr in trades.iterrows():
        entries[pd.Timestamp(tr["Entry date"])].append(tr.to_dict())

    cash = float(start_capital)
    positions = {}
    equity_vals = []
    position_counts = []
    accepted = 0

    for dt in calendar:
        # Close scheduled exits first.
        to_close = []
        for key, pos in positions.items():
            if pd.Timestamp(pos["Exit date"]) <= dt:
                cash += pos["Units"] * float(pos["Exit"])
                to_close.append(key)
        for key in to_close:
            positions.pop(key, None)

        # Mark current equity before allocating new slots.
        holdings_value = 0.0
        for key, pos in positions.items():
            sym = pos["Ticker"]
            h = histories.get(sym, pd.DataFrame())
            if dt in h.index:
                px = float(h.loc[dt, "Close"])
            else:
                prev = h.loc[h.index <= dt]
                px = float(prev["Close"].iloc[-1]) if not prev.empty else float(pos["Entry"])
            holdings_value += pos["Units"] * px

        total_equity = cash + holdings_value
        slot_value = total_equity / max(max_positions, 1)

        # Highest score gets first claim on limited slots.
        todays = sorted(entries.get(pd.Timestamp(dt), []), key=lambda x: x["Score"], reverse=True)
        for tr in todays:
            if len(positions) >= max_positions:
                break
            alloc = min(slot_value, cash)
            if alloc <= 0:
                break
            units = alloc / float(tr["Entry"])
            key = f"{tr['Ticker']}-{tr['Entry date']}"
            positions[key] = {**tr, "Units": units}
            cash -= alloc
            accepted += 1

        # End-of-day marked equity.
        holdings_value = 0.0
        for pos in positions.values():
            sym = pos["Ticker"]
            h = histories.get(sym, pd.DataFrame())
            if dt in h.index:
                px = float(h.loc[dt, "Close"])
            else:
                prev = h.loc[h.index <= dt]
                px = float(prev["Close"].iloc[-1]) if not prev.empty else float(pos["Entry"])
            holdings_value += pos["Units"] * px

        equity_vals.append(cash + holdings_value)
        position_counts.append(len(positions))

    equity = pd.Series(equity_vals, index=calendar, dtype=float)
    total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    years = max((calendar[-1] - calendar[0]).days / 365.25, 1 / 365.25)
    cagr = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100

    spy_slice = spy.loc[(spy.index >= calendar[0]) & (spy.index <= calendar[-1])]
    spy_total = (float(spy_slice["Close"].iloc[-1]) / float(spy_slice["Close"].iloc[0]) - 1) * 100
    spy_cagr = ((float(spy_slice["Close"].iloc[-1]) / float(spy_slice["Close"].iloc[0])) ** (1 / years) - 1) * 100

    exposure = float(np.mean(position_counts) / max(max_positions, 1) * 100)

    return {
        "Portfolio trades": accepted,
        "Total return %": round(total_return, 1),
        "CAGR %": round(cagr, 1),
        "Max drawdown %": round(_max_drawdown(equity), 1),
        "Exposure %": round(exposure, 1),
        "SPY total return %": round(spy_total, 1),
        "SPY CAGR %": round(spy_cagr, 1),
        "CAGR alpha %": round(cagr - spy_cagr, 1),
    }


def run_strategy_vs_spy(
    symbols,
    technical_func,
    threshold: float = 85,
    min_rr: float = 2.0,
    zone_mode: str = "either",
    max_hold: int = 40,
    cooldown: int = 10,
    max_positions: int = 5,
    benchmark: str = "SPY",
):
    histories = {}
    candidates_by_symbol = {}

    for raw in symbols:
        symbol = str(raw).strip().upper()
        if not symbol:
            continue
        try:
            df = _clean_history(symbol)
        except Exception:
            continue
        if df.empty or len(df) < 300:
            continue
        histories[symbol] = df
        candidates_by_symbol[symbol] = _generate_candidates(
            symbol,
            df,
            technical_func,
            threshold=threshold,
            min_rr=min_rr,
            zone_mode=zone_mode,
            cooldown=cooldown,
            max_hold=max_hold,
        )

    spy = _clean_history(benchmark)
    if spy.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    method_rows = []
    portfolio_rows = []
    all_trades = []

    for label, method in EXIT_METHODS.items():
        method_trades = []

        for symbol, candidates in candidates_by_symbol.items():
            df = histories[symbol]
            next_free_idx = -1

            for cand in candidates:
                if int(cand["Entry idx"]) <= next_free_idx:
                    continue

                trade = _simulate_one_trade(cand, df, method=method, max_hold=max_hold)
                if trade is None:
                    continue

                trade["Exit method"] = label
                trade["SPY return %"] = _spy_return(spy, trade["Entry date"], trade["Exit date"])
                trade["Excess vs SPY %"] = (
                    trade["Return %"] - trade["SPY return %"]
                    if not np.isnan(trade["SPY return %"])
                    else np.nan
                )
                method_trades.append(trade)

                try:
                    next_free_idx = int(df.index.get_loc(trade["Exit date"]))
                except Exception:
                    next_free_idx = int(cand["Entry idx"]) + max_hold

        trades_df = pd.DataFrame(method_trades)
        if trades_df.empty:
            continue

        all_trades.append(trades_df)
        returns = pd.to_numeric(trades_df["Return %"], errors="coerce").dropna()
        winners = returns[returns > 0]
        losers = returns[returns < 0]
        excess = pd.to_numeric(trades_df["Excess vs SPY %"], errors="coerce").dropna()

        method_rows.append({
            "Exit method": label,
            "Trades": len(trades_df),
            "Win rate %": round(float((returns > 0).mean() * 100), 1),
            "Avg return %": round(float(returns.mean()), 2),
            "Median return %": round(float(returns.median()), 2),
            "Avg winner %": round(float(winners.mean()), 2) if len(winners) else np.nan,
            "Avg loser %": round(float(losers.mean()), 2) if len(losers) else np.nan,
            "Profit factor": round(_profit_factor(returns), 2),
            "Median hold days": round(float(trades_df["Hold days"].median()), 1),
            "Avg SPY same-period %": round(float(trades_df["SPY return %"].mean()), 2),
            "Avg excess vs SPY %": round(float(excess.mean()), 2) if len(excess) else np.nan,
            "Beat SPY %": round(float((excess > 0).mean() * 100), 1) if len(excess) else np.nan,
            "Invalidation %": round(float((trades_df["Exit reason"] == "Invalidation").mean() * 100), 1),
        })

        p = _portfolio_simulation(
            trades_df,
            histories=histories,
            spy=spy,
            max_positions=max_positions,
        )
        if p:
            portfolio_rows.append({"Exit method": label, **p})

    trade_summary = pd.DataFrame(method_rows)
    portfolio_summary = pd.DataFrame(portfolio_rows)
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()

    if not trade_summary.empty:
        trade_summary = trade_summary.sort_values(
            ["Avg excess vs SPY %", "Profit factor"],
            ascending=[False, False],
        ).reset_index(drop=True)

    if not portfolio_summary.empty:
        portfolio_summary = portfolio_summary.sort_values(
            ["CAGR alpha %", "CAGR %"],
            ascending=[False, False],
        ).reset_index(drop=True)

    return trade_summary, portfolio_summary, trades
