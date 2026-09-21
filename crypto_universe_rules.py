from __future__ import annotations

import re
from typing import Optional


STABLE_OR_CASH_BASES = {
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "EURC", "USD1",
    "BUSD", "USDP", "GUSD", "LUSD", "FRAX", "EUR", "GBP",
    "USDG", "RLUSD", "BFUSD", "USDS", "USDD", "USD0", "USD0PP", "AEUR",
}

LEVERAGED_SUFFIXES = (
    "UP", "DOWN", "BULL", "BEAR", "2L", "2S", "3L", "3S", "5L", "5S",
)

# Binance bStocks tokenized securities. Maintained from Binance's current
# bStocks spot / collateral announcements. These are securities exposures,
# not crypto-native assets for the CL Signal Crypto universe.
BINANCE_TOKENIZED_SECURITIES = {
    "AAOIB", "AAPLB", "ALABB", "AMATB", "AMDB", "AMZNB", "ARMB", "ASMLB",
    "ASTSB", "AVGOB", "AXTIB", "BABAB", "BEB", "BMNRB", "BNCB", "CBRSB",
    "COHRB", "COINB", "CRCLB", "CRDOB", "CRWVB", "DELLB", "DJTB", "DRAMB",
    "EWYB", "FLNCB", "GLWB", "GMEB", "GOOGLB", "GSB", "HOODB", "IBMB",
    "INTCB", "INTWB", "IRENB", "KORUB", "LITEB", "METAB", "MRVLB", "MSFTB",
    "MSTRB", "MUB", "MUUB", "MVLLB", "NBISB", "NFLXB", "NOKB", "NVDAB",
    "ORCLB", "PLTRB", "PYPLB", "QCOMB", "QNTB", "QQQB", "RKLBB", "SKHYB",
    "SMCIB", "SMHB", "SNDKB", "SNXXB", "SOXLB", "SOXSB", "SPCXB", "SPYB",
    "TQQQB", "TSLAB", "TSMB", "USARB", "WDCB",
}

# OKX Unified Tokenized Stocks / ETFs. Exact identifiers are used rather than
# a blanket X-prefix rule so native crypto assets such as XRP/XLM/XAUT remain
# eligible.
OKX_TOKENIZED_SECURITIES = {
    "XAAPL", "XAMD", "XAMAT", "XAMZN", "XAPP", "XASML", "XAVGO", "XBE",
    "XCOHR", "XCOIN", "XCRM", "XCRCL", "XCRWD", "XCRWV", "XCSCO", "XDELL",
    "XGEV", "XGOOGL", "XHOOD", "XIBM", "XINTC", "XIWM", "XJNJ", "XKLAC",
    "XLITE", "XLLY", "XLRCX", "XMETA", "XMRVL", "XMSFT", "XMSTR", "XMU",
    "XNBIS", "XNFLX", "XNVDA", "XORCL", "XPLTR", "XQCOM", "XQQQ", "XSKHY",
    "XSMH", "XSNDK", "XSOXL", "XSPCX", "XSPY", "XTSLA", "XTSM", "XTQQQ",
    "XUNH", "XWDC", "XXIAOMI",
}


def is_leveraged_token(base: str) -> bool:
    base = str(base or "").upper().strip()
    return any(
        base.endswith(suffix) and len(base) > len(suffix) + 2
        for suffix in LEVERAGED_SUFFIXES
    )


def crypto_universe_exclusion_reason(
    base: str,
    exchange_id: Optional[str] = None,
) -> Optional[str]:
    base = str(base or "").upper().strip()
    exchange_id = str(exchange_id or "").lower().strip()

    if not base or re.fullmatch(r"[A-Z0-9]{1,20}", base) is None:
        return "invalid_symbol"
    if base in STABLE_OR_CASH_BASES:
        return "stable_or_cash"
    if is_leveraged_token(base):
        return "leveraged_token"
    if exchange_id == "binance" and base in BINANCE_TOKENIZED_SECURITIES:
        return "tokenized_security"
    if exchange_id == "okx" and base in OKX_TOKENIZED_SECURITIES:
        return "tokenized_security"
    return None


def is_crypto_universe_asset(base: str, exchange_id: Optional[str] = None) -> bool:
    return crypto_universe_exclusion_reason(base, exchange_id) is None
