import pandas as pd

from meme_trade_utils import aggregate_ohlcv, classify_meme_decision, select_bulk_plan_indices, classify_advanced_technical_entry, classify_advanced_entry_signal


def test_bulk_plan_selection_excludes_failed_gates_and_prioritises_passes():
    frame = pd.DataFrame(
        [
            {"Gate": "FAIL", "Decision": "HIGH PRIORITY", "Score": 99, "Liquidity": 999999},
            {"Gate": "PASS", "Decision": "TRADE WATCH", "Score": 70, "Liquidity": 100000},
            {"Gate": "PASS", "Decision": "SHORTLIST", "Score": 66, "Liquidity": 80000},
            {"Gate": "PASS", "Decision": "HIGH PRIORITY", "Score": 81, "Liquidity": 70000},
            {"Gate": "PASS", "Decision": "TRADE WATCH", "Score": 75, "Liquidity": 90000},
            {"Gate": "PASS", "Decision": "PASS", "Score": 90, "Liquidity": 1000000},
        ],
        index=[10, 11, 12, 13, 14, 15],
    )

    result = select_bulk_plan_indices(frame, requested_limit=4, api_budget=3)

    assert result["ineligible"] == [10, 15]
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


def test_decision_split_never_makes_failed_gate_trade_watch():
    assert classify_meme_decision(True, 82, 65) == "HIGH PRIORITY"
    assert classify_meme_decision(True, 70, 65) == "SHORTLIST"
    assert classify_meme_decision(True, 60, 65) == "TRADE WATCH"
    assert classify_meme_decision(False, 82, 65) == "DISCOVERY WATCH"
    assert classify_meme_decision(False, 60, 65) == "DISCOVERY WATCH"
    assert classify_meme_decision(False, 50, 65) == "PASS"



def test_advanced_high_rank_is_not_entry_without_entry_area_plan():
    technical = classify_advanced_technical_entry("PASS", "HIGH PRIORITY", "HIGH VOLATILITY — WAIT")
    assert technical == "WAIT"
    assert classify_advanced_entry_signal(technical, "PASS") == "WAIT"


def test_advanced_failed_gate_is_never_actionable():
    technical = classify_advanced_technical_entry("FAIL", "HIGH PRIORITY", "ENTRY AREA")
    assert technical == "AVOID"
    assert classify_advanced_entry_signal(technical, "PASS") == "AVOID"


def test_advanced_trade_watch_does_not_become_entry_qualified():
    technical = classify_advanced_technical_entry("PASS", "TRADE WATCH", "ENTRY AREA")
    assert technical == "WAIT"
    assert classify_advanced_entry_signal(technical, "PASS") == "WAIT"


def test_advanced_entry_requires_rank_plan_and_safety_pass():
    technical = classify_advanced_technical_entry("PASS", "SHORTLIST", "ENTRY AREA")
    assert technical == "QUALIFIED"
    assert classify_advanced_entry_signal(technical, "UNKNOWN") == "SAFETY UNKNOWN"
    assert classify_advanced_entry_signal(technical, "FAIL") == "SAFETY BLOCK"
    assert classify_advanced_entry_signal(technical, "PASS") == "ENTRY QUALIFIED"
