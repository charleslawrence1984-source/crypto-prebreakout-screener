from __future__ import annotations

import math
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf


def safe(v, default=np.nan):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _row(frame, names):
    if frame is None or frame.empty:
        return None
    for name in names:
        if name in frame.index:
            s = pd.to_numeric(frame.loc[name], errors="coerce").dropna()
            if not s.empty:
                return s
    return None


def _cagr(series):
    if series is None or len(series) < 2:
        return np.nan
    s = series.sort_index()
    first = safe(s.iloc[0])
    last = safe(s.iloc[-1])
    years = max(len(s) - 1, 1)
    if np.isnan(first) or np.isnan(last) or first <= 0 or last <= 0:
        return np.nan
    return (last / first) ** (1 / years) - 1


@st.cache_data(ttl=3600, show_spinner=False)
def long_term_analysis(symbol: str, price: float, fund_snapshot: dict | None = None) -> dict:
    t = yf.Ticker(symbol)
    try:
        info = t.info or {}
    except Exception:
        info = {}

    try:
        income = t.financials
    except Exception:
        income = pd.DataFrame()
    try:
        cashflow = t.cashflow
    except Exception:
        cashflow = pd.DataFrame()
    try:
        balance = t.balance_sheet
    except Exception:
        balance = pd.DataFrame()

    fund = fund_snapshot or {}
    market_cap = safe(fund.get("market_cap", info.get("marketCap")))
    profit_margin = fund.get("profit_margin")
    if profit_margin is None:
        pm = safe(info.get("profitMargins"))
        profit_margin = None if np.isnan(pm) else pm * 100

    debt_equity = safe(fund.get("debt_equity", info.get("debtToEquity")))
    current_fcf = safe(fund.get("free_cash_flow", info.get("freeCashflow")))

    revenue = _row(income, ["Total Revenue", "Operating Revenue"])
    net_income = _row(income, ["Net Income", "Net Income Common Stockholders"])
    fcf_series = _row(cashflow, ["Free Cash Flow"])
    if fcf_series is None:
        cfo = _row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        capex = _row(cashflow, ["Capital Expenditure", "Capital Expenditures"])
        if cfo is not None and capex is not None:
            common = cfo.index.intersection(capex.index)
            if len(common) >= 2:
                fcf_series = cfo.loc[common] + capex.loc[common]  # capex is normally negative

    shares = _row(balance, ["Ordinary Shares Number", "Share Issued"])

    revenue_cagr = _cagr(revenue)
    earnings_cagr = _cagr(net_income)
    roe = safe(info.get("returnOnEquity"))
    operating_margin = safe(info.get("operatingMargins"))
    current_ratio = safe(info.get("currentRatio"))
    total_cash = safe(info.get("totalCash"))
    total_debt = safe(info.get("totalDebt"))

    score = 0.0

    # Growth consistency / runway: 25
    if not np.isnan(revenue_cagr):
        rg = revenue_cagr * 100
        score += 12 if rg >= 12 else 10 if rg >= 8 else 7 if rg >= 5 else 3 if rg > 0 else 0
        ordered = revenue.sort_index() if revenue is not None else None
        if ordered is not None and len(ordered) >= 3:
            growths = ordered.pct_change().dropna()
            positive_share = float((growths > 0).mean()) if len(growths) else 0
            score += 5 if positive_share >= 0.75 else 3 if positive_share >= 0.5 else 0
    else:
        current_rg = fund.get("revenue_growth")
        if current_rg is not None:
            score += 8 if current_rg >= 10 else 5 if current_rg >= 5 else 2 if current_rg > 0 else 0

    if not np.isnan(earnings_cagr):
        eg = earnings_cagr * 100
        score += 8 if eg >= 12 else 6 if eg >= 8 else 4 if eg >= 3 else 1 if eg > 0 else 0
    else:
        current_eg = fund.get("earnings_growth")
        if current_eg is not None:
            score += 6 if current_eg >= 15 else 4 if current_eg >= 8 else 2 if current_eg > 0 else 0

    # Profitability / capital efficiency: 25
    if profit_margin is not None:
        score += 8 if profit_margin >= 20 else 6 if profit_margin >= 10 else 3 if profit_margin >= 5 else 1 if profit_margin > 0 else 0
    if not np.isnan(operating_margin):
        om = operating_margin * 100
        score += 7 if om >= 20 else 5 if om >= 12 else 3 if om >= 6 else 1 if om > 0 else 0
    if not np.isnan(roe):
        r = roe * 100
        score += 10 if r >= 20 else 7 if r >= 12 else 4 if r >= 8 else 1 if r > 0 else 0

    # Cash generation durability: 15
    if fcf_series is not None and len(fcf_series) >= 2:
        vals = pd.to_numeric(fcf_series, errors="coerce").dropna()
        if len(vals):
            pos = float((vals > 0).mean())
            score += 10 if pos >= 0.75 else 7 if pos >= 0.5 else 3 if pos > 0 else 0
            if len(vals) >= 3:
                latest = safe(vals.sort_index().iloc[-1])
                oldest = safe(vals.sort_index().iloc[0])
                if not np.isnan(latest) and not np.isnan(oldest) and latest > oldest:
                    score += 5
    elif not np.isnan(current_fcf) and current_fcf > 0:
        score += 8

    # Balance sheet: 15
    if not np.isnan(debt_equity):
        score += 8 if debt_equity <= 50 else 6 if debt_equity <= 100 else 4 if debt_equity <= 150 else 1 if debt_equity <= 250 else 0
    else:
        score += 3

    if not np.isnan(current_ratio):
        score += 4 if current_ratio >= 1.5 else 2 if current_ratio >= 1.0 else 0
    else:
        score += 1

    if not np.isnan(total_cash) and not np.isnan(total_debt):
        score += 3 if total_cash >= total_debt else 1 if total_cash >= total_debt * 0.5 else 0
    else:
        score += 1

    # Dilution discipline: 10
    if shares is not None and len(shares) >= 2:
        ordered = shares.sort_index()
        old = safe(ordered.iloc[0])
        new = safe(ordered.iloc[-1])
        if not np.isnan(old) and old > 0 and not np.isnan(new):
            dilution = (new / old - 1) * 100
            score += 10 if dilution <= 2 else 7 if dilution <= 5 else 4 if dilution <= 10 else 0
        else:
            score += 5
    else:
        score += 5

    # Scale / durability proxy: 10
    if not np.isnan(market_cap):
        score += 10 if market_cap >= 100e9 else 8 if market_cap >= 20e9 else 6 if market_cap >= 5e9 else 3 if market_cap >= 1e9 else 1
    else:
        score += 3

    score = round(min(100, score), 1)
    label = "ELITE" if score >= 85 else "STRONG" if score >= 75 else "DEVELOPING" if score >= 65 else "WEAK"

    return {
        "long_term_score": score,
        "long_term_label": label,
        "revenue_cagr_pct": None if np.isnan(revenue_cagr) else round(revenue_cagr * 100, 1),
        "earnings_cagr_pct": None if np.isnan(earnings_cagr) else round(earnings_cagr * 100, 1),
        "roe_pct": None if np.isnan(roe) else round(roe * 100, 1),
        "operating_margin_pct": None if np.isnan(operating_margin) else round(operating_margin * 100, 1),
        "current_ratio": None if np.isnan(current_ratio) else round(current_ratio, 2),
    }


