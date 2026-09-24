from meme_safety import (
    finalize_entry_signal,
    merge_safety_results,
    parse_goplus_security,
    parse_honeypot_check,
    parse_rugcheck_report,
)


def test_safe_solana_report_passes_core_safety_gate():
    report = {
        "token": {
            "mintAuthority": None,
            "freezeAuthority": None,
            "supply": "1000000",
        },
        "creatorBalance": 20000,
        "topHolders": [
            {"pct": 7.0, "insider": False},
            {"pct": 6.0, "insider": False},
            {"pct": 5.0, "insider": False},
        ],
        "markets": [{"lp": {"lpLockedPct": 100}}],
        "risks": [],
        "score_normalised": 12,
        "insiderNetworks": [],
    }
    result = parse_rugcheck_report(report)
    assert result["Safety Gate"] == "PASS"
    assert result["Mint Authority"] == "RENOUNCED"
    assert result["Freeze Authority"] == "RENOUNCED"
    assert result["LP Locked %"] == 100


def test_solana_active_authority_or_unlocked_lp_blocks_entry():
    report = {
        "token": {
            "mintAuthority": "AUTHORITY",
            "freezeAuthority": None,
            "supply": "1000000",
        },
        "creatorBalance": 10000,
        "topHolders": [{"pct": 5.0, "insider": False}],
        "markets": [{"lp": {"lpLockedPct": 20}}],
        "risks": [],
        "score_normalised": 10,
        "insiderNetworks": [],
    }
    result = parse_rugcheck_report(report)
    assert result["Safety Gate"] == "FAIL"
    assert "Mint authority still active" in result["Safety Blockers"]
    assert "LP locked 20.0%" in result["Safety Blockers"]


def test_incomplete_solana_report_is_unknown_not_pass():
    report = {
        "token": {"mintAuthority": None, "freezeAuthority": None, "supply": "1000000"},
        "topHolders": [{"pct": 5.0, "insider": False}],
        "risks": [],
    }
    result = parse_rugcheck_report(report)
    assert result["Safety Gate"] == "UNKNOWN"
    assert "LP lock" in result["Safety Warnings"]


def test_goplus_honeypot_admin_and_unlocked_lp_are_blocked():
    address = "0xabc"
    payload = {
        "result": {
            address: {
                "is_honeypot": "1",
                "is_open_source": "1",
                "is_mintable": "1",
                "owner_change_balance": "0",
                "transfer_pausable": "0",
                "is_blacklisted": "0",
                "slippage_modifiable": "0",
                "can_take_back_ownership": "0",
                "hidden_owner": "0",
                "selfdestruct": "0",
                "cannot_buy": "0",
                "cannot_sell_all": "0",
                "honeypot_with_same_creator": "0",
                "is_airdrop_scam": "0",
                "buy_tax": "0",
                "sell_tax": "0",
                "holders": [{"percent": "0.05", "is_locked": "0", "tag": ""}],
                "lp_holders": [{"percent": "1", "is_locked": "0", "tag": ""}],
            }
        }
    }
    result = parse_goplus_security(payload, address)
    assert result["Safety Gate"] == "FAIL"
    assert result["Honeypot"] == "YES"
    assert "mintable" in result["Safety Blockers"].lower()
    assert "LP locked/burned 0.0%" in result["Safety Blockers"]


def test_clean_goplus_result_can_pass():
    address = "0xabc"
    payload = {
        "result": {
            address: {
                "is_honeypot": "0",
                "is_open_source": "1",
                "is_mintable": "0",
                "owner_change_balance": "0",
                "transfer_pausable": "0",
                "is_blacklisted": "0",
                "slippage_modifiable": "0",
                "can_take_back_ownership": "0",
                "hidden_owner": "0",
                "selfdestruct": "0",
                "cannot_buy": "0",
                "cannot_sell_all": "0",
                "honeypot_with_same_creator": "0",
                "is_airdrop_scam": "0",
                "buy_tax": "0",
                "sell_tax": "0",
                "creator_percent": "0.02",
                "owner_percent": "0.01",
                "holders": [
                    {"percent": "0.08", "is_locked": "0", "tag": ""},
                    {"percent": "0.06", "is_locked": "0", "tag": ""},
                ],
                "lp_holders": [
                    {"percent": "0.90", "is_locked": "1", "tag": "Locker"},
                    {"percent": "0.10", "is_locked": "0", "tag": ""},
                ],
            }
        }
    }
    result = parse_goplus_security(payload, address)
    assert result["Safety Gate"] == "PASS"
    assert result["Honeypot"] == "NO"
    assert result["LP Locked %"] == 90.0


def test_honeypot_simulation_failure_blocks():
    result = parse_honeypot_check(
        {
            "simulationSuccess": False,
            "honeypotResult": {"isHoneypot": True, "honeypotReason": "cannot sell"},
            "summary": {"risk": "honeypot", "riskLevel": 100, "flags": []},
        }
    )
    assert result["Safety Gate"] == "FAIL"
    assert result["Sell Test"] == "FAIL"
    assert result["Honeypot"] == "YES"


def test_technical_qualification_needs_safety_pass_for_entry():
    safety = {
        "Safety Gate": "PASS",
        "Safety Blockers": "",
        "Safety Warnings": "",
    }
    result = finalize_entry_signal("QUALIFIED", safety, position_liquidity_pct=0.1)
    assert result["Entry Signal"] == "ENTRY QUALIFIED"


def test_unknown_safety_never_qualifies_entry():
    safety = {
        "Safety Gate": "UNKNOWN",
        "Safety Blockers": "",
        "Safety Warnings": "provider unavailable",
    }
    result = finalize_entry_signal("QUALIFIED", safety)
    assert result["Entry Signal"] == "SAFETY UNKNOWN"


def test_liquidity_drop_over_25_percent_blocks_even_if_provider_passes():
    safety = {
        "Safety Gate": "PASS",
        "Safety Blockers": "",
        "Safety Warnings": "",
    }
    result = finalize_entry_signal(
        "QUALIFIED",
        safety,
        position_liquidity_pct=0.1,
        liquidity_change_pct=-30,
    )
    assert result["Safety Gate"] == "FAIL"
    assert result["Entry Signal"] == "SAFETY BLOCK"
    assert "Liquidity fell 30.0%" in result["Safety Blockers"]


def test_position_above_half_percent_of_pool_blocks():
    safety = {
        "Safety Gate": "PASS",
        "Safety Blockers": "",
        "Safety Warnings": "",
    }
    result = finalize_entry_signal("QUALIFIED", safety, position_liquidity_pct=0.75)
    assert result["Safety Gate"] == "FAIL"
    assert result["Entry Signal"] == "SAFETY BLOCK"
