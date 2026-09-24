from meme_discovery_utils import (
    merge_discovery_universes,
    parse_gecko_new_pool_tokens,
    trim_discovery_universe,
)


def test_parse_gecko_new_pool_extracts_chain_and_token():
    payload = {
        "data": [
            {
                "id": "solana_POOL1",
                "type": "pool",
                "attributes": {
                    "address": "POOL1",
                    "name": "CAT / SOL",
                    "pool_created_at": "2026-09-24T08:00:00Z",
                    "fdv_usd": "50000",
                    "reserve_in_usd": "12000",
                },
                "relationships": {
                    "base_token": {
                        "data": {
                            "id": "solana_CATADDRESS",
                            "type": "token",
                        }
                    },
                    "network": {
                        "data": {
                            "id": "solana",
                            "type": "network",
                        }
                    },
                },
            }
        ]
    }

    parsed = parse_gecko_new_pool_tokens(
        payload,
        default_network="solana",
        allowed_chains={"solana"},
    )

    assert list(parsed) == ["solana:cataddress"]
    row = parsed["solana:cataddress"]
    assert row["chainId"] == "solana"
    assert row["tokenAddress"] == "CATADDRESS"
    assert row["sources"] == {"Gecko new pool"}
    assert row["gecko_pool_address"] == "POOL1"


def test_parse_gecko_new_pool_uses_default_network_when_relationship_missing():
    payload = {
        "data": [
            {
                "attributes": {"address": "0xpool"},
                "relationships": {
                    "base_token": {
                        "data": {
                            "id": "eth_0xtoken",
                            "type": "token",
                        }
                    }
                },
            }
        ]
    }

    parsed = parse_gecko_new_pool_tokens(
        payload,
        default_network="eth",
        allowed_chains={"ethereum"},
    )

    assert "ethereum:0xtoken" in parsed


def test_merge_preserves_discovery_sources_and_profile_metadata():
    dex = {
        "solana:abc": {
            "chainId": "solana",
            "tokenAddress": "ABC",
            "sources": {"Profile"},
            "profile_description": "cat meme",
            "profile_links": [{"type": "twitter", "url": "x"}],
            "boost_amount": 0,
            "boost_total": 0,
            "community_takeover": False,
            "_last_seen_ts": 1,
        }
    }
    gecko = {
        "solana:abc": {
            "chainId": "solana",
            "tokenAddress": "ABC",
            "sources": {"Gecko new pool"},
            "profile_description": "",
            "profile_links": [],
            "boost_amount": 0,
            "boost_total": 0,
            "community_takeover": False,
            "gecko_pool_address": "POOL",
            "_last_seen_ts": 2,
        }
    }

    merged = merge_discovery_universes(dex, gecko)
    row = merged["solana:abc"]

    assert row["sources"] == {"Profile", "Gecko new pool"}
    assert row["profile_description"] == "cat meme"
    assert row["gecko_pool_address"] == "POOL"
    assert row["_last_seen_ts"] == 2


def test_trim_keeps_most_recent_candidates():
    universe = {
        "solana:a": {"_last_seen_ts": 1},
        "solana:b": {"_last_seen_ts": 3},
        "solana:c": {"_last_seen_ts": 2},
    }

    trimmed = trim_discovery_universe(universe, max_tokens=2)

    assert list(trimmed) == ["solana:b", "solana:c"]
