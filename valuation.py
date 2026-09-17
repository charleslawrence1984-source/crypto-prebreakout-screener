import math
import re
import numpy as np
import yfinance as yf


def _safe(v, default=np.nan):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _pct(v):
    x = _safe(v)
    return None if np.isnan(x) else x * 100


def _search_metadata(symbol: str) -> dict:
    """Best-effort lightweight metadata fallback when quoteSummary/info is unavailable."""
    try:
        search = yf.Search(symbol, max_results=5)
        quotes = getattr(search, "quotes", None) or []
    except Exception:
        return {}

    symbol_u = str(symbol).upper()
    match = None
    for q in quotes:
        if str(q.get("symbol") or "").upper() == symbol_u:
            match = q
            break
    if match is None and quotes:
        match = quotes[0]
    if not match:
        return {}

    return {
        "longName": match.get("longname") or match.get("longName"),
        "shortName": match.get("shortname") or match.get("shortName"),
        "sector": match.get("sectorDisp") or match.get("sector"),
        "industry": match.get("industryDisp") or match.get("industry"),
        "exchange": match.get("exchange") or match.get("exchDisp"),
        "fullExchangeName": match.get("exchDisp") or match.get("exchange"),
        "quoteType": match.get("quoteType"),
    }


def _exchange_country(symbol: str, info: dict) -> str:
    s = str(symbol).upper()
    suffix_map = {
        ".L": "UK",
        ".TO": "Canada",
        ".V": "Canada",
        ".MC": "Spain",
        ".DE": "Germany",
        ".PA": "France",
        ".AS": "Netherlands",
        ".MI": "Italy",
        ".AX": "Australia",
        ".T": "Japan",
    }
    for suffix, country in suffix_map.items():
        if s.endswith(suffix):
            return country

    exchange = str(info.get("exchange") or info.get("fullExchangeName") or "").upper()
    if any(x in exchange for x in ["NASDAQ", "NYSE", "AMEX", "ARCA", "NMS", "NGM", "NCM", "NYQ"]):
        return "US"
    if any(x in exchange for x in ["LSE", "LONDON"]):
        return "UK"
    if any(x in exchange for x in ["TSX", "TORONTO", "TSXV", "VENTURE"]):
        return "Canada"
    if any(x in exchange for x in ["BME", "MADRID"]):
        return "Spain"
    if "XETRA" in exchange or "FRANKFURT" in exchange:
        return "Germany"
    if "PARIS" in exchange:
        return "France"
    if "AMSTERDAM" in exchange:
        return "Netherlands"
    if "MILAN" in exchange:
        return "Italy"
    if "ASX" in exchange:
        return "Australia"
    if "TOKYO" in exchange:
        return "Japan"

    # Yahoo-style symbols for non-US primary listings normally carry a suffix.
    # A plain symbol from the public universes is therefore most likely US.
    if re.fullmatch(r"[A-Z0-9-]+", s):
        return "US"
    return "Other / Unknown"


