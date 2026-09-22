from __future__ import annotations

import math
import re
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf


BASE_REQUIRED_RETURN = 0.09
VALUATION_MODEL_VERSION = "investment-fx-v2-2026-09-22"

CYCLICAL_TERMS = (
    "oil", "gas", "coal", "steel", "copper", "aluminum", "aluminium",
    "mining", "metals", "shipping", "marine", "airline", "homebuilding",
    "homebuilder", "lumber", "paper", "commodity"
)

MOAT_PATTERNS = {
    "brand/pricing power": ("brand", "premium", "trademark", "loyal", "pricing"),
    "switching costs/ecosystem": ("subscription", "workflow", "ecosystem", "integrated", "mission-critical", "recurring"),
    "network effects": ("network", "marketplace", "platform", "participants", "users"),
    "proprietary IP/technology": ("patent", "proprietary", "intellectual property", "patented"),
    "regulatory/licensing": ("license", "licence", "regulatory approval", "regulated"),
    "scale/cost advantage": ("scale", "low-cost", "cost advantage", "installed base", "distribution network"),
}


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
    first, last = safe(s.iloc[0]), safe(s.iloc[-1])
    years = max(len(s) - 1, 1)
    if np.isnan(first) or np.isnan(last) or first <= 0 or last <= 0:
        return np.nan
    return (last / first) ** (1 / years) - 1


def _positive_share(series):
    if series is None:
        return np.nan
    s = pd.to_numeric(series, errors="coerce").dropna()
    return np.nan if s.empty else float((s > 0).mean())


def _latest(series):
    if series is None:
        return np.nan
    s = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    return np.nan if s.empty else safe(s.iloc[-1])


def _median_positive(series, n=3):
    if series is None:
        return np.nan
    s = pd.to_numeric(series, errors="coerce").dropna().sort_index().tail(n)
    s = s[s > 0]
    return np.nan if s.empty else float(s.median())


def _margin_series(num, den):
    if num is None or den is None:
        return None
    common = num.index.intersection(den.index)
    if len(common) == 0:
        return None
    x = pd.to_numeric(num.loc[common], errors="coerce")
    y = pd.to_numeric(den.loc[common], errors="coerce").replace(0, np.nan)
    return (x / y).replace([np.inf, -np.inf], np.nan).dropna().sort_index()


def _latest_pair_ratio(num, den):
    s = _margin_series(num, den)
    return np.nan if s is None or s.empty else safe(s.iloc[-1])


def _series_cv(series):
    if series is None:
        return np.nan
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 3:
        return np.nan
    m = abs(float(s.mean()))
    return np.nan if m <= 1e-12 else float(s.std(ddof=0) / m)


def _detect_moat_mechanisms(summary: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(summary or "").lower())
    found = []
    for label, patterns in MOAT_PATTERNS.items():
        if any(p in text for p in patterns):
            found.append(label)
    return found


def _growth_path(start: float, terminal: float, years: int, moat_confidence: str) -> list[float]:
    start = max(-0.05, min(0.18, start))
    terminal = max(0.0, min(0.035, terminal))
    exponent = {"HIGH": 1.8, "MEDIUM": 1.25, "LOW": 0.85}.get(moat_confidence, 1.0)
    out = []
    for y in range(1, years + 1):
        progress = (y / years) ** exponent
        out.append(start * (1 - progress) + terminal * progress)
    return out


def _dcf_per_share(
    starting_fcf_per_share: float,
    start_growth: float,
    terminal_growth: float,
    discount_rate: float,
    moat_confidence: str,
    years: int = 10,
) -> float:
    if (
        np.isnan(starting_fcf_per_share)
        or starting_fcf_per_share <= 0
        or discount_rate <= terminal_growth
    ):
        return np.nan
    fcf = starting_fcf_per_share
    pv = 0.0
    growths = _growth_path(start_growth, terminal_growth, years, moat_confidence)
    for year, g in enumerate(growths, start=1):
        fcf *= 1 + g
        pv += fcf / ((1 + discount_rate) ** year)
    terminal = fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv += terminal / ((1 + discount_rate) ** years)
    return pv


