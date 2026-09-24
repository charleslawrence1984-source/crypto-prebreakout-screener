from __future__ import annotations

import re
from typing import Dict


# Exact symbols/names are intentionally conservative. The relevance filter is
# for obvious non-meme assets only; ambiguous tokens stay in the discovery set.
STABLE_SYMBOLS = {
    "USDT", "USDC", "USDS", "DAI", "USDE", "FDUSD", "TUSD", "PYUSD",
    "FRAX", "LUSD", "GUSD", "EURC", "EURS", "USD0", "USDP",
}

WRAPPED_OR_MAJOR_SYMBOLS = {
    "BTC", "WBTC", "TBTC", "CBBTC",
    "ETH", "WETH", "STETH", "WSTETH", "RETH", "CBETH",
    "SOL", "WSOL", "JITOSOL", "MSOL", "BSOL",
    "BNB", "WBNB",
}

STABLE_NAMES = {
    "tether", "tether usd", "usd coin", "dai", "paypal usd",
    "first digital usd", "trueusd", "frax", "usde", "ethena usde",
}

MAJOR_NAMES = {
    "bitcoin", "wrapped bitcoin", "ethereum", "wrapped ether",
    "wrapped ethereum", "solana", "wrapped solana", "bnb", "wrapped bnb",
}


HIGH_CONFIDENCE_PATTERNS = [
    (
        "Tokenized stock/equity",
        re.compile(
            r"\b(tokeni[sz]ed\s+(stock|equity|shares?)|"
            r"(stock|equity|share)\s+token|"
            r"b\s*stocks?\b|x\s*stocks?\b|"
            r"wrapped\s+(stock|equity))",
            re.IGNORECASE,
        ),
    ),
    (
        "SPV / securities token",
        re.compile(
            r"\b(spv\s+token|special\s+purpose\s+vehicle|"
            r"security\s+token|securities\s+token)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "RWA / bond / treasury token",
        re.compile(
            r"\b(tokeni[sz]ed\s+(bond|treasury|real[- ]world\s+asset)|"
            r"(bond|treasury)\s+token|rwa\s+token)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Stablecoin / pegged asset",
        re.compile(
            r"\b(stablecoin|usd[- ]pegged|dollar[- ]pegged|"
            r"pegged\s+usd|fiat[- ]backed)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Wrapped / bridged major asset",
        re.compile(
            r"\b(wrapped\s+(bitcoin|btc|ether|ethereum|eth|solana|sol|bnb)|"
            r"bridged\s+(bitcoin|btc|ether|ethereum|eth|solana|sol|bnb))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Liquid-staking derivative",
        re.compile(
            r"\b(liquid\s+staked|liquid\s+staking\s+token|"
            r"staked\s+(ether|ethereum|eth|solana|sol))\b",
            re.IGNORECASE,
        ),
    ),
]


def classify_meme_relevance(pair: Dict, meta: Dict | None = None) -> Dict[str, str]:
    """
    High-confidence pre-ranking relevance filter.

    Returns KEEP unless the asset clearly belongs to a non-meme category.
    This is deliberately asymmetric: false negatives are more damaging to
    discovery than allowing an uncertain asset through for later scoring.
    """
    meta = meta or {}
    base = pair.get("baseToken") or {}
    symbol = str(base.get("symbol") or "").strip()
    name = str(base.get("name") or "").strip()
    description = str(meta.get("profile_description") or "").strip()
    gecko_name = str(meta.get("gecko_pool_name") or "").strip()

    symbol_upper = symbol.upper()
    name_lower = name.lower().strip()

    if symbol_upper in STABLE_SYMBOLS or name_lower in STABLE_NAMES:
        return {
            "status": "EXCLUDE",
            "reason": "Stablecoin / fiat-pegged asset",
            "confidence": "HIGH",
        }

    if symbol_upper in WRAPPED_OR_MAJOR_SYMBOLS or name_lower in MAJOR_NAMES:
        return {
            "status": "EXCLUDE",
            "reason": "Major / wrapped / staking asset",
            "confidence": "HIGH",
        }

    # Name is the strongest identity field. Description and pool name are used
    # only for explicit financial-token phrases, never vague keywords alone.
    identity_text = " | ".join(x for x in [name, description, gecko_name] if x)
    for reason, pattern in HIGH_CONFIDENCE_PATTERNS:
        if pattern.search(identity_text):
            return {
                "status": "EXCLUDE",
                "reason": reason,
                "confidence": "HIGH",
            }

    return {
        "status": "KEEP",
        "reason": "",
        "confidence": "UNDETERMINED",
    }
