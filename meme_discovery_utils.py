from __future__ import annotations

from copy import deepcopy
from typing import Dict, Iterable, Optional


GECKO_TO_DEX_CHAIN = {
    "solana": "solana",
    "base": "base",
    "eth": "ethereum",
    "bsc": "bsc",
    "robinhood": "robinhood",
    "arbitrum": "arbitrum",
    "polygon_pos": "polygon",
}


def token_key(chain_id: str, token_address: str) -> str:
    return f"{chain_id}:{token_address}".lower()


def _relationship_id(item: dict, name: str) -> str:
    relationship = ((item.get("relationships") or {}).get(name) or {})
    data = relationship.get("data") or {}
    return str(data.get("id") or "")


def _strip_network_prefix(resource_id: str, network_id: str) -> str:
    value = str(resource_id or "")
    prefix = f"{network_id}_"
    if value.lower().startswith(prefix.lower()):
        return value[len(prefix):]
    # GeckoTerminal relationship IDs are normally network_address. Preserve
    # addresses containing no prefix rather than guessing beyond the first "_".
    return value


def parse_gecko_new_pool_tokens(
    payload: dict,
    *,
    default_network: str = "",
    allowed_chains: Optional[Iterable[str]] = None,
) -> Dict[str, Dict]:
    """
    Convert GeckoTerminal new-pool resources into the existing discovery-token
    metadata shape. Pool attributes are retained as discovery context; live
    scoring still comes from the existing pair-data enrichment layer.
    """
    allowed = {str(x).lower() for x in (allowed_chains or [])}
    out: Dict[str, Dict] = {}

    for item in (payload or {}).get("data") or []:
        if not isinstance(item, dict):
            continue
        network = _relationship_id(item, "network") or str(default_network or "")
        network = network.lower()
        chain = GECKO_TO_DEX_CHAIN.get(network)
        if not chain or (allowed and chain not in allowed):
            continue

        base_id = _relationship_id(item, "base_token")
        address = _strip_network_prefix(base_id, network)
        if not address:
            continue

        attrs = item.get("attributes") or {}
        key = token_key(chain, address)
        out[key] = {
            "chainId": chain,
            "tokenAddress": address,
            "sources": {"Gecko new pool"},
            "profile_description": "",
            "profile_links": [],
            "boost_amount": 0.0,
            "boost_total": 0.0,
            "community_takeover": False,
            "gecko_pool_address": str(attrs.get("address") or ""),
            "gecko_pool_name": str(attrs.get("name") or ""),
            "gecko_pool_created_at": str(attrs.get("pool_created_at") or ""),
            "gecko_market_cap_usd": attrs.get("market_cap_usd"),
            "gecko_fdv_usd": attrs.get("fdv_usd"),
            "gecko_reserve_usd": attrs.get("reserve_in_usd"),
        }
    return out


def merge_discovery_universes(*universes: Dict[str, Dict]) -> Dict[str, Dict]:
    """Merge discovery sources without losing source provenance."""
    merged: Dict[str, Dict] = {}

    for universe in universes:
        for key, incoming in (universe or {}).items():
            incoming = deepcopy(incoming)
            incoming["sources"] = set(incoming.get("sources") or [])
            if key not in merged:
                merged[key] = incoming
                continue

            current = merged[key]
            current["sources"] = set(current.get("sources") or []) | incoming["sources"]

            if not current.get("profile_description") and incoming.get("profile_description"):
                current["profile_description"] = incoming["profile_description"]
            if incoming.get("profile_links"):
                existing_links = list(current.get("profile_links") or [])
                seen = {repr(x) for x in existing_links}
                for link in incoming.get("profile_links") or []:
                    if repr(link) not in seen:
                        existing_links.append(link)
                        seen.add(repr(link))
                current["profile_links"] = existing_links

            current["boost_amount"] = max(
                float(current.get("boost_amount") or 0),
                float(incoming.get("boost_amount") or 0),
            )
            current["boost_total"] = max(
                float(current.get("boost_total") or 0),
                float(incoming.get("boost_total") or 0),
            )
            current["community_takeover"] = bool(
                current.get("community_takeover") or incoming.get("community_takeover")
            )

            for field in [
                "gecko_pool_address",
                "gecko_pool_name",
                "gecko_pool_created_at",
                "gecko_market_cap_usd",
                "gecko_fdv_usd",
                "gecko_reserve_usd",
                "_last_seen_ts",
            ]:
                if incoming.get(field) not in (None, ""):
                    current[field] = incoming[field]

    return merged


def trim_discovery_universe(
    universe: Dict[str, Dict],
    max_tokens: int = 1000,
) -> Dict[str, Dict]:
    """Keep the most recently observed candidates while preserving dict order."""
    if len(universe or {}) <= max_tokens:
        return dict(universe or {})

    ranked = sorted(
        (universe or {}).items(),
        key=lambda kv: float((kv[1] or {}).get("_last_seen_ts") or 0),
        reverse=True,
    )
    return dict(ranked[: max(1, int(max_tokens))])
