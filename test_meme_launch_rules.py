from meme_launch_rules import LAUNCH_DEFAULTS, score_launch_candidate


NOW_MS = 1_800_000_000_000


def launch_pair(
    *,
    age_minutes=30,
    liquidity=50_000,
    vol5=20_000,
    vol1h=80_000,
    buys5=60,
    sells5=40,
    buys1h=240,
    sells1h=160,
    ch5=5,
    ch1=15,
):
    return {
        "chainId": "solana",
        "dexId": "test",
        "pairAddress": "POOL",
        "pairCreatedAt": NOW_MS - age_minutes * 60_000,
        "priceUsd": "0.0001",
        "baseToken": {"symbol": "MEME", "name": "Meme", "address": "TOKEN"},
        "quoteToken": {"symbol": "SOL", "name": "Solana"},
        "liquidity": {"usd": liquidity},
        "volume": {"m5": vol5, "h1": vol1h},
        "txns": {
            "m5": {"buys": buys5, "sells": sells5},
            "h1": {"buys": buys1h, "sells": sells1h},
        },
        "priceChange": {"m5": ch5, "h1": ch1},
    }


def test_constructive_launch_can_become_launch_leader_without_24h_data():
    result = score_launch_candidate(launch_pair(), now_ms=NOW_MS)

    assert result["Launch Gate"] == "PASS"
    assert result["Launch Decision"] == "LAUNCH LEADER"
    assert result["Launch Score"] >= LAUNCH_DEFAULTS["strong_score"]
    assert result["Age min"] == 30.0


def test_low_liquidity_launch_is_avoided():
    result = score_launch_candidate(
        launch_pair(liquidity=5_000),
        now_ms=NOW_MS,
    )

    assert result["Launch Gate"] == "FAIL"
    assert result["Launch Decision"] == "LAUNCH AVOID"
    assert "Very low launch liquidity" in result["Launch Gate Reasons"]


def test_vertical_launch_is_chase_risk():
    result = score_launch_candidate(
        launch_pair(ch5=50),
        now_ms=NOW_MS,
    )

    assert result["Launch Gate"] == "FAIL"
    assert "Vertical / chase-risk launch" in result["Launch Gate Reasons"]


def test_older_than_two_hours_is_not_a_launch_candidate():
    result = score_launch_candidate(
        launch_pair(age_minutes=150),
        now_ms=NOW_MS,
    )

    assert result["Launch Gate"] == "FAIL"
    assert result["Launch Decision"] == "LAUNCH AVOID"
    assert "Older than launch lane" in result["Launch Gate Reasons"]


def test_under_five_minutes_is_capped_at_data_building():
    result = score_launch_candidate(
        launch_pair(
            age_minutes=1.6,
            liquidity=100_000,
            vol5=50_000,
            buys5=120,
            sells5=60,
        ),
        now_ms=NOW_MS,
    )

    assert result["Launch Gate"] == "PASS"
    assert result["Launch Score"] >= LAUNCH_DEFAULTS["strong_score"]
    assert result["Launch Decision"] == "DATA BUILDING"
    assert "Under 5 minutes old" in result["Launch Cautions"]
    assert "capped at DATA BUILDING" in result["Decision Constraint"]


def test_severe_five_minute_collapse_is_launch_avoid():
    result = score_launch_candidate(
        launch_pair(
            age_minutes=109.6,
            liquidity=75_000,
            vol5=30_000,
            buys5=90,
            sells5=60,
            ch5=-39.24,
            ch1=10,
        ),
        now_ms=NOW_MS,
    )

    assert result["Launch Gate"] == "FAIL"
    assert result["Launch Decision"] == "LAUNCH AVOID"
    assert "Severe 5m price collapse" in result["Launch Gate Reasons"]


def test_heavy_but_not_severe_drawdown_is_caution_only():
    result = score_launch_candidate(
        launch_pair(ch5=-25),
        now_ms=NOW_MS,
    )

    assert result["Launch Gate"] == "PASS"
    assert "Heavy 5m drawdown" in result["Launch Cautions"]
