from __future__ import annotations

import io
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests


FRED_SERIES = ("M2SL", "WALCL", "WTREGEN", "RRPONTSYD", "NFCI", "DFII10", "DTWEXBGS")


def unavailable_macro(errors: List[str] | None = None) -> Dict:
    return {
        "available": False,
        "score": None,
        "regime": "DATA LIMITED",
        "stance": "Do not macro-gate trades",
        "allows_new_swing_risk": True,
        "factors": [],
        "errors": list(errors or []),
    }


def _safe_float(value, default=np.nan) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _fred_series(series_id: str, timeout: float = 8.0) -> pd.Series:
    response = requests.get(
        "https://fred.stlouisfed.org/graph/fredgraph.csv",
        params={"id": series_id},
        headers={"User-Agent": "cl-signal/1.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    frame = pd.read_csv(io.StringIO(response.text))
    if frame.empty or len(frame.columns) < 2:
        return pd.Series(dtype=float)
    dates = pd.to_datetime(frame.iloc[:, 0], errors="coerce", utc=True)
    values = pd.to_numeric(frame.iloc[:, -1], errors="coerce")
    series = pd.Series(values.to_numpy(), index=dates).dropna()
    return series[~series.index.isna()].sort_index()


def _stablecoin_supply_series(timeout: float = 8.0) -> pd.Series:
    response = requests.get(
        "https://stablecoins.llama.fi/stablecoincharts/all",
        headers={"User-Agent": "cl-signal/1.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    points = []
    for item in response.json() or []:
        try:
            timestamp = pd.to_datetime(float(item.get("date")), unit="s", utc=True)
            circulating = item.get("totalCirculatingUSD") or item.get("totalCirculating") or {}
            value = _safe_float(circulating.get("peggedUSD"), np.nan)
            if math.isfinite(value):
                points.append((timestamp, value))
        except Exception:
            continue
    if not points:
        return pd.Series(dtype=float)
    dates, values = zip(*points)
    return pd.Series(values, index=pd.DatetimeIndex(dates)).sort_index()


def _series_change_days(series: pd.Series, days: int) -> float:
    if series is None or series.empty:
        return np.nan
    series = series.dropna().sort_index()
    if len(series) < 2:
        return np.nan
    prior = series.loc[: series.index[-1] - pd.Timedelta(days=days)]
    if prior.empty or float(prior.iloc[-1]) == 0:
        return np.nan
    return (float(series.iloc[-1]) / float(prior.iloc[-1]) - 1) * 100


def _series_delta_days(series: pd.Series, days: int) -> float:
    if series is None or series.empty:
        return np.nan
    series = series.dropna().sort_index()
    if len(series) < 2:
        return np.nan
    prior = series.loc[: series.index[-1] - pd.Timedelta(days=days)]
    if prior.empty:
        return np.nan
    return float(series.iloc[-1]) - float(prior.iloc[-1])


def _score_growth(
    change: float,
    bands: List[Tuple[float, float]],
    fallback: float = 0.0,
) -> float:
    if not math.isfinite(change):
        return np.nan
    for threshold, points in bands:
        if change >= threshold:
            return points
    return fallback


def calculate_macro_liquidity_regime(timeout: float = 8.0) -> Dict:
    """Build the macro regime with one bounded parallel network phase."""
    series: Dict[str, pd.Series] = {}
    errors: List[str] = []

    def load(name: str) -> Tuple[str, pd.Series]:
        if name == "STABLECOINS":
            return name, _stablecoin_supply_series(timeout)
        return name, _fred_series(name, timeout)

    names = [*FRED_SERIES, "STABLECOINS"]
    with ThreadPoolExecutor(max_workers=len(names)) as executor:
        futures = {executor.submit(load, name): name for name in names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                key, value = future.result()
                series[key] = value
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:180]}")

    factors = []

    def add_factor(name, value, change_text, points, max_points, signal, source):
        if math.isfinite(points):
            factors.append({
                "Factor": name,
                "Latest": float(value),
                "Trend change": change_text,
                "Points": round(float(points), 1),
                "Max": float(max_points),
                "Signal": signal,
                "Source": source,
            })

    try:
        value = series["M2SL"]
        change = _series_change_days(value, 100)
        points = _score_growth(change, [(1.5, 20), (0.5, 16), (0.0, 12), (-0.5, 8)], 3)
        add_factor("US M2", value.iloc[-1], f"{change:+.2f}% / ~3m", points, 20,
                   "Expanding" if change > 0 else "Contracting", "FRED M2SL")
    except Exception as exc:
        errors.append("US M2 calculation: " + str(exc))

    try:
        net_frame = pd.concat(
            [
                series["WALCL"].rename("fed"),
                series["WTREGEN"].rename("tga"),
                (series["RRPONTSYD"] * 1000.0).rename("rrp"),
            ],
            axis=1,
        ).sort_index().ffill().dropna()
        value = net_frame["fed"] - net_frame["tga"] - net_frame["rrp"]
        change = _series_change_days(value, 91)
        points = _score_growth(change, [(2.0, 15), (0.5, 12), (0.0, 9), (-2.0, 5)], 2)
        add_factor("Fed net liquidity", value.iloc[-1], f"{change:+.2f}% / 13w", points, 15,
                   "Expanding" if change > 0 else "Contracting",
                   "FRED WALCL - WTREGEN - RRPONTSYD")
    except Exception as exc:
        errors.append("Fed net liquidity calculation: " + str(exc))

    try:
        value = series["NFCI"]
        latest = float(value.iloc[-1])
        change = _series_delta_days(value, 28)
        if latest <= -0.5 and (not math.isfinite(change) or change <= 0):
            points = 20
        elif latest <= -0.25:
            points = 17 if not math.isfinite(change) or change <= 0 else 14
        elif latest <= 0:
            points = 13 if not math.isfinite(change) or change <= 0 else 10
        elif math.isfinite(change) and change < 0:
            points = 7
        else:
            points = 2
        add_factor("Financial conditions (NFCI)", latest,
                   f"{change:+.3f} index pts / 4w" if math.isfinite(change) else "Unavailable",
                   points, 20,
                   "Loose / easing" if latest < 0 and (not math.isfinite(change) or change <= 0)
                   else "Tightening / restrictive", "FRED NFCI")
    except Exception as exc:
        errors.append("NFCI calculation: " + str(exc))

    try:
        value = series["DFII10"]
        latest = float(value.iloc[-1])
        change = _series_delta_days(value, 30)
        if math.isfinite(change) and change <= -0.25:
            points = 15
        elif math.isfinite(change) and change < -0.05:
            points = 12
        elif math.isfinite(change) and change <= 0.05:
            points = 8
        elif math.isfinite(change) and change <= 0.25:
            points = 4
        else:
            points = 1
        add_factor("10Y real yield", latest,
                   f"{change * 100:+.0f} bps / 30d" if math.isfinite(change) else "Unavailable",
                   points, 15, "Falling / supportive" if math.isfinite(change) and change < 0
                   else "Rising / headwind", "FRED DFII10")
    except Exception as exc:
        errors.append("10Y real yield calculation: " + str(exc))

    try:
        value = series["DTWEXBGS"]
        latest = float(value.iloc[-1])
        change = _series_change_days(value, 30)
        if change <= -2:
            points = 15
        elif change <= -0.5:
            points = 12
        elif change <= 0.5:
            points = 8
        elif change <= 2:
            points = 4
        else:
            points = 1
        add_factor("Broad US dollar", latest, f"{change:+.2f}% / 30d", points, 15,
                   "Weakening / supportive" if change < 0 else "Strengthening / headwind",
                   "FRED DTWEXBGS")
    except Exception as exc:
        errors.append("Broad dollar calculation: " + str(exc))

    try:
        value = series["STABLECOINS"]
        latest = float(value.iloc[-1])
        change = _series_change_days(value, 30)
        points = _score_growth(change, [(5.0, 15), (2.0, 13), (0.5, 10), (0.0, 8), (-2.0, 4)], 1)
        add_factor("Stablecoin supply", latest, f"{change:+.2f}% / 30d", points, 15,
                   "Expanding" if change > 0 else "Contracting", "DefiLlama stablecoins")
    except Exception as exc:
        errors.append("Stablecoin supply calculation: " + str(exc))

    available_max = sum(float(item["Max"]) for item in factors)
    if available_max < 45:
        result = unavailable_macro(errors)
        result["factors"] = factors
        return result

    score = round(sum(float(item["Points"]) for item in factors) / available_max * 100, 1)
    if score >= 70:
        regime, stance = "EXPANSION", "Risk-on supportive"
    elif score >= 58:
        regime, stance = "IMPROVING", "Supportive / selective risk-on"
    elif score >= 42:
        regime, stance = "MIXED / NEUTRAL", "Technical setups can proceed selectively"
    elif score >= 30:
        regime, stance = "DETERIORATING", "New swing risk should wait"
    else:
        regime, stance = "CONTRACTION", "Defensive — avoid new swing risk"

    return {
        "available": True,
        "score": score,
        "regime": regime,
        "stance": stance,
        "allows_new_swing_risk": score >= 42,
        "factors": factors,
        "errors": errors,
    }


def snapshot_payload(timeout: float = 8.0) -> Dict:
    result = calculate_macro_liquidity_regime(timeout=timeout)
    result["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return result


def snapshot_age_minutes(snapshot: Dict) -> float:
    try:
        timestamp = pd.Timestamp(snapshot.get("generated_at"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        return max(
            (pd.Timestamp.now(tz="UTC") - timestamp.tz_convert("UTC")).total_seconds() / 60,
            0.0,
        )
    except Exception:
        return np.nan
