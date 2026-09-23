import pandas as pd
import pytest

from meme_launch_research import checkpoint_features, forward_outcomes


def test_checkpoint_features_uses_only_checkpoint_data():
    start = pd.Timestamp("2026-09-23T12:00:00Z")
    trades = pd.DataFrame({
        "timestamp": [
            start + pd.Timedelta(seconds=10),
            start + pd.Timedelta(seconds=20),
            start + pd.Timedelta(seconds=40),
        ],
        "side": ["buy", "sell", "buy"],
        "volume_usd": [100, 40, 1000],
        "price_usd": [1.0, 0.98, 1.5],
        "wallet_address": ["A", "B", "C"],
        "trade_id": ["1", "2", "3"],
    })
    row = checkpoint_features(trades, start, 30, initial_liquidity_usd=10000, current_liquidity_usd=10100)
    assert row["trade_count"] == 2
    assert row["gross_buy_usd"] == 100
    assert row["gross_sell_usd"] == 40
    assert row["unique_buyers"] == 1


def test_forward_outcome_path_order():
    start = pd.Timestamp("2026-09-23T12:00:00Z")
    path = pd.DataFrame({
        "timestamp": [
            start,
            start + pd.Timedelta(minutes=1),
            start + pd.Timedelta(minutes=2),
            start + pd.Timedelta(minutes=3),
        ],
        "price_usd": [1.0, 0.8, 2.1, 3.2],
        "liquidity_usd": [10000, 9000, 12000, 13000],
    })
    out = forward_outcomes(path, start, 1.0, entry_liquidity_usd=10000, horizons=[300])
    row = out.iloc[0]
    assert bool(row["hit_2x"]) is True
    assert bool(row["hit_2x_before_50dd"]) is True
    assert row["mae_before_2x_pct"] == pytest.approx(-20.0)


def test_target_after_severe_drawdown_not_tradable_success():
    start = pd.Timestamp("2026-09-23T12:00:00Z")
    path = pd.DataFrame({
        "timestamp": [
            start,
            start + pd.Timedelta(minutes=1),
            start + pd.Timedelta(minutes=2),
        ],
        "price_usd": [1.0, 0.45, 2.2],
    })
    out = forward_outcomes(path, start, 1.0, horizons=[300])
    row = out.iloc[0]
    assert bool(row["hit_2x"]) is True
    assert bool(row["hit_2x_before_50dd"]) is False
