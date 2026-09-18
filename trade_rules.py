"""Approved Trade Search rulebook calculations.

This module is deliberately independent from the long-term Investment Search model.
It contains only the price/fundamental calculations used by the Trade Search tab.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd


EXCLUDED_SECTORS = {"financial services", "financials", "real estate"}


def _number(value: Any, default: float = np.nan) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float, high: float) -> float:
    return float(np.clip(value, low, high))


def _linear(value: float, low: float, high: float, points: float) -> float:
    if not math.isfinite(value) or high <= low:
        return 0.0
    return points * _clip((value - low) / (high - low), 0.0, 1.0)


def _series(frame: pd.DataFrame, names: Iterable[str]) -> pd.Series:
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    lookup = {str(index).strip().lower(): index for index in frame.index}
    for name in names:
        index = lookup.get(name.strip().lower())
        if index is not None:
            result = pd.to_numeric(frame.loc[index], errors="coerce").dropna()
            try:
                result.index = pd.to_datetime(result.index)
                result = result.sort_index(ascending=False)
            except Exception:
                pass
            return result.astype(float)
    return pd.Series(dtype=float)


def _latest(series: pd.Series) -> float:
    return _number(series.iloc[0]) if series is not None and len(series) else np.nan


def _annual_fcf(cashflow: pd.DataFrame) -> pd.Series:
    direct = _series(cashflow, ["Free Cash Flow"])
    if len(direct):
        return direct
    operating = _series(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    capex = _series(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    if not len(operating) or not len(capex):
        return pd.Series(dtype=float)
    aligned = pd.concat([operating.rename("operating"), capex.rename("capex")], axis=1).dropna()
    if aligned.empty:
        return pd.Series(dtype=float)
    # Yahoo normally reports capex as a negative cash-flow item.
    return aligned["operating"] + aligned["capex"]


def _is_yahoo_rate_limit_error(exc: Exception) -> bool:
    """Keep provider throttling distinct from genuinely missing company data."""
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return (
        "yfratelimiterror" in name
        or "too many requests" in message
        or "rate limit" in message
        or "http 429" in message
    )


def _ticker_mapping(ticker: Any, attribute: str) -> dict[str, Any]:
    """Read a Yahoo mapping without allowing one failed endpoint to abort a scan."""
    try:
        value = getattr(ticker, attribute)
        return value if isinstance(value, dict) else dict(value or {})
    except Exception as exc:
        if _is_yahoo_rate_limit_error(exc):
            raise
        return {}


def _ticker_statement(
    ticker: Any,
    attributes: Iterable[str],
    methods: Iterable[tuple[str, dict[str, Any]]],
) -> pd.DataFrame:
    """Try yfinance's equivalent statement endpoints in a stable order."""
    for attribute in attributes:
        try:
            value = getattr(ticker, attribute)
            if isinstance(value, pd.DataFrame) and not value.empty:
                return value
        except Exception as exc:
            if _is_yahoo_rate_limit_error(exc):
                raise
            continue
    for method_name, kwargs in methods:
        try:
            method = getattr(ticker, method_name)
            value = method(**kwargs)
            if isinstance(value, pd.DataFrame) and not value.empty:
                return value
        except Exception as exc:
            if _is_yahoo_rate_limit_error(exc):
                raise
            continue
    return pd.DataFrame()


def _growth(latest: float, previous: float) -> float:
    if not math.isfinite(latest) or not math.isfinite(previous) or previous <= 0:
        return np.nan
    return latest / previous - 1.0


def _statement_growth(frame: pd.DataFrame, names: list[str]) -> tuple[float, str]:
    quarterly = _series(frame, names)
    if len(quarterly) >= 8:
        return _growth(float(quarterly.iloc[:4].sum()), float(quarterly.iloc[4:8].sum())), "TTM"
    return np.nan, "UNAVAILABLE"


def _wilder_rsi(close: pd.Series, periods: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / periods, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / periods, adjust=False).mean()
    relative = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + relative)).fillna(50.0)


