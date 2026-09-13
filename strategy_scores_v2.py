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
                return s.sort_index()
    return None


def _cagr(series):
    if series is None or len(series) < 2:
        return np.nan
    s = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    if len(s) < 2:
        return np.nan
    first = safe(s.iloc[0])
    last = safe(s.iloc[-1])
    years = max(len(s) - 1, 1)
    if np.isnan(first) or np.isnan(last) or first <= 0 or last <= 0:
        return np.nan
    return (last / first) ** (1 / years) - 1


def _positive_growth_share(series):
    if series is None or len(series) < 2:
        return np.nan
    s = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    g = s.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if g.empty:
        return np.nan
    return float((g > 0).mean())


def _positive_share(series):
    if series is None or len(series) == 0:
        return np.nan
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return np.nan
    return float((s > 0).mean())


def _latest(series):
    if series is None or len(series) == 0:
        return np.nan
    s = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    return safe(s.iloc[-1]) if not s.empty else np.nan


def _latest_pair_ratio(num, den):
    if num is None or den is None:
        return np.nan
    common = num.index.intersection(den.index)
    if len(common) == 0:
        return np.nan
    n = pd.to_numeric(num.loc[common], errors="coerce").dropna()
    d = pd.to_numeric(den.loc[common], errors="coerce").dropna()
    common2 = n.index.intersection(d.index)
    if len(common2) == 0:
        return np.nan
    nv = safe(n.loc[common2].iloc[-1])
    dv = safe(d.loc[common2].iloc[-1])
    if np.isnan(nv) or np.isnan(dv) or dv == 0:
        return np.nan
    return nv / dv


