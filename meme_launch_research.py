from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, Sequence

import numpy as np
import pandas as pd


CHECKPOINT_SECONDS = (30, 60, 120, 180, 300)
OUTCOME_HORIZONS_SECONDS = (900, 1800, 3600, 10800, 21600, 86400)
TARGET_MULTIPLES = (2.0, 3.0, 5.0)
SEVERE_DRAWDOWN_LEVELS = (0.50, 0.70, 0.90)
LIQUIDITY_COLLAPSE_LEVELS = (0.50, 0.80)


@dataclass(frozen=True)
class LaunchAnchor:
    token_address: str
    pool_address: str
    chain: str
    launch_ts: pd.Timestamp
    first_seen_ts: pd.Timestamp
    first_seen_price: float
    first_seen_liquidity_usd: float = np.nan
    discovery_source: str = ""


def _safe(value, default=np.nan) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _gini(values: Sequence[float]) -> float:
    arr = np.asarray([x for x in values if math.isfinite(_safe(x)) and _safe(x) >= 0], dtype=float)
    if arr.size == 0 or arr.sum() <= 0:
        return np.nan
    arr.sort()
    n = arr.size
    index = np.arange(1, n + 1)
    return float((2 * np.sum(index * arr) / (n * arr.sum())) - (n + 1) / n)


def _max_drawdown(prices: pd.Series) -> float:
    clean = pd.to_numeric(prices, errors="coerce").dropna()
    if clean.empty:
        return np.nan
    peaks = clean.cummax()
    dd = clean / peaks.replace(0, np.nan) - 1
    return float(dd.min() * 100)