def fundamental_analysis(symbol: str, price: float):
    t = yf.Ticker(symbol)
    try:
        info = t.info or {}
    except Exception:
        info = {}

    # Lightweight fallbacks. These are deliberately best-effort and never
    # substitute fabricated values when Yahoo does not provide a field.
    try:
        fast = t.fast_info
    except Exception:
        fast = {}

    if not info.get("longName") and not info.get("shortName"):
        search_info = _search_metadata(symbol)
        for k, v in search_info.items():
            if v not in (None, "") and not info.get(k):
                info[k] = v

    market_cap = _safe(info.get("marketCap"))
    if np.isnan(market_cap):
        try:
            market_cap = _safe(fast.get("market_cap"))
        except Exception:
            pass

    revenue_growth = _pct(info.get("revenueGrowth"))
    earnings_growth = _pct(info.get("earningsGrowth"))
    margin = _pct(info.get("profitMargins"))
    debt_equity = _safe(info.get("debtToEquity"))
    fcf = _safe(info.get("freeCashflow"))
    div_yield = _pct(info.get("dividendYield"))
    payout = _pct(info.get("payoutRatio"))
    target = _safe(info.get("targetMeanPrice"))
    analysts = _safe(info.get("numberOfAnalystOpinions"), 0)
    target_upside = (target / price - 1) * 100 if not np.isnan(target) and price > 0 else np.nan

    forward_pe = _safe(info.get("forwardPE"))
    trailing_pe = _safe(info.get("trailingPE"))
    peg = _safe(info.get("pegRatio"))
    if np.isnan(peg):
        peg = _safe(info.get("trailingPegRatio"))
    price_sales = _safe(info.get("priceToSalesTrailing12Months"))
    ev_ebitda = _safe(info.get("enterpriseToEbitda"))
    fcf_yield = (fcf / market_cap) * 100 if not np.isnan(fcf) and not np.isnan(market_cap) and market_cap > 0 else np.nan

    quality = 0.0

    if not np.isnan(market_cap):
        quality += 5 if market_cap >= 10e9 else 4 if market_cap >= 3e9 else 2.5 if market_cap >= 1e9 else 0

    if revenue_growth is not None:
        quality += 15 if revenue_growth >= 20 else 12 if revenue_growth >= 10 else 9 if revenue_growth >= 5 else 4 if revenue_growth > 0 else 0

    if earnings_growth is not None:
        quality += 15 if earnings_growth >= 30 else 13 if earnings_growth >= 20 else 9 if earnings_growth >= 10 else 5 if earnings_growth > 0 else 0

    if margin is not None:
        quality += 10 if margin >= 20 else 8 if margin >= 10 else 5 if margin >= 5 else 2 if margin > 0 else 0

    if not np.isnan(debt_equity):
        quality += 10 if debt_equity <= 50 else 8 if debt_equity <= 100 else 6 if debt_equity <= 150 else 3 if debt_equity <= 250 else 0
    else:
        quality += 4

    if not np.isnan(fcf):
        quality += 10 if fcf > 0 else 0
    else:
        quality += 3

    if not np.isnan(target_upside):
        quality += 7 if target_upside >= 25 else 6 if target_upside >= 15 else 4.5 if target_upside >= 10 else 2 if target_upside > 0 else 0

    quality += 3 if analysts >= 10 else 2 if analysts >= 5 else 1 if analysts >= 2 else 0

    shareholder = 0.0
    if div_yield is None or np.isnan(div_yield):
        shareholder += 2.5
    elif 2 <= div_yield <= 6:
        shareholder += 3
    elif 0 < div_yield < 2:
        shareholder += 2
    elif div_yield > 8:
        shareholder += 0.5

    if payout is None or np.isnan(payout):
        shareholder += 1.5
    elif 0 <= payout <= 65:
        shareholder += 2
    elif payout <= 85:
        shareholder += 1

    quality += min(shareholder, 5)

    valuation_points = 0.0
    valuation_max = 0.0

    pe_used = forward_pe if not np.isnan(forward_pe) and forward_pe > 0 else trailing_pe
    if not np.isnan(pe_used) and pe_used > 0:
        valuation_max += 5
        valuation_points += 5 if pe_used <= 15 else 4 if pe_used <= 22 else 3 if pe_used <= 30 else 1.5 if pe_used <= 40 else 0

    if not np.isnan(peg) and peg > 0:
        valuation_max += 5
        valuation_points += 5 if peg <= 1 else 4 if peg <= 1.5 else 2.5 if peg <= 2 else 1 if peg <= 3 else 0

    if not np.isnan(price_sales) and price_sales > 0:
        valuation_max += 4
        valuation_points += 4 if price_sales <= 2 else 3 if price_sales <= 4 else 1.5 if price_sales <= 7 else 0.5 if price_sales <= 10 else 0

    if not np.isnan(fcf_yield):
        valuation_max += 4
        valuation_points += 4 if fcf_yield >= 6 else 3 if fcf_yield >= 4 else 1.5 if fcf_yield >= 2 else 0.5 if fcf_yield > 0 else 0

    if not np.isnan(ev_ebitda) and ev_ebitda > 0:
        valuation_max += 2
        valuation_points += 2 if ev_ebitda <= 10 else 1.5 if ev_ebitda <= 15 else 0.75 if ev_ebitda <= 22 else 0

    if valuation_max >= 8:
        valuation_score = round(min(20, valuation_points / valuation_max * 20), 1)
        valuation_data = "Good"
    else:
        valuation_score = 10.0
        valuation_data = "Limited" if valuation_max > 0 else "Unavailable"

    if valuation_data != "Good":
        valuation_label = "Limited data"
    elif valuation_score >= 16:
        valuation_label = "Attractive"
    elif valuation_score >= 12:
        valuation_label = "Fair"
    elif valuation_score >= 8:
        valuation_label = "Full"
    else:
        valuation_label = "Expensive"

    flags = []
    if not np.isnan(pe_used) and pe_used > 45:
        flags.append("high P/E")
    if not np.isnan(peg) and peg > 3:
        flags.append("high PEG")
    if not np.isnan(price_sales) and price_sales > 10:
        flags.append("high P/S")
    if not np.isnan(fcf_yield) and fcf_yield < 1:
        flags.append("low FCF yield")

    hold_score = round(min(100, quality + valuation_score), 1)

    quote_currency = info.get("currency") or ""
    financial_currency = info.get("financialCurrency") or ""
    exchange = info.get("fullExchangeName") or info.get("exchange") or ""
    try:
        if not quote_currency:
            quote_currency = fast.get("currency") or ""
        if not exchange:
            exchange = fast.get("exchange") or ""
    except Exception:
        pass

    metadata_complete = bool(
        (info.get("longName") or info.get("shortName"))
        and info.get("sector")
        and info.get("industry")
    )

    return {
        "hold_score": hold_score,
        "quality_score": round(min(80, quality), 1),
        "valuation_score": valuation_score,
        "valuation_label": valuation_label,
        "valuation_data": valuation_data,
        "valuation_warning": ", ".join(flags),
        "name": info.get("longName") or info.get("shortName") or symbol,
        "market_cap": market_cap,
        "revenue_growth": revenue_growth,
        "earnings_growth": earnings_growth,
        "profit_margin": margin,
        "debt_equity": debt_equity,
        "free_cash_flow": fcf,
        "dividend_yield": div_yield,
        "payout_ratio": payout,
        "analyst_target": target,
        "analyst_count": int(analysts) if not np.isnan(analysts) else 0,
        "analyst_upside": target_upside,
        "forward_pe": forward_pe,
        "trailing_pe": trailing_pe,
        "pe_used": pe_used,
        "peg": peg,
        "price_sales": price_sales,
        "ev_ebitda": ev_ebitda,
        "fcf_yield": fcf_yield,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "business_summary": info.get("longBusinessSummary") or "",
        "return_on_equity": _safe(info.get("returnOnEquity")),
        "return_on_assets": _safe(info.get("returnOnAssets")),
        "current_ratio": _safe(info.get("currentRatio")),
        "quote_currency": quote_currency,
        "financial_currency": financial_currency,
        "exchange": exchange,
        "exchange_country": _exchange_country(symbol, {**info, "exchange": exchange}),
        "metadata_complete": metadata_complete,
    }