@st.cache_data(ttl=3600, show_spinner=False)
def long_term_analysis(symbol: str, price: float, fund_snapshot: dict | None = None) -> dict:
    """Strict financial compounder filter.

    Yahoo typically exposes roughly 3-4 annual statements here, so this is a
    research filter rather than a complete 25-35 year investment thesis.
    """
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

    revenue = _row(income, ["Total Revenue", "Operating Revenue"])
    net_income = _row(income, ["Net Income", "Net Income Common Stockholders"])
    operating_income = _row(income, ["Operating Income", "EBIT"])
    pretax_income = _row(income, ["Pretax Income", "Income Before Tax"])
    tax_provision = _row(income, ["Tax Provision", "Income Tax Expense"])
    interest_expense = _row(income, ["Interest Expense", "Interest Expense Non Operating"])

    fcf = _row(cashflow, ["Free Cash Flow"])
    if fcf is None:
        cfo = _row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        capex = _row(cashflow, ["Capital Expenditure", "Capital Expenditures"])
        if cfo is not None and capex is not None:
            common = cfo.index.intersection(capex.index)
            if len(common) >= 2:
                fcf = (cfo.loc[common] + capex.loc[common]).sort_index()

    shares = _row(balance, ["Ordinary Shares Number", "Share Issued"])
    total_debt = _row(balance, ["Total Debt"])
    equity = _row(balance, ["Stockholders Equity", "Total Stockholder Equity"])
    cash = _row(balance, [
        "Cash Cash Equivalents And Short Term Investments",
        "Cash And Cash Equivalents",
        "Cash",
    ])

    revenue_cagr = _cagr(revenue)
    earnings_cagr = _cagr(net_income)
    fcf_cagr = _cagr(fcf)
    revenue_positive_growth = _positive_growth_share(revenue)
    positive_income_share = _positive_share(net_income)
    positive_fcf_share = _positive_share(fcf)

    # FCF per share CAGR rewards genuine per-share compounding and penalises dilution.
    fcf_per_share_cagr = np.nan
    if fcf is not None and shares is not None:
        common = fcf.index.intersection(shares.index)
        if len(common) >= 2:
            sh = shares.loc[common].replace(0, np.nan)
            fps = (fcf.loc[common] / sh).replace([np.inf, -np.inf], np.nan).dropna()
            fcf_per_share_cagr = _cagr(fps)

    dilution_cagr = np.nan
    if shares is not None and len(shares) >= 2:
        s = shares.sort_index()
        first = safe(s.iloc[0])
        last = safe(s.iloc[-1])
        years = max(len(s) - 1, 1)
        if not np.isnan(first) and first > 0 and not np.isnan(last) and last > 0:
            dilution_cagr = (last / first) ** (1 / years) - 1

    # Latest margins / efficiency.
    operating_margin = _latest_pair_ratio(operating_income, revenue)
    roe = safe(info.get("returnOnEquity"))
    roa = safe(info.get("returnOnAssets"))

    latest_ebit = _latest(operating_income)
    latest_pretax = _latest(pretax_income)
    latest_tax = _latest(tax_provision)
    tax_rate = 0.21
    if not np.isnan(latest_pretax) and latest_pretax > 0 and not np.isnan(latest_tax):
        tax_rate = min(max(latest_tax / latest_pretax, 0.0), 0.35)

    latest_debt = _latest(total_debt)
    latest_equity = _latest(equity)
    latest_cash = _latest(cash)
    latest_fcf = _latest(fcf)

    # Approximate ROIC = NOPAT / invested capital.
    roic = np.nan
    if not np.isnan(latest_ebit) and not np.isnan(latest_equity):
        debt_for_ic = 0.0 if np.isnan(latest_debt) else latest_debt
        cash_for_ic = 0.0 if np.isnan(latest_cash) else latest_cash
        invested_capital = debt_for_ic + latest_equity - cash_for_ic
        if invested_capital > 0:
            roic = latest_ebit * (1 - tax_rate) / invested_capital

    net_debt_to_fcf = np.nan
    if not np.isnan(latest_fcf) and latest_fcf > 0:
        debt_v = 0.0 if np.isnan(latest_debt) else latest_debt
        cash_v = 0.0 if np.isnan(latest_cash) else latest_cash
        net_debt_to_fcf = (debt_v - cash_v) / latest_fcf

    interest_coverage = np.nan
    latest_interest = _latest(interest_expense)
    if not np.isnan(latest_ebit) and not np.isnan(latest_interest) and latest_interest != 0:
        interest_coverage = latest_ebit / abs(latest_interest)

    current_ratio = safe(info.get("currentRatio"))
    debt_equity = safe(fund.get("debt_equity", info.get("debtToEquity")))

    score = 0.0

    # 1) Multi-year growth durability — 20
    if not np.isnan(revenue_cagr):
        rg = revenue_cagr * 100
        score += 8 if rg >= 15 else 7 if rg >= 10 else 5.5 if rg >= 7 else 3.5 if rg >= 4 else 1.5 if rg > 0 else 0
    if not np.isnan(revenue_positive_growth):
        score += 4 if revenue_positive_growth >= 0.99 else 3 if revenue_positive_growth >= 0.66 else 1.5 if revenue_positive_growth >= 0.5 else 0
    if not np.isnan(earnings_cagr):
        eg = earnings_cagr * 100
        score += 5 if eg >= 15 else 4 if eg >= 10 else 2.5 if eg >= 5 else 1 if eg > 0 else 0
    if not np.isnan(positive_income_share):
        score += 3 if positive_income_share >= 0.99 else 2 if positive_income_share >= 0.75 else 0.5 if positive_income_share >= 0.5 else 0

    # 2) Cash compounding — 20
    if not np.isnan(positive_fcf_share):
        score += 7 if positive_fcf_share >= 0.99 else 5 if positive_fcf_share >= 0.75 else 2 if positive_fcf_share >= 0.5 else 0
    if not np.isnan(fcf_cagr):
        fg = fcf_cagr * 100
        score += 5 if fg >= 15 else 4 if fg >= 10 else 2.5 if fg >= 5 else 1 if fg > 0 else 0
    if not np.isnan(fcf_per_share_cagr):
        fpg = fcf_per_share_cagr * 100
        score += 8 if fpg >= 15 else 6 if fpg >= 10 else 4 if fpg >= 5 else 1.5 if fpg > 0 else 0

    # 3) Profitability / capital efficiency — 20
    if not np.isnan(roic):
        r = roic * 100
        score += 10 if r >= 20 else 8 if r >= 15 else 6 if r >= 10 else 3 if r >= 7 else 1 if r > 0 else 0
    if not np.isnan(operating_margin):
        om = operating_margin * 100
        score += 6 if om >= 25 else 5 if om >= 18 else 3.5 if om >= 12 else 2 if om >= 7 else 0.5 if om > 0 else 0
    if not np.isnan(roa):
        a = roa * 100
        score += 2 if a >= 10 else 1.5 if a >= 6 else 0.5 if a > 0 else 0
    if not np.isnan(roe):
        e = roe * 100
        score += 2 if e >= 18 else 1.5 if e >= 12 else 0.5 if e > 0 else 0

    # 4) Balance-sheet resilience — 15
    if not np.isnan(net_debt_to_fcf):
        score += 7 if net_debt_to_fcf <= 0 else 6 if net_debt_to_fcf <= 1 else 4.5 if net_debt_to_fcf <= 2 else 2.5 if net_debt_to_fcf <= 3 else 0.5 if net_debt_to_fcf <= 5 else 0
    if not np.isnan(debt_equity):
        score += 4 if debt_equity <= 50 else 3 if debt_equity <= 100 else 2 if debt_equity <= 150 else 0.5 if debt_equity <= 250 else 0
    if not np.isnan(interest_coverage):
        score += 3 if interest_coverage >= 10 else 2 if interest_coverage >= 5 else 1 if interest_coverage >= 2 else 0
    elif not np.isnan(current_ratio):
        score += 2 if current_ratio >= 1.5 else 1 if current_ratio >= 1 else 0
    if not np.isnan(current_ratio) and not np.isnan(interest_coverage):
        score += 1 if current_ratio >= 1.2 else 0

    # 5) Shareholder dilution discipline — 10
    if not np.isnan(dilution_cagr):
        d = dilution_cagr * 100
        score += 10 if d <= 0 else 8 if d <= 1 else 6 if d <= 2 else 3 if d <= 4 else 0
    else:
        score += 2

    # 6) Evidence / consistency quality — 10
    evidence = 0
    if revenue is not None and len(revenue) >= 3:
        evidence += 2
    if fcf is not None and len(fcf) >= 3:
        evidence += 2
    if shares is not None and len(shares) >= 2:
        evidence += 2
    if not np.isnan(roic):
        evidence += 2
    if not np.isnan(net_debt_to_fcf):
        evidence += 1
    if not np.isnan(dilution_cagr):
        evidence += 1
    score += evidence

    # 7) Scale / resilience proxy — 5. This is deliberately small; size is not a moat.
    if not np.isnan(market_cap):
        score += 5 if market_cap >= 100e9 else 4 if market_cap >= 20e9 else 3 if market_cap >= 5e9 else 1.5 if market_cap >= 1e9 else 0

    raw_score = min(100.0, score)
    cap_reasons = []

    # Hard quality gates: a very high grade requires enough evidence and genuine
    # per-share compounding, not just strong current margins.
    score_cap = 100.0
    if evidence < 7:
        score_cap = min(score_cap, 74.0)
        cap_reasons.append("limited multi-year evidence")
    elif evidence < 9:
        score_cap = min(score_cap, 84.0)
        cap_reasons.append("incomplete elite-level evidence")

    if np.isnan(fcf_per_share_cagr):
        score_cap = min(score_cap, 84.0)
        cap_reasons.append("FCF/share history unavailable")
    if np.isnan(roic):
        score_cap = min(score_cap, 84.0)
        cap_reasons.append("ROIC unavailable")

    if not np.isnan(positive_fcf_share) and positive_fcf_share < 0.5:
        score_cap = min(score_cap, 64.0)
        cap_reasons.append("weak FCF consistency")
    if not np.isnan(revenue_cagr) and revenue_cagr < 0:
        score_cap = min(score_cap, 69.0)
        cap_reasons.append("shrinking multi-year revenue")
    if not np.isnan(fcf_per_share_cagr) and fcf_per_share_cagr < 0:
        score_cap = min(score_cap, 74.0)
        cap_reasons.append("declining FCF/share")
    if not np.isnan(net_debt_to_fcf) and net_debt_to_fcf > 5:
        score_cap = min(score_cap, 79.0)
        cap_reasons.append("high net debt vs FCF")

    elite_gate = all([
        evidence >= 9,
        not np.isnan(revenue_cagr) and revenue_cagr >= 0.08,
        not np.isnan(fcf_per_share_cagr) and fcf_per_share_cagr >= 0.08,
        not np.isnan(positive_fcf_share) and positive_fcf_share >= 0.75,
        not np.isnan(roic) and roic >= 0.15,
        not np.isnan(dilution_cagr) and dilution_cagr <= 0.02,
        np.isnan(net_debt_to_fcf) or net_debt_to_fcf <= 2.5,
    ])
    if raw_score >= 90 and not elite_gate:
        score_cap = min(score_cap, 89.0)
        cap_reasons.append("elite gate not fully met")

    final_score = round(min(raw_score, score_cap), 1)
    label = (
        "ELITE" if final_score >= 90 else
        "STRONG" if final_score >= 82 else
        "QUALITY" if final_score >= 72 else
        "DEVELOPING" if final_score >= 60 else
        "WEAK"
    )

    return {
        "long_term_score": final_score,
        "long_term_raw_score": round(raw_score, 1),
        "long_term_label": label,
        "evidence_score": evidence,
        "elite_gate_pass": bool(elite_gate),
        "score_cap_reason": ", ".join(dict.fromkeys(cap_reasons)),
        "revenue_cagr_pct": None if np.isnan(revenue_cagr) else round(revenue_cagr * 100, 1),
        "earnings_cagr_pct": None if np.isnan(earnings_cagr) else round(earnings_cagr * 100, 1),
        "fcf_cagr_pct": None if np.isnan(fcf_cagr) else round(fcf_cagr * 100, 1),
        "fcf_per_share_cagr_pct": None if np.isnan(fcf_per_share_cagr) else round(fcf_per_share_cagr * 100, 1),
        "positive_fcf_years_pct": None if np.isnan(positive_fcf_share) else round(positive_fcf_share * 100, 0),
        "roic_pct": None if np.isnan(roic) else round(roic * 100, 1),
        "roe_pct": None if np.isnan(roe) else round(roe * 100, 1),
        "operating_margin_pct": None if np.isnan(operating_margin) else round(operating_margin * 100, 1),
        "dilution_cagr_pct": None if np.isnan(dilution_cagr) else round(dilution_cagr * 100, 2),
        "net_debt_to_fcf": None if np.isnan(net_debt_to_fcf) else round(net_debt_to_fcf, 2),
        "interest_coverage": None if np.isnan(interest_coverage) else round(interest_coverage, 1),
        "current_ratio": None if np.isnan(current_ratio) else round(current_ratio, 2),
    }