@st.cache_data(ttl=3600, show_spinner=False)
def _financial_to_quote_fx(financial_currency: str, quote_currency: str) -> dict:
    """
    Return quote-currency units per one financial-currency unit.

    DCF cash flows are expressed in the company's financial-statement currency.
    Share prices can be quoted in another currency on secondary listings.  The
    valuation must be converted before comparing intrinsic value with price.
    GBp/GBX are handled as pence (100 pence per GBP).
    """
    source = str(financial_currency or "").strip().upper()
    raw_target = str(quote_currency or "").strip()
    target_upper = raw_target.upper()
    minor_scale = 100.0 if raw_target == "GBp" or target_upper == "GBX" else 1.0
    target = "GBP" if raw_target == "GBp" or target_upper == "GBX" else target_upper

    if not source or not target:
        return {
            "rate": np.nan,
            "status": "MISSING CURRENCY",
            "pair": "",
            "source": source,
            "target": target,
            "quote_scale": minor_scale,
        }

    if source == target:
        return {
            "rate": minor_scale,
            "status": "PASS",
            "pair": f"{source}/{target}",
            "source": source,
            "target": target,
            "quote_scale": minor_scale,
        }

    def last_close(pair: str) -> float:
        try:
            hist = yf.Ticker(pair).history(period="10d", interval="1d", auto_adjust=False)
            if hist is None or hist.empty or "Close" not in hist.columns:
                return np.nan
            closes = pd.to_numeric(hist["Close"], errors="coerce").dropna()
            return np.nan if closes.empty else safe(closes.iloc[-1])
        except Exception:
            return np.nan

    direct_pair = f"{source}{target}=X"
    direct = last_close(direct_pair)
    if not np.isnan(direct) and direct > 0:
        return {
            "rate": direct * minor_scale,
            "status": "PASS",
            "pair": direct_pair,
            "source": source,
            "target": target,
            "quote_scale": minor_scale,
        }

    inverse_pair = f"{target}{source}=X"
    inverse = last_close(inverse_pair)
    if not np.isnan(inverse) and inverse > 0:
        return {
            "rate": (1.0 / inverse) * minor_scale,
            "status": "PASS",
            "pair": inverse_pair + " (inverse)",
            "source": source,
            "target": target,
            "quote_scale": minor_scale,
        }

    return {
        "rate": np.nan,
        "status": "FX UNAVAILABLE",
        "pair": f"{source}->{target}",
        "source": source,
        "target": target,
        "quote_scale": minor_scale,
    }


def _score_band(value, bands):
    for threshold, points in bands:
        if value >= threshold:
            return points
    return 0.0