def normalise_trades(trades: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise raw DEX trades into the minimum research schema.

    Required:
      timestamp, side, volume_usd

    Optional:
      price_usd, wallet_address, tx_hash, trade_id
    """
    if trades is None or trades.empty:
        return pd.DataFrame(
            columns=[
                "timestamp", "side", "volume_usd", "price_usd",
                "wallet_address", "tx_hash", "trade_id",
            ]
        )

    out = trades.copy()
    required = {"timestamp", "side", "volume_usd"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"Missing required trade columns: {sorted(missing)}")

    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out["side"] = out["side"].astype(str).str.lower().str.strip()
    out["volume_usd"] = pd.to_numeric(out["volume_usd"], errors="coerce")
    if "price_usd" not in out.columns:
        out["price_usd"] = np.nan
    out["price_usd"] = pd.to_numeric(out["price_usd"], errors="coerce")
    for col in ("wallet_address", "tx_hash", "trade_id"):
        if col not in out.columns:
            out[col] = ""

    out = out.dropna(subset=["timestamp", "volume_usd"])
    out = out[out["volume_usd"] >= 0].copy()

    dedupe_cols = [col for col in ("trade_id", "tx_hash") if out[col].astype(str).str.len().gt(0).any()]
    if dedupe_cols:
        key = dedupe_cols[0]
        out = out.drop_duplicates(subset=[key], keep="last")

    return out.sort_values("timestamp").reset_index(drop=True)


def checkpoint_features(
    trades: pd.DataFrame,
    anchor_ts,
    checkpoint_seconds: int,
    initial_liquidity_usd: float = np.nan,
    current_liquidity_usd: float = np.nan,
) -> Dict:
    """
    Build point-in-time order-flow features using only trades available by checkpoint.
    """
    if checkpoint_seconds <= 0:
        raise ValueError("checkpoint_seconds must be positive")

    t = normalise_trades(trades)
    anchor = pd.Timestamp(anchor_ts)
    if anchor.tzinfo is None:
        anchor = anchor.tz_localize("UTC")
    else:
        anchor = anchor.tz_convert("UTC")
    end = anchor + pd.Timedelta(seconds=int(checkpoint_seconds))
    observed = t[(t["timestamp"] >= anchor) & (t["timestamp"] <= end)].copy()

    buys = observed[observed["side"].isin(["buy", "b"])]
    sells = observed[observed["side"].isin(["sell", "s"])]
    buy_usd = float(buys["volume_usd"].sum())
    sell_usd = float(sells["volume_usd"].sum())
    total_usd = buy_usd + sell_usd
    net_usd = buy_usd - sell_usd

    wallets = observed["wallet_address"].fillna("").astype(str)
    valid_wallet = wallets.str.len().gt(0)
    unique_traders = int(wallets[valid_wallet].nunique())
    unique_buyers = int(
        buys.loc[buys["wallet_address"].fillna("").astype(str).str.len().gt(0), "wallet_address"].astype(str).nunique()
    )
    unique_sellers = int(
        sells.loc[sells["wallet_address"].fillna("").astype(str).str.len().gt(0), "wallet_address"].astype(str).nunique()
    )

    wallet_flow = (
        observed.loc[valid_wallet]
        .groupby("wallet_address", dropna=False)["volume_usd"]
        .sum()
        .sort_values(ascending=False)
    )
    top1_share = float(wallet_flow.iloc[:1].sum() / total_usd) if total_usd > 0 and not wallet_flow.empty else np.nan
    top5_share = float(wallet_flow.iloc[:5].sum() / total_usd) if total_usd > 0 and not wallet_flow.empty else np.nan
    hhi = float(((wallet_flow / total_usd) ** 2).sum()) if total_usd > 0 and not wallet_flow.empty else np.nan
    gini = _gini(wallet_flow.tolist())

    buy_wallets = set(buys["wallet_address"].dropna().astype(str)) - {""}
    sell_wallets = set(sells["wallet_address"].dropna().astype(str)) - {""}
    both = buy_wallets & sell_wallets
    roundtrip_wallet_ratio = len(both) / max(len(buy_wallets | sell_wallets), 1)

    midpoint = anchor + pd.Timedelta(seconds=checkpoint_seconds / 2)
    early = observed[observed["timestamp"] < midpoint]
    late = observed[observed["timestamp"] >= midpoint]
    early_vol = float(early["volume_usd"].sum())
    late_vol = float(late["volume_usd"].sum())
    volume_acceleration = late_vol / early_vol if early_vol > 0 else (np.inf if late_vol > 0 else np.nan)

    early_buyers = set(
        early.loc[
            early["side"].isin(["buy", "b"])
            & early["wallet_address"].fillna("").astype(str).str.len().gt(0),
            "wallet_address",
        ].astype(str)
    )
    late_buyers = set(
        late.loc[
            late["side"].isin(["buy", "b"])
            & late["wallet_address"].fillna("").astype(str).str.len().gt(0),
            "wallet_address",
        ].astype(str)
    )
    new_late_buyers = late_buyers - early_buyers
    buyer_acceleration = len(new_late_buyers) / max(len(early_buyers), 1)

    prices = pd.to_numeric(observed["price_usd"], errors="coerce").dropna()
    first_price = _safe(prices.iloc[0]) if not prices.empty else np.nan
    last_price = _safe(prices.iloc[-1]) if not prices.empty else np.nan
    checkpoint_return = (
        (last_price / first_price - 1) * 100
        if math.isfinite(first_price) and first_price > 0 and math.isfinite(last_price)
        else np.nan
    )
    price_range = (
        (prices.max() / prices.min() - 1) * 100
        if len(prices) >= 2 and prices.min() > 0 else np.nan
    )
    drawdown = _max_drawdown(prices)

    liq = _safe(current_liquidity_usd)
    initial_liq = _safe(initial_liquidity_usd)
    liquidity_change_pct = (
        (liq / initial_liq - 1) * 100
        if math.isfinite(liq) and math.isfinite(initial_liq) and initial_liq > 0
        else np.nan
    )

    return {
        "checkpoint_seconds": int(checkpoint_seconds),
        "checkpoint_ts": end,
        "trade_count": int(len(observed)),
        "buy_count": int(len(buys)),
        "sell_count": int(len(sells)),
        "gross_buy_usd": buy_usd,
        "gross_sell_usd": sell_usd,
        "gross_volume_usd": total_usd,
        "net_buy_usd": net_usd,
        "buy_volume_share": buy_usd / total_usd if total_usd > 0 else np.nan,
        "net_buy_share": net_usd / total_usd if total_usd > 0 else np.nan,
        "unique_traders": unique_traders,
        "unique_buyers": unique_buyers,
        "unique_sellers": unique_sellers,
        "buyer_seller_ratio": unique_buyers / max(unique_sellers, 1),
        "new_buyers_latest_half": int(len(new_late_buyers)),
        "buyer_acceleration": float(buyer_acceleration),
        "volume_acceleration": float(volume_acceleration),
        "roundtrip_wallet_ratio": float(roundtrip_wallet_ratio),
        "top1_wallet_volume_share": top1_share,
        "top5_wallet_volume_share": top5_share,
        "wallet_volume_hhi": hhi,
        "wallet_volume_gini": gini,
        "median_trade_usd": float(observed["volume_usd"].median()) if not observed.empty else np.nan,
        "p90_trade_usd": float(observed["volume_usd"].quantile(0.90)) if not observed.empty else np.nan,
        "checkpoint_return_pct": checkpoint_return,
        "observed_price_range_pct": price_range,
        "observed_max_drawdown_pct": drawdown,
        "checkpoint_price": last_price,
        "liquidity_usd": liq,
        "liquidity_change_pct": liquidity_change_pct,
        "volume_to_liquidity": total_usd / liq if math.isfinite(liq) and liq > 0 else np.nan,
        "net_buy_to_liquidity": net_usd / liq if math.isfinite(liq) and liq > 0 else np.nan,
        "price_response_per_1k_net_buy_pct": (
            checkpoint_return / (net_usd / 1000)
            if math.isfinite(checkpoint_return) and abs(net_usd) >= 1 else np.nan
        ),
    }


def launch_feature_panel(
    trades: pd.DataFrame,
    anchor_ts,
    checkpoints: Iterable[int] = CHECKPOINT_SECONDS,
    initial_liquidity_usd: float = np.nan,
    liquidity_by_checkpoint: Dict[int, float] | None = None,
) -> pd.DataFrame:
    liquidity_by_checkpoint = liquidity_by_checkpoint or {}
    rows = [
        checkpoint_features(
            trades,
            anchor_ts,
            int(seconds),
            initial_liquidity_usd=initial_liquidity_usd,
            current_liquidity_usd=liquidity_by_checkpoint.get(int(seconds), np.nan),
        )
        for seconds in checkpoints
    ]
    return pd.DataFrame(rows)


def forward_outcomes(
    path: pd.DataFrame,
    entry_ts,
    entry_price: float,
    entry_liquidity_usd: float = np.nan,
    horizons: Iterable[int] = OUTCOME_HORIZONS_SECONDS,
) -> pd.DataFrame:
    """
    Label forward price/liquidity outcomes from a checkpoint price.

    Path columns:
      timestamp, price_usd
    Optional:
      liquidity_usd
    """
    if path is None or path.empty:
        return pd.DataFrame()
    p = path.copy()
    if not {"timestamp", "price_usd"}.issubset(p.columns):
        raise ValueError("path must include timestamp and price_usd")
    p["timestamp"] = pd.to_datetime(p["timestamp"], utc=True, errors="coerce")
    p["price_usd"] = pd.to_numeric(p["price_usd"], errors="coerce")
    if "liquidity_usd" in p.columns:
        p["liquidity_usd"] = pd.to_numeric(p["liquidity_usd"], errors="coerce")
    p = p.dropna(subset=["timestamp", "price_usd"]).sort_values("timestamp")

    start = pd.Timestamp(entry_ts)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    else:
        start = start.tz_convert("UTC")
    entry = _safe(entry_price)
    if not math.isfinite(entry) or entry <= 0:
        raise ValueError("entry_price must be positive")

    rows = []
    for horizon in horizons:
        end = start + pd.Timedelta(seconds=int(horizon))
        w = p[(p["timestamp"] >= start) & (p["timestamp"] <= end)].copy()
        if w.empty:
            continue
        prices = w["price_usd"]
        returns = prices / entry - 1
        row = {
            "horizon_seconds": int(horizon),
            "final_return_pct": float(returns.iloc[-1] * 100),
            "mfe_pct": float(returns.max() * 100),
            "mae_pct": float(returns.min() * 100),
            "max_drawdown_pct": _max_drawdown(prices),
        }

        for multiple in TARGET_MULTIPLES:
            target = entry * multiple
            hits = w[w["price_usd"] >= target]
            label = str(int(multiple))
            row[f"hit_{label}x"] = bool(not hits.empty)
            row[f"time_to_{label}x_seconds"] = (
                float((hits.iloc[0]["timestamp"] - start).total_seconds())
                if not hits.empty else np.nan
            )

            dd_price = entry * 0.50
            severe = w[w["price_usd"] <= dd_price]
            if hits.empty:
                before = False
                adverse_before = np.nan
            else:
                target_ts = hits.iloc[0]["timestamp"]
                before_path = w[w["timestamp"] <= target_ts]
                adverse_before = float((before_path["price_usd"].min() / entry - 1) * 100)
                before = severe.empty or target_ts < severe.iloc[0]["timestamp"]
            row[f"hit_{label}x_before_50dd"] = bool(before)
            row[f"mae_before_{label}x_pct"] = adverse_before

        for level in SEVERE_DRAWDOWN_LEVELS:
            pct = int(level * 100)
            row[f"price_drawdown_{pct}pct"] = bool((returns <= -level).any())

        if "liquidity_usd" in w.columns:
            liq = w["liquidity_usd"].dropna()
            initial_liq = _safe(entry_liquidity_usd)
            if math.isfinite(initial_liq) and initial_liq > 0 and not liq.empty:
                liq_returns = liq / initial_liq - 1
                row["final_liquidity_change_pct"] = float(liq_returns.iloc[-1] * 100)
                row["min_liquidity_change_pct"] = float(liq_returns.min() * 100)
                for level in LIQUIDITY_COLLAPSE_LEVELS:
                    pct = int(level * 100)
                    row[f"liquidity_collapse_{pct}pct"] = bool((liq_returns <= -level).any())

        rows.append(row)
    return pd.DataFrame(rows)


def quantile_outcome_table(
    research_rows: pd.DataFrame,
    feature: str,
    outcome: str,
    bins: int = 5,
) -> pd.DataFrame:
    """Simple leakage-free descriptive table for one feature versus one outcome."""
    if research_rows.empty or feature not in research_rows or outcome not in research_rows:
        return pd.DataFrame()
    d = research_rows[[feature, outcome]].copy()
    d[feature] = pd.to_numeric(d[feature], errors="coerce")
    d = d.dropna(subset=[feature, outcome])
    if len(d) < max(20, bins * 4):
        return pd.DataFrame()
    try:
        d["feature_bin"] = pd.qcut(d[feature], q=bins, duplicates="drop")
    except ValueError:
        return pd.DataFrame()
    grouped = d.groupby("feature_bin", observed=True)
    return grouped.agg(
        samples=(outcome, "size"),
        outcome_rate=(outcome, "mean"),
        feature_median=(feature, "median"),
    ).reset_index()


def chronological_split(
    frame: pd.DataFrame,
    timestamp_col: str,
    train_frac: float = 0.60,
    validation_frac: float = 0.20,
):
    """Chronological split only; never randomly shuffle launch events."""
    if frame.empty:
        return frame.copy(), frame.copy(), frame.copy()
    d = frame.copy()
    d[timestamp_col] = pd.to_datetime(d[timestamp_col], utc=True, errors="coerce")
    d = d.dropna(subset=[timestamp_col]).sort_values(timestamp_col).reset_index(drop=True)
    n = len(d)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + validation_frac))
    return d.iloc[:train_end].copy(), d.iloc[train_end:val_end].copy(), d.iloc[val_end:].copy()