def long_term_entry_score(symbol: str, price: float, valuation_score: float, tech: dict | None = None) -> dict:
    # Entry is deliberately separate from business quality.
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

    if not np.isnan(high52) and high52 > 0:
        drawdown = (1 - price / high52) * 100
        score += 15 if drawdown >= 20 else 11 if drawdown >= 12 else 7 if drawdown >= 7 else 3 if drawdown >= 3 else 1
    else:
        score += 7

    if not np.isnan(sma200) and sma200 > 0:
        ratio = price / sma200
        score += 15 if ratio <= 1.00 else 12 if ratio <= 1.05 else 8 if ratio <= 1.10 else 4 if ratio <= 1.20 else 1
    else:
        score += 7

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

    if active_trade and long_term_score >= 90 and long_term_entry >= 70:
        return "SWING + ELITE LONG-TERM CANDIDATE"
    if active_trade and long_term_score >= 82 and long_term_entry >= 70:
        return "SWING + LONG-TERM RESEARCH CANDIDATE"
    if active_trade and one_year_score >= 70 and valuation_score >= 12:
        return "SWING-TO-1Y-HOLD"
    if active_trade:
        return "SWING ONLY"

    if long_term_score >= 90 and long_term_entry >= 70:
        return "ELITE LONG-TERM CANDIDATE"
    if long_term_score >= 82 and long_term_entry >= 70:
        return "LONG-TERM RESEARCH CANDIDATE"
    if long_term_score >= 90:
        return "ELITE COMPOUNDER — WAIT FOR ENTRY"
    if long_term_score >= 82:
        return "QUALITY COMPOUNDER — WAIT FOR ENTRY"
    if one_year_score >= 70 and valuation_score >= 12:
        return "1Y HOLD WATCH"
    return "WAIT"