@st.cache_data(ttl=3600, show_spinner=False)
def long_term_analysis(symbol: str, price: float, fund_snapshot: dict | None = None, _ticker=None) -> dict:
    """10-years-to-forever investment research model.

    The automated layer is deliberately conservative. Yahoo commonly exposes
    only ~3-4 annual statements, so structural moat, disruption, concentration,
    governance and other qualitative items are surfaced as review items rather
    than invented from missing data.
    """
    # Streamlit ignores underscore-prefixed arguments when building its cache
    # key, allowing callers to reuse one live Ticker object safely.
    t = _ticker or yf.Ticker(symbol)
    fund = fund_snapshot or {}
    info = {} if fund_snapshot is not None else None
    if info is None:
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

    sector = str(fund.get("sector") or info.get("sector") or "")
    industry = str(fund.get("industry") or info.get("industry") or "")
    summary = str(fund.get("business_summary") or info.get("longBusinessSummary") or "")
    market_cap = safe(fund.get("market_cap", info.get("marketCap")))

    is_insurer = "insurance" in industry.lower()
    is_reit = "reit" in industry.lower() or "real estate investment trust" in industry.lower()
    is_financial = sector.lower() == "financial services" and not is_insurer
    specialist_sector = is_insurer or is_reit or is_financial

    revenue = _row(income, ["Total Revenue", "Operating Revenue"])
    gross_profit = _row(income, ["Gross Profit"])
    operating_income = _row(income, ["Operating Income", "EBIT"])
    net_income = _row(income, ["Net Income", "Net Income Common Stockholders"])
    pretax_income = _row(income, ["Pretax Income", "Income Before Tax"])
    tax_provision = _row(income, ["Tax Provision", "Income Tax Expense"])
    interest_expense = _row(income, ["Interest Expense", "Interest Expense Non Operating"])

    cfo = _row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    capex = _row(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    fcf = _row(cashflow, ["Free Cash Flow"])
    if fcf is None and cfo is not None and capex is not None:
        common = cfo.index.intersection(capex.index)
        if len(common):
            fcf = (cfo.loc[common] + capex.loc[common]).sort_index()

    sbc = _row(cashflow, ["Stock Based Compensation", "Stock Based Compensation Expense"])
    adjusted_fcf = fcf.copy() if fcf is not None else None
    if adjusted_fcf is not None and sbc is not None:
        common = adjusted_fcf.index.intersection(sbc.index)
        if len(common):
            adjusted_fcf.loc[common] = adjusted_fcf.loc[common] - sbc.loc[common].clip(lower=0)

    acquisitions = _row(cashflow, ["Net Business Purchases", "Purchase Of Business", "Acquisitions Net"])
    dividends = _row(cashflow, ["Cash Dividends Paid", "Common Stock Dividend Paid"])
    repurchases = _row(cashflow, ["Repurchase Of Capital Stock", "Repurchase Of Stock"])

    shares = _row(balance, ["Ordinary Shares Number", "Share Issued"])
    total_debt = _row(balance, ["Total Debt"])
    equity = _row(balance, ["Stockholders Equity", "Total Stockholder Equity"])
    cash = _row(balance, [
        "Cash Cash Equivalents And Short Term Investments",
        "Cash And Cash Equivalents",
        "Cash",
    ])
    goodwill = _row(balance, ["Goodwill And Other Intangible Assets", "Goodwill"])
    invested_capital_stmt = _row(balance, ["Invested Capital"])
    working_capital = _row(balance, ["Working Capital"])

    revenue_cagr = _cagr(revenue)
    earnings_cagr = _cagr(net_income)
    fcf_cagr = _cagr(adjusted_fcf)
    positive_income_share = _positive_share(net_income)
    positive_fcf_share = _positive_share(adjusted_fcf)

    latest_shares = _latest(shares)
    normalized_fcf = _median_positive(adjusted_fcf, 3)
    normalized_fcf_per_share = (
        normalized_fcf / latest_shares
        if not np.isnan(normalized_fcf) and not np.isnan(latest_shares) and latest_shares > 0
        else np.nan
    )

    fcf_per_share_cagr = np.nan
    if adjusted_fcf is not None and shares is not None:
        common = adjusted_fcf.index.intersection(shares.index)
        if len(common) >= 2:
            fps = adjusted_fcf.loc[common] / shares.loc[common].replace(0, np.nan)
            fcf_per_share_cagr = _cagr(fps)

    book_value_per_share_cagr = np.nan
    if equity is not None and shares is not None:
        common = equity.index.intersection(shares.index)
        if len(common) >= 2:
            book_value_per_share_cagr = _cagr(
                equity.loc[common] / shares.loc[common].replace(0, np.nan)
            )

    dilution_cagr = _cagr(shares)

    gross_margin_s = _margin_series(gross_profit, revenue)
    operating_margin_s = _margin_series(operating_income, revenue)
    operating_margin = np.nan if operating_margin_s is None or operating_margin_s.empty else safe(operating_margin_s.iloc[-1])
    gross_margin = np.nan if gross_margin_s is None or gross_margin_s.empty else safe(gross_margin_s.iloc[-1])
    operating_margin_cv = _series_cv(operating_margin_s)
    gross_margin_cv = _series_cv(gross_margin_s)

    latest_ebit = _latest(operating_income)
    latest_pretax = _latest(pretax_income)
    latest_tax = _latest(tax_provision)
    latest_debt = _latest(total_debt)
    latest_equity = _latest(equity)
    latest_cash = _latest(cash)
    latest_fcf = _latest(adjusted_fcf)

    tax_rate = 0.21
    if not np.isnan(latest_pretax) and latest_pretax > 0 and not np.isnan(latest_tax):
        tax_rate = min(max(latest_tax / latest_pretax, 0.15), 0.30)

    roic = np.nan
    invested_capital = _latest(invested_capital_stmt)
    if np.isnan(invested_capital):
        if not np.isnan(latest_equity):
            invested_capital = latest_equity + (0 if np.isnan(latest_debt) else latest_debt) - (0 if np.isnan(latest_cash) else latest_cash)
    if not np.isnan(latest_ebit) and not np.isnan(invested_capital) and invested_capital > 0:
        roic = latest_ebit * (1 - tax_rate) / invested_capital

    roic_history = []
    if operating_income is not None and equity is not None:
        common = operating_income.index.intersection(equity.index)
        if total_debt is not None:
            common = common.intersection(total_debt.index)
        for dt in common:
            ebit = safe(operating_income.get(dt))
            eq = safe(equity.get(dt))
            debt = safe(total_debt.get(dt), 0) if total_debt is not None else 0
            cash_dt = safe(cash.get(dt), 0) if cash is not None and dt in cash.index else 0
            ic = eq + debt - cash_dt
            if not np.isnan(ebit) and ic > 0:
                roic_history.append(ebit * (1 - tax_rate) / ic)
    roic_median = float(np.median(roic_history)) if roic_history else roic
    roic_trend = np.nan
    if len(roic_history) >= 3:
        # _row is ascending by date, so this is oldest -> newest.
        roic_trend = roic_history[-1] - roic_history[0]

    net_debt_to_fcf = np.nan
    if not np.isnan(latest_fcf) and latest_fcf > 0:
        net_debt_to_fcf = ((0 if np.isnan(latest_debt) else latest_debt) - (0 if np.isnan(latest_cash) else latest_cash)) / latest_fcf

    latest_interest = _latest(interest_expense)
    interest_coverage = np.nan
    if not np.isnan(latest_ebit) and not np.isnan(latest_interest) and latest_interest != 0:
        interest_coverage = latest_ebit / abs(latest_interest)

    current_ratio = safe(fund.get("current_ratio", info.get("currentRatio")))
    debt_equity = safe(fund.get("debt_equity", info.get("debtToEquity")))
    roe = safe(fund.get("return_on_equity", info.get("returnOnEquity")))
    roa = safe(fund.get("return_on_assets", info.get("returnOnAssets")))

    # --- Moat evidence ---
    moat_mechanisms = _detect_moat_mechanisms(summary)
    # Description matches are displayed for manual review only. They do not
    # add points, change confidence, or satisfy an automated investment gate.
    structural_moat_status = "CLUES FOUND" if moat_mechanisms else "UNVERIFIED"

    moat_score = 0.0
    if not np.isnan(roic_median):
        moat_score += _score_band(roic_median * 100, [(25, 34), (20, 31), (15, 27), (12, 22), (10, 16), (7, 8)])
    if not np.isnan(positive_fcf_share):
        moat_score += _score_band(positive_fcf_share, [(0.99, 18), (0.75, 15), (0.60, 9), (0.50, 5)])
    if not np.isnan(operating_margin_cv):
        moat_score += 14 if operating_margin_cv <= 0.10 else 11 if operating_margin_cv <= 0.20 else 7 if operating_margin_cv <= 0.35 else 2
    elif not np.isnan(operating_margin):
        moat_score += 7
    if not np.isnan(gross_margin_cv):
        moat_score += 10 if gross_margin_cv <= 0.08 else 7 if gross_margin_cv <= 0.18 else 3
    elif not np.isnan(gross_margin):
        moat_score += 5
    if not np.isnan(roic_trend):
        if roic_trend >= -0.02:
            moat_score += 6
        elif roic_trend <= -0.05:
            moat_score -= 8
    moat_score = round(max(0, min(100, moat_score)), 1)

    evidence_years = max(
        len(revenue) if revenue is not None else 0,
        len(adjusted_fcf) if adjusted_fcf is not None else 0,
        len(roic_history),
    )
    if evidence_years >= 8 and moat_score >= 78:
        moat_confidence = "HIGH"
    elif moat_score >= 70:
        moat_confidence = "MEDIUM"
    else:
        moat_confidence = "LOW"

    quantitative_moat_pass = (
        moat_score >= 62
        and (np.isnan(roic_median) or roic_median >= 0.10)
        and (np.isnan(positive_fcf_share) or positive_fcf_share >= 0.75)
    )

    # --- Resilience / cash / capital allocation ---
    resilience_score = 0.0
    if np.isnan(net_debt_to_fcf):
        resilience_score += 8
    else:
        resilience_score += 30 if net_debt_to_fcf <= 0 else 25 if net_debt_to_fcf <= 1 else 18 if net_debt_to_fcf <= 2 else 10 if net_debt_to_fcf <= 3 else 3 if net_debt_to_fcf <= 4 else 0
    if not np.isnan(interest_coverage):
        resilience_score += 20 if interest_coverage >= 12 else 16 if interest_coverage >= 8 else 11 if interest_coverage >= 5 else 5 if interest_coverage >= 2 else 0
    else:
        resilience_score += 8
    if not np.isnan(positive_fcf_share):
        resilience_score += 25 if positive_fcf_share >= 0.99 else 20 if positive_fcf_share >= 0.75 else 8 if positive_fcf_share >= 0.5 else 0
    if not np.isnan(current_ratio):
        resilience_score += 12 if current_ratio >= 1.5 else 8 if current_ratio >= 1.1 else 2
    else:
        resilience_score += 5
    if not np.isnan(dilution_cagr):
        d = dilution_cagr * 100
        resilience_score += 13 if d <= 0 else 10 if d <= 1 else 7 if d <= 2 else 2 if d <= 3 else 0
    else:
        resilience_score += 4
    resilience_score = round(min(100, resilience_score), 1)

    cash_quality_score = 0.0
    if not np.isnan(positive_fcf_share):
        cash_quality_score += 40 if positive_fcf_share >= 0.99 else 32 if positive_fcf_share >= 0.75 else 15 if positive_fcf_share >= 0.5 else 0
    if not np.isnan(fcf_per_share_cagr):
        g = fcf_per_share_cagr * 100
        cash_quality_score += 30 if g >= 12 else 25 if g >= 8 else 18 if g >= 4 else 8 if g >= 0 else 0
    if adjusted_fcf is not None and net_income is not None:
        common = adjusted_fcf.index.intersection(net_income.index)
        if len(common):
            ni = pd.to_numeric(net_income.loc[common], errors="coerce").replace(0, np.nan)
            conversion = (pd.to_numeric(adjusted_fcf.loc[common], errors="coerce") / ni).replace([np.inf, -np.inf], np.nan).dropna()
            if len(conversion):
                med = float(conversion.median())
                cash_quality_score += 20 if med >= 0.9 else 15 if med >= 0.7 else 8 if med >= 0.5 else 0
    if not np.isnan(normalized_fcf_per_share) and normalized_fcf_per_share > 0:
        cash_quality_score += 10
    cash_quality_score = round(min(100, cash_quality_score), 1)

    # Retained cash / reinvestment proxy. Dividends and buybacks are not rewarded merely for existing.
    payout = safe(fund.get("payout_ratio", info.get("payoutRatio")))
    if np.isnan(payout):
        retained_rate = 0.65
    else:
        retained_rate = max(0.15, min(0.85, 1 - payout))
    roic_for_growth = roic_median if not np.isnan(roic_median) else 0.10
    reinvestment_engine = max(0.0, min(0.16, retained_rate * max(roic_for_growth, 0)))

    history_checks = [x for x in [revenue_cagr, fcf_per_share_cagr] if not np.isnan(x)]
    historical_anchor = np.median(history_checks) if history_checks else reinvestment_engine
    base_growth = float(np.clip(0.65 * reinvestment_engine + 0.35 * historical_anchor, -0.02, 0.12))
    if revenue_cagr is not np.nan and not np.isnan(revenue_cagr) and revenue_cagr < 0:
        base_growth = min(base_growth, 0.02)

    reinvestment_score = 0.0
    reinvestment_score += _score_band(roic_for_growth * 100, [(25, 40), (20, 36), (15, 30), (12, 22), (10, 16), (7, 8)])
    reinvestment_score += _score_band(base_growth * 100, [(10, 30), (7, 25), (5, 19), (3, 12), (0, 6)])
    if not np.isnan(fcf_per_share_cagr):
        reinvestment_score += _score_band(fcf_per_share_cagr * 100, [(12, 20), (8, 16), (5, 12), (0, 6)])
    if not np.isnan(dilution_cagr):
        reinvestment_score += 10 if dilution_cagr <= 0.01 else 6 if dilution_cagr <= 0.02 else 0
    reinvestment_score = round(min(100, reinvestment_score), 1)

    capital_allocation_score = 60.0
    if not np.isnan(dilution_cagr):
        capital_allocation_score += 12 if dilution_cagr <= 0 else 8 if dilution_cagr <= 0.01 else 2 if dilution_cagr <= 0.02 else -12
    if not np.isnan(net_debt_to_fcf):
        capital_allocation_score += 10 if net_debt_to_fcf <= 1 else 4 if net_debt_to_fcf <= 2 else -12 if net_debt_to_fcf > 4 else 0
    if acquisitions is not None and adjusted_fcf is not None:
        common = acquisitions.index.intersection(adjusted_fcf.index)
        heavy_years = 0
        valid_years = 0
        for dt in common:
            acq = abs(safe(acquisitions.get(dt), 0))
            cf = abs(safe(adjusted_fcf.get(dt), 0))
            if cf > 0:
                valid_years += 1
                if acq > cf:
                    heavy_years += 1
        if valid_years >= 2 and heavy_years >= 2:
            capital_allocation_score -= 25
    capital_allocation_score = round(max(0, min(100, capital_allocation_score)), 1)

    # --- Hard gates that can be measured reliably ---
    hard_gate_failures = []
    hard_gate_warnings = []

    if not specialist_sector and not quantitative_moat_pass:
        hard_gate_failures.append("quantitative moat evidence is not strong enough")

    if not specialist_sector:
        if not np.isnan(net_debt_to_fcf) and net_debt_to_fcf > 4:
            hard_gate_failures.append("excessive net debt relative to FCF")
        if not np.isnan(interest_coverage) and interest_coverage < 2 and (np.isnan(net_debt_to_fcf) or net_debt_to_fcf > 1):
            hard_gate_failures.append("weak interest coverage")
        if not np.isnan(positive_fcf_share) and positive_fcf_share < 0.75:
            hard_gate_failures.append("persistent weak/negative adjusted FCF")
        if not np.isnan(latest_fcf) and latest_fcf <= 0:
            hard_gate_failures.append("latest adjusted FCF is not positive")
        if not np.isnan(positive_income_share) and positive_income_share < 0.75:
            hard_gate_failures.append("turnaround/depressed earnings profile")
        if not np.isnan(roic_trend) and roic_trend <= -0.05:
            hard_gate_warnings.append("ROIC has deteriorated materially")

    cyclical_keyword = any(term in industry.lower() for term in CYCLICAL_TERMS)
    revenue_cv = _series_cv(revenue)
    if cyclical_keyword and not np.isnan(revenue_cv) and revenue_cv > 0.25:
        hard_gate_failures.append("strongly cyclical earnings/revenue profile")

    acquisition_heavy = False
    if acquisitions is not None and adjusted_fcf is not None:
        common = acquisitions.index.intersection(adjusted_fcf.index)
        ratios = []
        for dt in common:
            cf = abs(safe(adjusted_fcf.get(dt), 0))
            if cf > 0:
                ratios.append(abs(safe(acquisitions.get(dt), 0)) / cf)
        acquisition_heavy = len(ratios) >= 2 and sum(r > 1.0 for r in ratios) >= 2
        if acquisition_heavy:
            hard_gate_failures.append("serial acquisition dependence")

    # Items Yahoo cannot safely prove either way.
    qualitative_review_items = [
        "structural moat mechanism and durability",
        "technology disruption risk",
        "key-person dependency",
        "customer concentration",
        "geographic concentration",
        "supplier concentration",
        "governance/minority shareholder protections",
        "regulatory dependence",
        "market-share trend",
    ]

    # --- DCF / margin of safety ---
    # Financial statements and secondary-listing share prices can use different
    # currencies. Convert DCF/share into the quote currency before comparing with
    # market price. If FX cannot be verified, valuation must fail safe to WAIT.
    quote_currency = str(fund.get("quote_currency") or "")
    financial_currency = str(fund.get("financial_currency") or "")
    valuation_fx = _financial_to_quote_fx(financial_currency, quote_currency)
    valuation_fx_rate = safe(valuation_fx.get("rate"))
    valuation_fx_status = str(valuation_fx.get("status") or "FX UNAVAILABLE")
    valuation_fx_pair = str(valuation_fx.get("pair") or "")

    terminal_base = 0.025
    if base_growth <= 0.02:
        terminal_base = 0.015
    elif moat_confidence == "HIGH" and base_growth >= 0.07:
        terminal_base = 0.028

    bear_growth = min(base_growth * 0.45, 0.045)
    bull_growth = min(base_growth + 0.025, 0.14)
    base_value = _dcf_per_share(normalized_fcf_per_share, base_growth, terminal_base, BASE_REQUIRED_RETURN, moat_confidence)
    bear_value = _dcf_per_share(normalized_fcf_per_share, bear_growth, max(0.01, terminal_base - 0.01), BASE_REQUIRED_RETURN, "LOW")
    bull_value = _dcf_per_share(normalized_fcf_per_share, bull_growth, min(0.032, terminal_base + 0.004), BASE_REQUIRED_RETURN, "HIGH")

    if valuation_fx_status == "PASS" and not np.isnan(valuation_fx_rate) and valuation_fx_rate > 0:
        base_value = base_value * valuation_fx_rate if not np.isnan(base_value) else base_value
        bear_value = bear_value * valuation_fx_rate if not np.isnan(bear_value) else bear_value
        bull_value = bull_value * valuation_fx_rate if not np.isnan(bull_value) else bull_value
    else:
        # Never compare financial-currency intrinsic value with a price quoted in
        # another/unverified currency.  Missing FX therefore cannot become BUY.
        base_value = np.nan
        bear_value = np.nan
        bull_value = np.nan

    base_mos = 0.15 if moat_confidence == "HIGH" else 0.25 if moat_confidence == "MEDIUM" else 0.35
    valuation_uncertainty_pct = np.nan
    if not np.isnan(base_value) and base_value > 0 and not np.isnan(bear_value):
        valuation_uncertainty_pct = max(0.0, (base_value - bear_value) / base_value * 100)
    uncertainty_add = 0.0 if np.isnan(valuation_uncertainty_pct) else min(0.10, valuation_uncertainty_pct / 100 * 0.20)
    evidence_add = 0.05 if evidence_years < 5 else 0.0
    required_mos = min(0.45, base_mos + uncertainty_add + evidence_add)

    mos_base = np.nan if np.isnan(base_value) or base_value <= 0 or price <= 0 else 1 - price / base_value
    mos_bear = np.nan if np.isnan(bear_value) or bear_value <= 0 or price <= 0 else 1 - price / bear_value

    valuation_gate_pass = (
        valuation_fx_status == "PASS"
        and not np.isnan(mos_base)
        and mos_base >= required_mos
        and not np.isnan(mos_bear)
        and mos_bear >= 0
    )

    valuation_score = 0.0
    if not np.isnan(mos_base):
        valuation_score = float(np.clip((mos_base + 0.25) / 0.70 * 100, 0, 100))

    quality_score = round(
        0.35 * moat_score
        + 0.20 * resilience_score
        + 0.20 * reinvestment_score
        + 0.15 * capital_allocation_score
        + 0.10 * cash_quality_score,
        1,
    )

    sector_model = "Generic"
    sector_review_required = False
    if is_insurer:
        sector_model = "Insurance specialist"
        sector_review_required = True
        hard_gate_warnings.append("use insurer-specific underwriting, reserves, float and capital review")
    elif is_reit:
        sector_model = "REIT specialist"
        sector_review_required = True
        hard_gate_warnings.append("use AFFO/FFO, occupancy, lease quality, NAV/cap-rate and debt review")
    elif is_financial:
        sector_model = "Bank/financial specialist"
        sector_review_required = True
        hard_gate_warnings.append("use ROTCE/ROE, CET1, credit quality, deposit franchise and tangible-book review")

    hard_gate_pass = len(hard_gate_failures) == 0

    # Preliminary model action. Specialist sectors stay WAIT until specialist review.
    if not hard_gate_pass:
        preliminary_action = "PASS"
    elif sector_review_required:
        preliminary_action = "WAIT"
    elif valuation_gate_pass:
        preliminary_action = "BUY CANDIDATE"
    else:
        preliminary_action = "WAIT"

    if not hard_gate_pass:
        action_reason = "; ".join(hard_gate_failures)
    elif sector_review_required:
        action_reason = "specialist sector review required before a buy decision"
    elif not valuation_gate_pass:
        action_reason = "quality may qualify, but valuation / bear-case margin of safety is insufficient"
    else:
        action_reason = "quantitative hard gates and DCF margin-of-safety gate passed; complete manual review before buying"

    if sector_review_required:
        base_value = np.nan
        bear_value = np.nan
        bull_value = np.nan
        mos_base = np.nan
        mos_bear = np.nan
        valuation_gate_pass = False

    score_cap_reason = "; ".join(hard_gate_failures + hard_gate_warnings)
    long_term_score = quality_score if hard_gate_pass else min(quality_score, 59.0)
    long_term_label = (
        "ELITE" if long_term_score >= 90 else
        "STRONG" if long_term_score >= 80 else
        "QUALITY" if long_term_score >= 70 else
        "DEVELOPING" if long_term_score >= 60 else
        "PASS"
    )

    return {
        "long_term_score": round(long_term_score, 1),
        "long_term_raw_score": round(quality_score, 1),
        "long_term_label": long_term_label,
        "investment_quality_score": round(quality_score, 1),
        "moat_score": moat_score,
        "moat_confidence": moat_confidence,
        "structural_moat_status": structural_moat_status,
        "moat_mechanisms": ", ".join(moat_mechanisms) if moat_mechanisms else "Needs manual verification",
        "quantitative_moat_pass": bool(quantitative_moat_pass),
        "resilience_score": resilience_score,
        "reinvestment_score": reinvestment_score,
        "capital_allocation_score": capital_allocation_score,
        "cash_quality_score": cash_quality_score,
        "hard_gate_pass": bool(hard_gate_pass),
        "hard_gate_failures": "; ".join(hard_gate_failures),
        "hard_gate_warnings": "; ".join(hard_gate_warnings),
        "qualitative_review_items": "; ".join(qualitative_review_items),
        "preliminary_action": preliminary_action,
        "action_reason": action_reason,
        "valuation_gate_pass": bool(valuation_gate_pass),
        "required_margin_of_safety_pct": round(required_mos * 100, 1),
        "margin_of_safety_base_pct": None if np.isnan(mos_base) else round(mos_base * 100, 1),
        "margin_of_safety_bear_pct": None if np.isnan(mos_bear) else round(mos_bear * 100, 1),
        "dcf_bear": None if np.isnan(bear_value) else round(bear_value, 2),
        "dcf_base": None if np.isnan(base_value) else round(base_value, 2),
        "dcf_bull": None if np.isnan(bull_value) else round(bull_value, 2),
        "dcf_base_growth_pct": round(base_growth * 100, 1),
        "dcf_bear_growth_pct": round(bear_growth * 100, 1),
        "dcf_bull_growth_pct": round(bull_growth * 100, 1),
        "dcf_terminal_growth_pct": round(terminal_base * 100, 1),
        "dcf_discount_rate_pct": round(BASE_REQUIRED_RETURN * 100, 1),
        "quote_currency": quote_currency,
        "financial_currency": financial_currency,
        "valuation_model_version": VALUATION_MODEL_VERSION,
        "valuation_fx_status": valuation_fx_status,
        "valuation_fx_rate": None if np.isnan(valuation_fx_rate) else float(valuation_fx_rate),
        "valuation_fx_pair": valuation_fx_pair,
        "quote_scale": quote_scale,
        "valuation_uncertainty_pct": None if np.isnan(valuation_uncertainty_pct) else round(valuation_uncertainty_pct, 1),
        "investment_valuation_score": round(valuation_score, 1),
        "normalized_fcf_per_share": None if np.isnan(normalized_fcf_per_share) else round(normalized_fcf_per_share, 4),
        "evidence_score": min(10, evidence_years + (2 if not np.isnan(roic) else 0) + (1 if not np.isnan(dilution_cagr) else 0)),
        "evidence_years": evidence_years,
        "elite_gate_pass": bool(hard_gate_pass and quality_score >= 85 and not sector_review_required),
        "score_cap_reason": score_cap_reason,
        "revenue_cagr_pct": None if np.isnan(revenue_cagr) else round(revenue_cagr * 100, 1),
        "earnings_cagr_pct": None if np.isnan(earnings_cagr) else round(earnings_cagr * 100, 1),
        "fcf_cagr_pct": None if np.isnan(fcf_cagr) else round(fcf_cagr * 100, 1),
        "fcf_per_share_cagr_pct": None if np.isnan(fcf_per_share_cagr) else round(fcf_per_share_cagr * 100, 1),
        "positive_fcf_years_pct": None if np.isnan(positive_fcf_share) else round(positive_fcf_share * 100, 0),
        "roic_pct": None if np.isnan(roic) else round(roic * 100, 1),
        "roic_median_pct": None if np.isnan(roic_median) else round(roic_median * 100, 1),
        "roic_trend_pct": None if np.isnan(roic_trend) else round(roic_trend * 100, 1),
        "roe_pct": None if np.isnan(roe) else round(roe * 100, 1),
        "operating_margin_pct": None if np.isnan(operating_margin) else round(operating_margin * 100, 1),
        "gross_margin_pct": None if np.isnan(gross_margin) else round(gross_margin * 100, 1),
        "dilution_cagr_pct": None if np.isnan(dilution_cagr) else round(dilution_cagr * 100, 2),
        "net_debt_to_fcf": None if np.isnan(net_debt_to_fcf) else round(net_debt_to_fcf, 2),
        "interest_coverage": None if np.isnan(interest_coverage) else round(interest_coverage, 1),
        "current_ratio": None if np.isnan(current_ratio) else round(current_ratio, 2),
        "book_value_per_share_cagr_pct": None if np.isnan(book_value_per_share_cagr) else round(book_value_per_share_cagr * 100, 1),
        "acquisition_heavy": bool(acquisition_heavy),
        "sector_model": sector_model,
        "sector_review_required": sector_review_required,
        "sector": sector,
        "industry": industry,
    }


def long_term_entry_score(symbol: str, price: float, valuation_score: float, tech: dict | None = None) -> dict:
    """Compatibility wrapper.

    Long-term entry quality is now valuation-led. Technical position is context
    only and cannot turn an expensive stock into a buy.
    """
    score = float(np.clip(safe(valuation_score, 0), 0, 100))
    label = "EXCELLENT" if score >= 80 else "ATTRACTIVE" if score >= 65 else "FAIR" if score >= 50 else "WAIT"
    return {"long_term_entry_score": round(score, 1), "long_term_entry_label": label}


def strategy_label(
    trade_signal: str,
    one_year_score: float,
    valuation_score: float,
    long_term_score: float,
    long_term_entry: float,
) -> str:
    active_trade = trade_signal in ("ACTIONABLE", "ELITE")
    if active_trade and long_term_score >= 80 and long_term_entry >= 65:
        return "TRADE + LONG-TERM QUALITY"
    if active_trade:
        return "TRADE"
    if long_term_score >= 80 and long_term_entry >= 65:
        return "LONG-TERM QUALITY"
    if long_term_score >= 70:
        return "LONG-TERM WATCH"
    return "WAIT"