def long_term_entry_score(symbol: str, price: float, valuation_score: float, tech: dict | None = None) -> dict:
    score = max(0.0, min(60.0, (safe(valuation_score, 10) / 20.0) * 60.0))

    history = None
    if tech and isinstance(tech.get("history"), pd.DataFrame):
        history = tech["history"]
    if history is None or history.empty:
        try:
            history = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=False)
        except Exception:
            history = pd.DataFrame()

    sma200 = np.nan
    high52 = np.nan
    if history is not None and not history.empty and "Close" in history.columns:
        close = pd.to_numeric(history["Close"], errors="coerce").dropna()
        if len(close) >= 50:
            high52 = safe(close.tail(252).max())
        if len(close) >= 200:
            sma200 = safe(close.rolling(200).mean().iloc[-1])

    # Discount from 52-week high: 15
    if not np.isnan(high52) and high52 > 0:
        drawdown = (1 - price / high52) * 100
        score += 15 if drawdown >= 20 else 11 if drawdown >= 12 else 7 if drawdown >= 7 else 3 if drawdown >= 3 else 1
    else:
        score += 7

    # Position vs 200-day average: 15
    if not np.isnan(sma200) and sma200 > 0:
        ratio = price / sma200
        score += 15 if ratio <= 1.00 else 12 if ratio <= 1.05 else 8 if ratio <= 1.10 else 4 if ratio <= 1.20 else 1
    else:
        score += 7

    # Current technical entry zone: 10
    if tech:
        if tech.get("in_strong_zone"):
            score += 10
        elif tech.get("in_preferred_zone"):
            score += 8
        else:
            score += 2
    else:
        score += 5

    score = round(min(100, score), 1)
    label = "EXCELLENT" if score >= 80 else "ATTRACTIVE" if score >= 70 else "FAIR" if score >= 60 else "WAIT"

    return {"long_term_entry_score": score, "long_term_entry_label": label}


def strategy_label(trade_signal: str, one_year_score: float, valuation_score: float, long_term_score: float, long_term_entry: float) -> str:
    active_trade = trade_signal in ("ACTIONABLE", "ELITE")

    if active_trade and long_term_score >= 85 and long_term_entry >= 70:
        return "SWING + LONG-TERM BUY"
    if active_trade and one_year_score >= 70 and valuation_score >= 12:
        return "SWING-TO-1Y-HOLD"
    if active_trade:
        return "SWING ONLY"
    if long_term_score >= 85 and long_term_entry >= 70:
        return "LONG-TERM BUY"
    if long_term_score >= 85:
        return "ELITE COMPOUNDER — WAIT FOR ENTRY"
    if one_year_score >= 70 and valuation_score >= 12:
        return "1Y HOLD WATCH"
    return "WAIT"
