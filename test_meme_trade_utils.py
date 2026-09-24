import pandas as pd

from meme_trade_utils import aggregate_ohlcv, select_bulk_plan_indices


def test_bulk_plan_selection_excludes_failed_gates_and_prioritises_passes():
    frame = pd.DataFrame(
        [
            {"Gate": "FAIL", "Decision": "HIGH PRIORITY", "Score": 99, "Liquidity": 999999},
            {"Gate": "PASS", "Decision": "WATCH", "Score": 70, "Liquidity": 100000},
            {"Gate": "PASS", "Decision": "SHORTLIST", "Score": 66, "Liquidity": 80000},
            {"Gate": "PASS", "Decision": "HIGH PRIORITY", "Score": 81, "Liquidity": 70000},
            {"Gate": "PASS", "Decision": "WATCH", "Score": 75, "Liquidity": 90000},
        ],
        index=[10, 11, 12, 13, 14],
    )

    result = select_bulk_plan_indices(frame, requested_limit=4, api_budget=3)

    assert result["ineligible"] == [10]
    assert result["selected"] == [13, 12, 14]
    assert result["deferred"] == [11]


def test_hourly_aggregation_builds_only_completed_four_hour_candles():
    timestamps = pd.date_range("2026-01-01T00:00:00Z", periods=10, freq="h")
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": range(10),
            "high": [x + 1 for x in range(10)],
            "low": [x - 1 for x in range(10)],
            "close": [x + 0.5 for x in range(10)],
            "volume": [10] * 10,
        }
    )

    out = aggregate_ohlcv(frame, "4h")

    # 00-03 and 04-07 are complete; 08-09 is still partial and excluded.
    assert len(out) == 2
    assert out.iloc[0]["open"] == 0
    assert out.iloc[0]["close"] == 3.5
    assert out.iloc[0]["high"] == 4
    assert out.iloc[0]["low"] == -1
    assert out.iloc[0]["volume"] == 40