def _atr(frame: pd.DataFrame, periods: int = 20) -> pd.Series:
    previous = frame["Close"].shift(1)
    true_range = pd.concat(
        [
            frame["High"] - frame["Low"],
            (frame["High"] - previous).abs(),
            (frame["Low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / periods, adjust=False).mean()


def _pivot_values(series: pd.Series, kind: str, wing: int = 2) -> list[tuple[int, float]]:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    output: list[tuple[int, float]] = []
    for index in range(wing, len(values) - wing):
        window = values[index - wing : index + wing + 1]
        if not np.isfinite(values[index]) or not np.isfinite(window).all():
            continue
        if kind == "low" and values[index] == np.min(window):
            output.append((index, float(values[index])))
        elif kind == "high" and values[index] == np.max(window):
            output.append((index, float(values[index])))
    return output


def _clusters(
    pivots: list[tuple[int, float]], atr_value: float, min_separation: int = 10
) -> list[dict[str, float]]:
    groups: list[dict[str, float]] = []
    if not math.isfinite(atr_value) or atr_value <= 0:
        return groups
    for left in range(len(pivots)):
        for right in range(left + 1, len(pivots)):
            index_a, value_a = pivots[left]
            index_b, value_b = pivots[right]
            if abs(index_b - index_a) < min_separation or abs(value_b - value_a) > atr_value:
                continue
            groups.append(
                {
                    "lower": min(value_a, value_b),
                    "upper": max(value_a, value_b),
                    "median": float(np.median([value_a, value_b])),
                    "last_index": float(max(index_a, index_b)),
                }
            )
    return groups


def market_regime_score(benchmark: pd.DataFrame | None) -> tuple[float, str]:
    if benchmark is None or len(benchmark) < 221:
        return 7.5, "NEUTRAL — BENCHMARK DATA LIMITED"
    close = pd.to_numeric(benchmark["Close"], errors="coerce").dropna()
    if len(close) < 221:
        return 7.5, "NEUTRAL — BENCHMARK DATA LIMITED"
    sma = close.rolling(200).mean()
    price = float(close.iloc[-1])
    current = float(sma.iloc[-1])
    previous = float(sma.iloc[-21])
    slope = current / previous - 1.0 if previous > 0 else 0.0
    if price > current and slope >= 0.005:
        return 15.0, "BULL"
    if price < current and slope <= -0.005:
        return 0.0, "BEAR"
    return 7.5, "NEUTRAL"


def evaluate_price_setup(
    frame: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Evaluate the approved technical setup on completed daily bars.

    A crossover on the last bar is a confirmed setup awaiting the following open.
    A crossover on the penultimate bar can validate the latest bar's opening entry.
    """
    result: dict[str, Any] = {
        "technical_state": "BLOCKED",
        "technical_reason": "PRICE DATA INCOMPLETE",
    }
    if frame is None or frame.empty:
        return result
    data = frame.copy()
    data.columns = [str(column).title() for column in data.columns]
    needed = ["Open", "High", "Low", "Close", "Volume"]
    if not set(needed).issubset(data.columns):
        return result
    for column in needed:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=needed).sort_index()
    if len(data) < 252:
        result["technical_reason"] = f"PRICE HISTORY {len(data)}/252 SESSIONS"
        return result

    data["ATR20"] = _atr(data, 20)
    data["SMA180"] = data["Close"].rolling(180).mean()
    data["SMA200"] = data["Close"].rolling(200).mean()
    data["RSI14"] = _wilder_rsi(data["Close"], 14)
    data["MACD"] = data["Close"].ewm(span=12, adjust=False).mean() - data["Close"].ewm(span=26, adjust=False).mean()
    data["MACD_SIGNAL"] = data["MACD"].ewm(span=9, adjust=False).mean()
    data["ZONE_HIGH"] = data[["SMA180", "SMA200"]].max(axis=1) + 0.5 * data["ATR20"]
    data["ZONE_LOW"] = data[["SMA180", "SMA200"]].min(axis=1) - 0.5 * data["ATR20"]

    cross = (data["MACD"] > data["MACD_SIGNAL"]) & (
        data["MACD"].shift(1) <= data["MACD_SIGNAL"].shift(1)
    )
    signal_index: int | None = None
    entry_index: int | None = None
    for candidate in (len(data) - 2, len(data) - 1):
        if candidate >= 0 and bool(cross.iloc[candidate]):
            signal_index = candidate
            entry_index = candidate + 1 if candidate + 1 < len(data) else None
            break

    latest = data.iloc[-1]
    latest_rsi = float(latest["RSI14"])
    recent_rsi = data["RSI14"].iloc[-10:]
    latest_sub30 = recent_rsi[recent_rsi < 30]
    if signal_index is None:
        if latest_rsi < 35 or len(latest_sub30):
            # WATCH is a trend/pullback setup, not an unrestricted oversold list.
            slopes = [float(latest[column] / data[column].iloc[-21] - 1)
                      for column in ("SMA180", "SMA200")]
            if any(not math.isfinite(slope) or slope < 0.005 for slope in slopes):
                result["technical_reason"] = "SMA180 AND SMA200 ARE NOT BOTH RISING BY 0.5%"
                return result
            recent = data.iloc[-10:]
            contact = ((recent["Low"] <= recent["ZONE_HIGH"])
                       & (recent["High"] >= recent["ZONE_LOW"])).any()
            if not contact:
                result["technical_reason"] = "NO PRICE CONTACT WITH THE MA SUPPORT ZONE"
                return result
            result.update(
                {
                    "technical_state": "WATCH",
                    "technical_reason": "RSI EXHAUSTION ACTIVE — NO CURRENT MACD CROSSOVER",
                    "rsi": round(latest_rsi, 2),
                    "price": float(latest["Close"]),
                }
            )
        else:
            result["technical_reason"] = "NO ACTIVE RSI EXHAUSTION OR CURRENT CROSSOVER"
        return result

    signal = data.iloc[signal_index]
    if signal_index < 220:
        result["technical_reason"] = "INSUFFICIENT SMA SLOPE HISTORY"
        return result
    sma180_slope = float(signal["SMA180"] / data["SMA180"].iloc[signal_index - 20] - 1)
    sma200_slope = float(signal["SMA200"] / data["SMA200"].iloc[signal_index - 20] - 1)
    if sma180_slope < 0.005 or sma200_slope < 0.005:
        result["technical_reason"] = "SMA180 AND SMA200 ARE NOT BOTH RISING BY 0.5%"
        return result

    start = max(0, signal_index - 9)
    rsi_window = data["RSI14"].iloc[start : signal_index + 1]
    sub30_positions = np.flatnonzero(rsi_window.to_numpy() < 30)
    if not len(sub30_positions):
        result["technical_reason"] = "NO SUB-30 RSI EVENT WITHIN 10 SESSIONS"
        return result
    exhaustion_index = start + int(sub30_positions[-1])
    reclaimed = False
    for index in range(exhaustion_index + 1, signal_index + 1):
        if data["RSI14"].iloc[index - 1] <= 30 < data["RSI14"].iloc[index]:
            reclaimed = True
            break
    if not reclaimed:
        result["technical_reason"] = "RSI HAS NOT RECLAIMED 30"
        return result

    contact = (
        (data["Low"].iloc[exhaustion_index : signal_index + 1] <= data["ZONE_HIGH"].iloc[exhaustion_index : signal_index + 1])
        & (data["High"].iloc[exhaustion_index : signal_index + 1] >= data["ZONE_LOW"].iloc[exhaustion_index : signal_index + 1])
    ).any()
    if not bool(contact):
        result["technical_reason"] = "NO PRICE CONTACT WITH THE MA SUPPORT ZONE"
        return result

    atr_value = float(signal["ATR20"])
    zone_low = float(signal["ZONE_LOW"])
    zone_high = float(signal["ZONE_HIGH"])
    confirmation_close = float(signal["Close"])
    history20 = data["Low"].iloc[max(0, signal_index - 19) : signal_index + 1]
    structural_low = min(zone_low, float(history20.min()))
    stop = structural_low - 0.5 * atr_value

    entry = float(data["Open"].iloc[entry_index]) if entry_index is not None else np.nan
    entry_date = data.index[entry_index] if entry_index is not None else pd.NaT
    gap_atr = (entry - confirmation_close) / atr_value if entry_index is not None and atr_value > 0 else np.nan
    if entry_index is not None:
        if abs(gap_atr) > 0.5:
            result["technical_reason"] = "OPENING GAP EXCEEDS 0.5 ATR"
            return result
        if entry <= stop:
            result["technical_reason"] = "OPEN IS AT OR BELOW PLANNED INVALIDATION"
            return result
        if entry > zone_high + atr_value:
            result["technical_reason"] = "ENTRY IS MORE THAN 1.0 ATR ABOVE MA ZONE"
            return result
        stop_distance = (entry - stop) / entry
        if stop_distance > 0.10:
            result["technical_reason"] = "STRUCTURAL STOP DISTANCE EXCEEDS 10%"
            return result
    else:
        stop_distance = (confirmation_close - stop) / confirmation_close

    prior = data.iloc[: signal_index + 1]
    confirmed_prior = prior.iloc[:-2] if len(prior) > 4 else prior.iloc[0:0]
    resistance = _clusters(_pivot_values(confirmed_prior["High"], "high"), atr_value)
    reference_entry = entry if math.isfinite(entry) else confirmation_close
    overhead = [cluster for cluster in resistance if cluster["lower"] > reference_entry]
    if overhead:
        chosen = min(overhead, key=lambda cluster: cluster["lower"])
        target_source = "TWO-TOUCH RESISTANCE"
        target = chosen["lower"] - 0.25 * atr_value
    else:
        target_source = "52-WEEK HIGH FALLBACK"
        target = float(prior["High"].tail(252).max()) - 0.25 * atr_value

    target_upside = (target - reference_entry) / reference_entry
    risk = reference_entry - stop
    reward_risk = (target - reference_entry) / risk if risk > 0 else np.nan
    if target_upside < 0.10:
        result["technical_reason"] = "TECHNICAL TARGET OFFERS LESS THAN 10% UPSIDE"
        return result
    if not math.isfinite(reward_risk) or reward_risk < 2.0:
        result["technical_reason"] = "TECHNICAL TARGET OFFERS LESS THAN 2:1 REWARD/RISK"
        return result

    support = _clusters(_pivot_values(confirmed_prior["Low"], "low"), atr_value)
    if support:
        nearest = min(
            support,
            key=lambda cluster: 0.0
            if cluster["upper"] >= zone_low and cluster["lower"] <= zone_high
            else min(abs(cluster["upper"] - zone_low), abs(cluster["lower"] - zone_high)),
        )
        distance = 0.0 if nearest["upper"] >= zone_low and nearest["lower"] <= zone_high else min(
            abs(nearest["upper"] - zone_low), abs(nearest["lower"] - zone_high)
        )
        support_score = 20.0 * _clip(1.0 - distance / (0.5 * atr_value), 0.0, 1.0)
    else:
        support_score = 0.0

    benchmark_score, market_state = market_regime_score(benchmark)
    relative_strength = np.nan
    relative_score = 10.0
    if benchmark is not None and len(benchmark):
        stock_close = data["Close"].iloc[: signal_index + 1]
        benchmark_close = pd.to_numeric(benchmark["Close"], errors="coerce").dropna()
        aligned = pd.concat([stock_close.rename("stock"), benchmark_close.rename("benchmark")], axis=1).dropna()
        if len(aligned) >= 11:
            stock_change = aligned["stock"].iloc[-1] / aligned["stock"].iloc[-11] - 1
            benchmark_change = aligned["benchmark"].iloc[-1] / aligned["benchmark"].iloc[-11] - 1
            relative_strength = float(stock_change - benchmark_change)
            relative_score = 20.0 * _clip((relative_strength + 0.05) / 0.10, 0.0, 1.0)

    macd_location = float(signal["MACD"] / confirmation_close) if confirmation_close else np.nan
    macd_score = 15.0 if macd_location <= 0 else 15.0 * _clip(1.0 - macd_location / 0.02, 0.0, 1.0)
    prior_volume = data["Volume"].iloc[max(0, signal_index - 20) : signal_index]
    median_volume = float(prior_volume.median()) if len(prior_volume) else np.nan
    volume_ratio = float(signal["Volume"] / median_volume) if median_volume > 0 else np.nan
    volume_score = 15.0 * _clip((volume_ratio - 0.5) / 1.5, 0.0, 1.0) if math.isfinite(volume_ratio) else 0.0
    day_range = float(signal["High"] - signal["Low"])
    close_location = (float(signal["Close"] - signal["Low"]) / day_range) if day_range > 0 else 0.5
    body_direction = 1.0 if signal["Close"] > signal["Open"] else 0.0 if signal["Close"] < signal["Open"] else 0.5
    candle_score = 15.0 * (0.5 * close_location + 0.5 * body_direction)
    technical_score = support_score + relative_score + macd_score + volume_score + candle_score + benchmark_score
    tier = "A" if technical_score >= 75 else "B" if technical_score >= 50 else "C"

    state = "ENTRY READY" if entry_index is not None else "AWAITING NEXT OPEN"
    result.update(
        {
            "technical_state": state,
            "technical_reason": "ALL TECHNICAL GATES PASS" if entry_index is not None else "VALID DAILY CLOSE — AWAITING NEXT OPEN",
            "signal_date": data.index[signal_index],
            "entry_date": entry_date,
            "price": confirmation_close,
            "entry": entry,
            "rsi": float(signal["RSI14"]),
            "atr20": atr_value,
            "zone_low": zone_low,
            "zone_high": zone_high,
            "stop": stop,
            "stop_distance_pct": stop_distance * 100,
            "target": target,
            "target_source": target_source,
            "upside_pct": target_upside * 100,
            "reward_risk": reward_risk,
            "gap_atr": gap_atr,
            "turnover_median_20": float((data["Close"] * data["Volume"]).iloc[max(0, signal_index - 20) : signal_index].median()),
            "sma180_slope_pct": sma180_slope * 100,
            "sma200_slope_pct": sma200_slope * 100,
            "technical_score": technical_score,
            "technical_tier": tier,
            "support_score": support_score,
            "relative_strength_pct": relative_strength * 100 if math.isfinite(relative_strength) else np.nan,
            "relative_strength_score": relative_score,
            "macd_score": macd_score,
            "volume_ratio": volume_ratio,
            "volume_score": volume_score,
            "candle_score": candle_score,
            "market_state": market_state,
            "market_score": benchmark_score,
        }
    )
    return result


@dataclass
class FundamentalSnapshot:
    symbol: str
    company: str
    sector: str
    industry: str
    currency: str
    market_cap: float
    roic: float
    roe: float
    operating_margin: float
    fcf_margin: float
    annual_fcf: list[float]
    annual_net_income: list[float]
    net_debt_to_fcf: float
    interest_coverage: float
    no_interest_expense: bool
    current_ratio: float
    revenue_growth: float
    earnings_growth: float
    operating_growth: float
    growth_source: str
    share_change: float
    distribution_ratio: float
    earnings_date: Any
    earnings_source: str
    trailing_pe: float
    price_sales: float
    missing_hard_inputs: list[str]


def build_fundamental_snapshot(symbol: str, ticker: Any) -> FundamentalSnapshot:
    info = _ticker_mapping(ticker, "info")
    fast_info = _ticker_mapping(ticker, "fast_info")
    annual_income = _ticker_statement(
        ticker,
        ["financials", "income_stmt"],
        [("get_income_stmt", {"freq": "yearly"})],
    )
    quarterly_income = _ticker_statement(
        ticker,
        ["quarterly_financials", "quarterly_income_stmt"],
        [("get_income_stmt", {"freq": "quarterly"})],
    )
    annual_cash = _ticker_statement(
        ticker,
        ["cashflow", "cash_flow"],
        [("get_cash_flow", {"freq": "yearly"})],
    )
    annual_balance = _ticker_statement(
        ticker,
        ["balance_sheet"],
        [("get_balance_sheet", {"freq": "yearly"})],
    )

    revenue = _series(annual_income, ["Total Revenue", "Operating Revenue"])
    net_income = _series(annual_income, ["Net Income", "Net Income Common Stockholders"])
    operating_income = _series(annual_income, ["Operating Income", "EBIT"])
    ebit = _series(annual_income, ["EBIT", "Operating Income"])
    tax = _series(annual_income, ["Tax Provision", "Income Tax Expense"])
    pretax = _series(annual_income, ["Pretax Income", "Income Before Tax"])
    interest = _series(annual_income, ["Interest Expense", "Interest Expense Non Operating"])
    fcf = _annual_fcf(annual_cash)
    total_debt = _latest(_series(annual_balance, ["Total Debt"]))
    cash = _latest(_series(annual_balance, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"]))
    current_assets = _latest(_series(annual_balance, ["Current Assets", "Total Current Assets"]))
    current_liabilities = _latest(_series(annual_balance, ["Current Liabilities", "Total Current Liabilities"]))
    equity = _latest(_series(annual_balance, ["Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity"]))
    invested_capital = _latest(_series(annual_balance, ["Invested Capital", "Total Capitalization"]))
    shares = _series(annual_balance, ["Ordinary Shares Number", "Share Issued"])

    latest_revenue = _latest(revenue)
    latest_net_income = _latest(net_income)
    latest_operating = _latest(operating_income)
    latest_ebit = _latest(ebit)
    latest_fcf = _latest(fcf)
    effective_tax = _latest(tax) / _latest(pretax) if _latest(pretax) > 0 else 0.21
    effective_tax = _clip(effective_tax, 0.0, 0.40)
    roic = _number(info.get("returnOnInvestedCapital"))
    if not math.isfinite(roic) and invested_capital > 0 and math.isfinite(latest_ebit):
        roic = latest_ebit * (1 - effective_tax) / invested_capital
    roe = _number(info.get("returnOnEquity"))
    if not math.isfinite(roe) and equity > 0 and math.isfinite(latest_net_income):
        roe = latest_net_income / equity
    operating_margin = _number(info.get("operatingMargins"))
    if not math.isfinite(operating_margin) and latest_revenue > 0:
        operating_margin = latest_operating / latest_revenue
    fcf_margin = latest_fcf / latest_revenue if latest_revenue > 0 and math.isfinite(latest_fcf) else np.nan
    net_debt = max(0.0, total_debt - cash) if math.isfinite(total_debt) and math.isfinite(cash) else np.nan
    net_debt_to_fcf = net_debt / latest_fcf if math.isfinite(net_debt) and latest_fcf > 0 else np.nan
    latest_interest = abs(_latest(interest))
    no_interest = not math.isfinite(latest_interest) or latest_interest <= 0
    interest_coverage = latest_ebit / latest_interest if not no_interest and math.isfinite(latest_ebit) else np.nan
    current_ratio = current_assets / current_liabilities if current_liabilities > 0 else np.nan

    revenue_growth, growth_source = _statement_growth(quarterly_income, ["Total Revenue", "Operating Revenue"])
    earnings_growth, earnings_source = _statement_growth(quarterly_income, ["Net Income", "Net Income Common Stockholders"])
    operating_growth, operating_source = _statement_growth(quarterly_income, ["Operating Income", "EBIT"])
    if not math.isfinite(revenue_growth):
        revenue_growth = _growth(_latest(revenue), _number(revenue.iloc[1]) if len(revenue) > 1 else np.nan)
        growth_source = "ANNUAL_FALLBACK"
    if not math.isfinite(earnings_growth):
        earnings_growth = _growth(_latest(net_income), _number(net_income.iloc[1]) if len(net_income) > 1 else np.nan)
        earnings_source = "ANNUAL_FALLBACK"
    if not math.isfinite(operating_growth):
        operating_growth = _growth(_latest(operating_income), _number(operating_income.iloc[1]) if len(operating_income) > 1 else np.nan)
        operating_source = "ANNUAL_FALLBACK"
    source = "TTM" if {growth_source, earnings_source, operating_source} == {"TTM"} else "ANNUAL_FALLBACK"

    share_change = _growth(_latest(shares), _number(shares.iloc[1]) if len(shares) > 1 else np.nan)
    dividends = abs(_latest(_series(annual_cash, ["Cash Dividends Paid", "Common Stock Dividend Paid"])))
    repurchase = abs(_latest(_series(annual_cash, ["Repurchase Of Capital Stock", "Repurchase Of Stock"])))
    issuance = abs(_latest(_series(annual_cash, ["Issuance Of Capital Stock", "Issuance Of Stock"])))
    net_buybacks = max(0.0, repurchase - issuance) if math.isfinite(repurchase) else 0.0
    if latest_fcf > 0:
        distribution_ratio = ((dividends if math.isfinite(dividends) else 0.0) + net_buybacks) / latest_fcf
    else:
        distribution_ratio = np.nan

    earnings_date = None
    earnings_source_label = "UNVERIFIED"
    try:
        calendar = ticker.calendar
        raw_date = calendar.get("Earnings Date") if isinstance(calendar, dict) else None
        if isinstance(calendar, pd.DataFrame) and "Earnings Date" in calendar.index:
            raw_date = calendar.loc["Earnings Date"].dropna().iloc[0]
        if isinstance(raw_date, (list, tuple)) and raw_date:
            raw_date = raw_date[0]
        if raw_date is not None:
            earnings_date = pd.Timestamp(raw_date)
            earnings_source_label = "PROVIDER CALENDAR"
    except Exception as exc:
        if _is_yahoo_rate_limit_error(exc):
            raise
        pass
    if earnings_date is None:
        try:
            history = ticker.get_earnings_dates(limit=12)
            dates = [] if history is None else [pd.Timestamp(value).tz_localize(None) for value in history.index]
            dates = sorted(set(dates))
            today = pd.Timestamp.utcnow().tz_localize(None).normalize()
            equivalents = sorted(date + pd.Timedelta(days=364) for date in dates if date + pd.Timedelta(days=364) >= today)
            if equivalents:
                earnings_date = equivalents[0]
                earnings_source_label = "PREVIOUS-YEAR EQUIVALENT ESTIMATE"
            elif len(dates) >= 3:
                intervals = np.diff(np.array(dates, dtype="datetime64[D]")).astype(int)
                positive_intervals = intervals[intervals > 0]
                if len(positive_intervals) >= 2:
                    interval_days = int(np.median(positive_intervals))
                    estimate = dates[-1] + pd.Timedelta(days=interval_days)
                    while estimate < today:
                        estimate += pd.Timedelta(days=interval_days)
                    earnings_date = estimate
                    earnings_source_label = "MEDIAN INTERVAL ESTIMATE"
        except Exception as exc:
            if _is_yahoo_rate_limit_error(exc):
                raise
            pass

    missing: list[str] = []
    required = {
        "three annual FCF periods": len(fcf) >= 3,
        "latest positive FCF": math.isfinite(latest_fcf),
        "net debt and FCF": math.isfinite(net_debt_to_fcf),
        "two share-count periods": len(shares) >= 2 and math.isfinite(share_change),
        "revenue trend": math.isfinite(revenue_growth),
        "earnings trend": math.isfinite(earnings_growth),
        "operating-profit trend": math.isfinite(operating_growth),
    }
    for label, available in required.items():
        if not available:
            missing.append(label)

    return FundamentalSnapshot(
        symbol=symbol,
        company=str(info.get("shortName") or info.get("longName") or symbol),
        sector=str(info.get("sector") or "UNAVAILABLE"),
        industry=str(info.get("industry") or "UNAVAILABLE"),
        currency=str(info.get("currency") or fast_info.get("currency") or "UNAVAILABLE").upper(),
        market_cap=_number(info.get("marketCap", fast_info.get("market_cap"))),
        roic=roic,
        roe=roe if equity > 0 else np.nan,
        operating_margin=operating_margin,
        fcf_margin=fcf_margin,
        annual_fcf=[float(value) for value in fcf.iloc[:10] if math.isfinite(float(value))],
        annual_net_income=[float(value) for value in net_income.iloc[:3] if math.isfinite(float(value))],
        net_debt_to_fcf=net_debt_to_fcf,
        interest_coverage=interest_coverage,
        no_interest_expense=no_interest,
        current_ratio=current_ratio,
        revenue_growth=revenue_growth,
        earnings_growth=earnings_growth,
        operating_growth=operating_growth,
        growth_source=source,
        share_change=share_change,
        distribution_ratio=distribution_ratio,
        earnings_date=earnings_date,
        earnings_source=earnings_source_label,
        trailing_pe=_number(info.get("trailingPE")),
        price_sales=_number(info.get("priceToSalesTrailing12Months")),
        missing_hard_inputs=missing,
    )


def _peer_score(
    value: float,
    snapshot: FundamentalSnapshot,
    peers: list[FundamentalSnapshot],
    field: str,
) -> tuple[float, str]:
    positive_industry = [
        _number(getattr(peer, field))
        for peer in peers
        if peer.symbol != snapshot.symbol
        and peer.industry == snapshot.industry
        and _number(getattr(peer, field)) > 0
    ]
    positive_sector = [
        _number(getattr(peer, field))
        for peer in peers
        if peer.symbol != snapshot.symbol
        and peer.sector == snapshot.sector
        and _number(getattr(peer, field)) > 0
    ]
    if len(positive_industry) >= 5:
        median = float(np.median(positive_industry))
        return _linear(value / median, 0.5, 1.5, 10.0), "INDUSTRY MEDIAN"
    if len(positive_sector) >= 5:
        median = float(np.median(positive_sector))
        return _linear(value / median, 0.5, 1.5, 10.0), "SECTOR MEDIAN"
    return _linear(value, 0.05, 0.20, 10.0), "ABSOLUTE 5–20% FALLBACK"


def score_fundamental_snapshot(
    snapshot: FundamentalSnapshot,
    peers: list[FundamentalSnapshot],
    gbp_rate: float,
    earnings_sessions: int | None,
    official_event_verified: bool = False,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    sector = snapshot.sector.strip().lower()
    if sector in {"", "unavailable", "unknown"}:
        failures.append("FUNDAMENTAL DATA INCOMPLETE — SECTOR")
    elif sector in EXCLUDED_SECTORS:
        failures.append("EXCLUDED SECTOR")
    market_cap_gbp = snapshot.market_cap * gbp_rate if math.isfinite(snapshot.market_cap) else np.nan
    if not math.isfinite(market_cap_gbp):
        failures.append("FUNDAMENTAL DATA INCOMPLETE — MARKET CAP")
    elif market_cap_gbp < 500_000_000:
        failures.append("MARKET CAP BELOW £500M")
    if snapshot.missing_hard_inputs:
        failures.append("FUNDAMENTAL DATA INCOMPLETE — " + ", ".join(snapshot.missing_hard_inputs))
    fcf_values = snapshot.annual_fcf[:10]
    if len(fcf_values) >= 3:
        if fcf_values[0] <= 0 or sum(value > 0 for value in fcf_values[:3]) < 2:
            failures.append("FCF HARD GATE FAILED")
    if math.isfinite(snapshot.net_debt_to_fcf):
        if snapshot.net_debt_to_fcf > 4:
            failures.append("NET DEBT/FCF ABOVE 4X")
        elif snapshot.net_debt_to_fcf >= 3:
            warnings.append("NET DEBT/FCF 3–4X")
    if math.isfinite(snapshot.share_change):
        if snapshot.share_change > 0.05:
            failures.append("ANNUAL DILUTION ABOVE 5%")
        elif snapshot.share_change >= 0.02:
            warnings.append("ANNUAL DILUTION 2–5%")
    if snapshot.revenue_growth < -0.10 and (
        snapshot.earnings_growth < -0.20 or snapshot.operating_growth < -0.20
    ):
        failures.append("REVENUE AND PROFIT DETERIORATION")
    if len(snapshot.annual_net_income) >= 2 and snapshot.annual_net_income[0] < 0 < snapshot.annual_net_income[1]:
        failures.append("MOVED FROM PROFIT TO MATERIAL LOSS")

    roic_points = _linear(snapshot.roic * 100, 5, 20, 10)
    roe_points = _linear(snapshot.roe * 100, 10, 25, 5)
    operating_points, operating_basis = _peer_score(snapshot.operating_margin, snapshot, peers, "operating_margin")
    fcf_margin_points, fcf_basis = _peer_score(snapshot.fcf_margin, snapshot, peers, "fcf_margin")
    evidence_years = min(len(fcf_values), 10)
    consistency_points = 10 * sum(value > 0 for value in fcf_values[:evidence_years]) / evidence_years if evidence_years >= 3 else 0
    aggregate_income = sum(snapshot.annual_net_income[:3])
    conversion = sum(fcf_values[:3]) / aggregate_income if aggregate_income > 0 and len(fcf_values) >= 3 else np.nan
    conversion_points = _linear(conversion, 0.5, 1.0, 5)
    if snapshot.net_debt_to_fcf <= 1:
        leverage_points = 10.0
    else:
        leverage_points = 10.0 * _clip((4 - snapshot.net_debt_to_fcf) / 3, 0, 1) if math.isfinite(snapshot.net_debt_to_fcf) else 0.0
    interest_points = 5.0 if snapshot.no_interest_expense else _linear(snapshot.interest_coverage, 5, 15, 5)
    liquidity_points = _linear(snapshot.current_ratio, 1, 2, 5)
    revenue_points = _linear(snapshot.revenue_growth * 100, 0, 15, 8)
    earnings_points = _linear(snapshot.earnings_growth * 100, 0, 25, 12)
    if snapshot.share_change <= -0.02:
        share_points = 7.0
    elif snapshot.share_change <= 0:
        share_points = 5 + 2 * (-snapshot.share_change / 0.02)
    else:
        share_points = 5 * _clip(1 - snapshot.share_change / 0.05, 0, 1)
    if math.isfinite(snapshot.distribution_ratio):
        distribution_points = 3.0 if snapshot.distribution_ratio <= 1 else 3.0 * _clip((1.5 - snapshot.distribution_ratio) / 0.5, 0, 1)
    else:
        distribution_points = 0.0

    score = sum(
        [
            roic_points,
            roe_points,
            operating_points,
            fcf_margin_points,
            consistency_points,
            conversion_points,
            leverage_points,
            interest_points,
            liquidity_points,
            revenue_points,
            earnings_points,
            share_points,
            distribution_points,
        ]
    )
    if not math.isfinite(score):
        failures.append("FUNDAMENTAL DATA INCOMPLETE — QUALITY SCORE")
    elif score < 65:
        failures.append("FUNDAMENTAL QUALITY SCORE BELOW 65")
    if earnings_sessions is None or earnings_sessions < 0:
        failures.append("EARNINGS DATE UNVERIFIED")
    elif 0 <= earnings_sessions <= 5:
        failures.append("EARNINGS WAIT — RESULTS WITHIN FIVE SESSIONS")
    if not official_event_verified:
        failures.append("FAIL-SAFE EVENT BLOCK — OFFICIAL CHECK UNVERIFIED")

    return {
        "fundamental_score": round(score, 2),
        "fundamental_failures": failures,
        "fundamental_warnings": warnings,
        "market_cap_gbp": market_cap_gbp,
        "profitability_score": roic_points + roe_points + operating_points,
        "cash_score": fcf_margin_points + consistency_points + conversion_points,
        "balance_score": leverage_points + interest_points + liquidity_points,
        "growth_score": revenue_points + earnings_points,
        "shareholder_score": share_points + distribution_points,
        "operating_margin_basis": operating_basis,
        "fcf_margin_basis": fcf_basis,
        "fcf_evidence_years": evidence_years,
        "valuation_gate": "N/A — 10-YEAR HISTORY UNAVAILABLE",
    }


def business_sessions_until(value: Any, today: pd.Timestamp | None = None) -> int | None:
    if value is None or pd.isna(value):
        return None
    start = (today or pd.Timestamp.utcnow()).normalize().tz_localize(None)
    end = pd.Timestamp(value).normalize().tz_localize(None)
    if end < start:
        return -1
    return int(np.busday_count(start.date(), end.date()))
