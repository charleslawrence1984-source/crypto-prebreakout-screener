from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

import requests


RUGCHECK_API = "https://api.rugcheck.xyz/v1"
GOPLUS_API = "https://api.gopluslabs.io/api/v1"
HONEYPOT_API = "https://api.honeypot.is"

GOPLUS_CHAIN_IDS = {
    "ethereum": "1",
    "eth": "1",
    "bsc": "56",
    "base": "8453",
    "arbitrum": "42161",
    "polygon": "137",
    "polygon_pos": "137",
    "robinhood": "4663",
}
HONEYPOT_CHAIN_IDS = {
    "ethereum": 1,
    "eth": 1,
    "bsc": 56,
    "base": 8453,
}

SAFETY_DEFAULTS = {
    "max_sell_tax_pct": 10.0,
    "max_buy_tax_pct": 10.0,
    "max_creator_pct": 10.0,
    "max_owner_pct": 10.0,
    "max_single_holder_pct": 15.0,
    "max_top10_holder_pct": 40.0,
    "min_lp_locked_pct": 50.0,
    "max_risk_level": 59.0,
    "max_rugcheck_score_normalised": 59.0,
}


def _safe(value: Any, default: float = float("nan")) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _pct_fraction(value: Any) -> float:
    number = _safe(value)
    if not math.isfinite(number):
        return float("nan")
    return number * 100.0 if abs(number) <= 1.0 else number


def _truth(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def _join(items: Iterable[str]) -> str:
    return "; ".join(dict.fromkeys(str(x) for x in items if str(x).strip()))


def _base_result(provider: str) -> Dict[str, Any]:
    return {
        "Safety Gate": "UNKNOWN",
        "Safety Provider": provider,
        "Safety Blockers": "",
        "Safety Warnings": "",
        "Safety Coverage": "",
        "Sell Test": "UNKNOWN",
        "Honeypot": "UNKNOWN",
        "Risk Level": float("nan"),
        "Risk Label": "",
        "Buy Tax %": float("nan"),
        "Sell Tax %": float("nan"),
        "LP Locked %": float("nan"),
        "Largest Holder %": float("nan"),
        "Top 10 Holders %": float("nan"),
        "Creator %": float("nan"),
        "Owner %": float("nan"),
        "Mint Authority": "UNKNOWN",
        "Freeze Authority": "UNKNOWN",
        "Contract Open Source": "UNKNOWN",
        "Insider Networks": float("nan"),
        "Safety Checked At": datetime.now(timezone.utc).isoformat(),
    }


def parse_rugcheck_report(payload: Dict[str, Any], cfg: Dict | None = None) -> Dict[str, Any]:
    rules = dict(SAFETY_DEFAULTS)
    if cfg:
        rules.update(cfg)
    out = _base_result("RugCheck")
    blockers: list[str] = []
    warnings: list[str] = []
    coverage: list[str] = []

    if not isinstance(payload, dict) or not payload:
        out["Safety Coverage"] = "No RugCheck report returned"
        return out

    token = payload.get("token") or {}
    top_holders = payload.get("topHolders") or []
    markets = payload.get("markets") or []
    risks = payload.get("risks") or []
    insider_networks = payload.get("insiderNetworks") or []

    mint_key_present = isinstance(token, dict) and "mintAuthority" in token
    freeze_key_present = isinstance(token, dict) and "freezeAuthority" in token
    mint_authority = token.get("mintAuthority") if isinstance(token, dict) else None
    freeze_authority = token.get("freezeAuthority") if isinstance(token, dict) else None
    out["Mint Authority"] = (
        "RENOUNCED" if mint_key_present and not mint_authority
        else str(mint_authority) if mint_key_present else "UNKNOWN"
    )
    out["Freeze Authority"] = (
        "RENOUNCED" if freeze_key_present and not freeze_authority
        else str(freeze_authority) if freeze_key_present else "UNKNOWN"
    )
    if mint_key_present:
        coverage.append("mint authority")
        if mint_authority:
            blockers.append("Mint authority still active")
    if freeze_key_present:
        coverage.append("freeze authority")
        if freeze_authority:
            blockers.append("Freeze authority still active")

    holder_pcts = []
    insider_pct = 0.0
    for holder in top_holders if isinstance(top_holders, list) else []:
        pct = _safe((holder or {}).get("pct"))
        if math.isfinite(pct):
            holder_pcts.append(pct)
            if bool((holder or {}).get("insider")):
                insider_pct += pct
    if holder_pcts:
        coverage.append("holder concentration")
        out["Largest Holder %"] = max(holder_pcts)
        out["Top 10 Holders %"] = sum(sorted(holder_pcts, reverse=True)[:10])
        if out["Largest Holder %"] > rules["max_single_holder_pct"]:
            blockers.append(
                f"Largest holder {out['Largest Holder %']:.1f}% > "
                f"{rules['max_single_holder_pct']:.0f}%"
            )
        if out["Top 10 Holders %"] > rules["max_top10_holder_pct"]:
            blockers.append(
                f"Top holders {out['Top 10 Holders %']:.1f}% > "
                f"{rules['max_top10_holder_pct']:.0f}%"
            )

    supply = _safe(token.get("supply")) if isinstance(token, dict) else float("nan")
    creator_balance = _safe(payload.get("creatorBalance"))
    if math.isfinite(supply) and supply > 0 and math.isfinite(creator_balance):
        creator_pct = creator_balance / supply * 100.0
        out["Creator %"] = creator_pct
        coverage.append("creator concentration")
        if creator_pct > rules["max_creator_pct"]:
            blockers.append(
                f"Creator holds {creator_pct:.1f}% > {rules['max_creator_pct']:.0f}%"
            )

    locked_values = []
    for market in markets if isinstance(markets, list) else []:
        lp = (market or {}).get("lp") or {}
        locked = _safe(lp.get("lpLockedPct"))
        if math.isfinite(locked):
            locked_values.append(locked)
    if locked_values:
        out["LP Locked %"] = max(locked_values)
        coverage.append("LP lock")
        if out["LP Locked %"] < rules["min_lp_locked_pct"]:
            blockers.append(
                f"LP locked {out['LP Locked %']:.1f}% < {rules['min_lp_locked_pct']:.0f}%"
            )

    normalized = _safe(
        payload.get("score_normalised"),
        _safe(payload.get("scoreNormalized")),
    )
    if math.isfinite(normalized):
        out["Risk Level"] = normalized
        coverage.append("RugCheck risk score")
        if normalized > rules["max_rugcheck_score_normalised"]:
            blockers.append(f"RugCheck risk score {normalized:.0f} is too high")
        elif normalized >= 30:
            warnings.append(f"RugCheck risk score {normalized:.0f}")

    danger_risks = []
    warn_risks = []
    for risk in risks if isinstance(risks, list) else []:
        level = str((risk or {}).get("level") or "").lower()
        name = str((risk or {}).get("name") or "RugCheck risk")
        if level in {"danger", "critical", "high"}:
            danger_risks.append(name)
        elif level in {"warn", "warning", "medium"}:
            warn_risks.append(name)
    if isinstance(risks, list):
        coverage.append("detected risks")
    if danger_risks:
        blockers.extend(f"RugCheck danger: {name}" for name in danger_risks)
    warnings.extend(f"RugCheck warning: {name}" for name in warn_risks[:5])

    out["Insider Networks"] = len(insider_networks) if isinstance(insider_networks, list) else 0
    if insider_networks:
        coverage.append("insider networks")
        supply_raw = supply
        for network in insider_networks:
            token_amount = _safe((network or {}).get("tokenAmount"))
            if math.isfinite(supply_raw) and supply_raw > 0 and math.isfinite(token_amount):
                network_pct = token_amount / supply_raw * 100.0
                if network_pct > 10:
                    blockers.append(f"Insider network controls about {network_pct:.1f}%")
                elif network_pct > 3:
                    warnings.append(f"Insider network controls about {network_pct:.1f}%")
            else:
                warnings.append("Insider network detected")

    critical_complete = (
        mint_key_present
        and freeze_key_present
        and bool(holder_pcts)
        and bool(locked_values)
    )

    out["Safety Blockers"] = _join(blockers)
    out["Safety Warnings"] = _join(warnings)
    out["Safety Coverage"] = _join(coverage)
    if blockers:
        out["Safety Gate"] = "FAIL"
    elif critical_complete:
        out["Safety Gate"] = "PASS"
    else:
        out["Safety Gate"] = "UNKNOWN"
        missing = []
        if not mint_key_present or not freeze_key_present:
            missing.append("token authorities")
        if not holder_pcts:
            missing.append("holder concentration")
        if not locked_values:
            missing.append("LP lock")
        if missing:
            out["Safety Warnings"] = _join(
                warnings + ["Missing critical safety data: " + ", ".join(missing)]
            )
    return out


def parse_goplus_security(
    payload: Dict[str, Any],
    token_address: str,
    cfg: Dict | None = None,
) -> Dict[str, Any]:
    rules = dict(SAFETY_DEFAULTS)
    if cfg:
        rules.update(cfg)
    out = _base_result("GoPlus")
    blockers: list[str] = []
    warnings: list[str] = []
    coverage: list[str] = []

    result = (payload or {}).get("result") or {}
    token = None
    if isinstance(result, dict):
        token = result.get(token_address) or result.get(token_address.lower())
        if token is None:
            for key, value in result.items():
                if str(key).lower() == token_address.lower():
                    token = value
                    break
    if not isinstance(token, dict):
        out["Safety Coverage"] = "No GoPlus token-security result returned"
        return out

    binary_fail_fields = {
        "is_honeypot": "GoPlus flags token as honeypot",
        "cannot_buy": "Token cannot be bought normally",
        "cannot_sell_all": "Contract restricts selling all tokens",
        "is_mintable": "Token is mintable",
        "owner_change_balance": "Owner can modify holder balances",
        "transfer_pausable": "Transfers can be paused",
        "is_blacklisted": "Contract has blacklist controls",
        "slippage_modifiable": "Owner can modify trading tax/slippage",
        "can_take_back_ownership": "Ownership can be reclaimed",
        "hidden_owner": "Contract has a hidden owner",
        "selfdestruct": "Contract can self-destruct",
        "honeypot_with_same_creator": "Creator has deployed a honeypot before",
        "is_airdrop_scam": "GoPlus flags token as an airdrop scam",
    }
    known_binary = 0
    for field, label in binary_fail_fields.items():
        state = _truth(token.get(field))
        if state is not None:
            known_binary += 1
            coverage.append(field)
            if state:
                blockers.append(label)

    honeypot = _truth(token.get("is_honeypot"))
    out["Honeypot"] = (
        "YES" if honeypot is True else "NO" if honeypot is False else "UNKNOWN"
    )
    open_source = _truth(token.get("is_open_source"))
    out["Contract Open Source"] = (
        "YES" if open_source is True else "NO" if open_source is False else "UNKNOWN"
    )
    if open_source is False:
        blockers.append("Contract is not open source")
    if open_source is not None:
        coverage.append("open source")

    buy_tax = _pct_fraction(token.get("buy_tax"))
    sell_tax = _pct_fraction(token.get("sell_tax"))
    out["Buy Tax %"] = buy_tax
    out["Sell Tax %"] = sell_tax
    if math.isfinite(buy_tax):
        coverage.append("buy tax")
        if buy_tax > rules["max_buy_tax_pct"]:
            blockers.append(f"Buy tax {buy_tax:.1f}% > {rules['max_buy_tax_pct']:.0f}%")
    if math.isfinite(sell_tax):
        coverage.append("sell tax")
        if sell_tax > rules["max_sell_tax_pct"]:
            blockers.append(f"Sell tax {sell_tax:.1f}% > {rules['max_sell_tax_pct']:.0f}%")
        elif sell_tax > 5:
            warnings.append(f"Sell tax {sell_tax:.1f}%")

    creator_pct = _pct_fraction(token.get("creator_percent"))
    owner_pct = _pct_fraction(token.get("owner_percent"))
    out["Creator %"] = creator_pct
    out["Owner %"] = owner_pct
    if math.isfinite(creator_pct):
        coverage.append("creator concentration")
        if creator_pct > rules["max_creator_pct"]:
            blockers.append(
                f"Creator holds {creator_pct:.1f}% > {rules['max_creator_pct']:.0f}%"
            )
    if math.isfinite(owner_pct):
        coverage.append("owner concentration")
        if owner_pct > rules["max_owner_pct"]:
            blockers.append(
                f"Owner holds {owner_pct:.1f}% > {rules['max_owner_pct']:.0f}%"
            )

    holder_pcts = []
    for holder in token.get("holders") or []:
        if _truth((holder or {}).get("is_locked")) is True:
            continue
        tag = str((holder or {}).get("tag") or "").lower()
        if any(word in tag for word in ("burn", "null", "dead", "lock")):
            continue
        pct = _pct_fraction((holder or {}).get("percent"))
        if math.isfinite(pct):
            holder_pcts.append(pct)
    if holder_pcts:
        coverage.append("holder concentration")
        out["Largest Holder %"] = max(holder_pcts)
        out["Top 10 Holders %"] = sum(sorted(holder_pcts, reverse=True)[:10])
        if out["Largest Holder %"] > rules["max_single_holder_pct"]:
            blockers.append(
                f"Largest unlocked holder {out['Largest Holder %']:.1f}% > "
                f"{rules['max_single_holder_pct']:.0f}%"
            )
        if out["Top 10 Holders %"] > rules["max_top10_holder_pct"]:
            blockers.append(
                f"Top unlocked holders {out['Top 10 Holders %']:.1f}% > "
                f"{rules['max_top10_holder_pct']:.0f}%"
            )

    lp_holders = token.get("lp_holders") or []
    locked_lp = 0.0
    lp_seen = False
    for holder in lp_holders if isinstance(lp_holders, list) else []:
        pct = _pct_fraction((holder or {}).get("percent"))
        if not math.isfinite(pct):
            continue
        lp_seen = True
        tag = str((holder or {}).get("tag") or "").lower()
        locked = _truth((holder or {}).get("is_locked")) is True
        burned = any(word in tag for word in ("burn", "null", "dead"))
        if locked or burned:
            locked_lp += pct
    if lp_seen:
        coverage.append("LP lock/burn")
        out["LP Locked %"] = min(100.0, locked_lp)
        if out["LP Locked %"] < rules["min_lp_locked_pct"]:
            blockers.append(
                f"LP locked/burned {out['LP Locked %']:.1f}% < "
                f"{rules['min_lp_locked_pct']:.0f}%"
            )

    fake_token = token.get("fake_token")
    if isinstance(fake_token, dict) and _truth(fake_token.get("value")) is True:
        blockers.append("GoPlus flags token as a fake/copy token")

    other_risks = str(token.get("other_potential_risks") or "").strip()
    if other_risks:
        warnings.append("GoPlus potential risk: " + other_risks[:240])

    critical_complete = (
        honeypot is not None
        and open_source is not None
        and math.isfinite(sell_tax)
        and bool(holder_pcts)
        and lp_seen
    )

    out["Safety Blockers"] = _join(blockers)
    out["Safety Warnings"] = _join(warnings)
    out["Safety Coverage"] = _join(coverage)
    if blockers:
        out["Safety Gate"] = "FAIL"
    elif critical_complete:
        out["Safety Gate"] = "PASS"
    else:
        out["Safety Gate"] = "UNKNOWN"
        missing = []
        if honeypot is None:
            missing.append("honeypot status")
        if open_source is None:
            missing.append("contract source")
        if not math.isfinite(sell_tax):
            missing.append("sell tax")
        if not holder_pcts:
            missing.append("holder concentration")
        if not lp_seen:
            missing.append("LP lock/burn")
        out["Safety Warnings"] = _join(
            warnings + (["Missing critical safety data: " + ", ".join(missing)] if missing else [])
        )
    return out


def parse_honeypot_check(payload: Dict[str, Any], cfg: Dict | None = None) -> Dict[str, Any]:
    rules = dict(SAFETY_DEFAULTS)
    if cfg:
        rules.update(cfg)
    out = _base_result("Honeypot.is")
    blockers: list[str] = []
    warnings: list[str] = []
    coverage: list[str] = []

    if not isinstance(payload, dict) or not payload:
        out["Safety Coverage"] = "No Honeypot.is result returned"
        return out

    simulation_success = payload.get("simulationSuccess")
    hp = payload.get("honeypotResult") or {}
    is_honeypot = hp.get("isHoneypot")
    if isinstance(is_honeypot, bool):
        coverage.append("sell simulation")
        out["Honeypot"] = "YES" if is_honeypot else "NO"
        if is_honeypot:
            blockers.append(
                "Honeypot simulation failed: "
                + str(hp.get("honeypotReason") or "token may not be sellable")
            )
    if simulation_success is True:
        out["Sell Test"] = "PASS"
        coverage.append("buy/sell simulation")
    elif simulation_success is False:
        out["Sell Test"] = "FAIL"
        blockers.append("Buy/sell simulation was not successful")

    sim = payload.get("simulationResult") or {}
    buy_tax = _safe(sim.get("buyTax"))
    sell_tax = _safe(sim.get("sellTax"))
    out["Buy Tax %"] = buy_tax
    out["Sell Tax %"] = sell_tax
    if math.isfinite(buy_tax):
        coverage.append("buy tax")
        if buy_tax > rules["max_buy_tax_pct"]:
            blockers.append(f"Buy tax {buy_tax:.1f}% > {rules['max_buy_tax_pct']:.0f}%")
    if math.isfinite(sell_tax):
        coverage.append("sell tax")
        if sell_tax > rules["max_sell_tax_pct"]:
            blockers.append(f"Sell tax {sell_tax:.1f}% > {rules['max_sell_tax_pct']:.0f}%")
        elif sell_tax > 5:
            warnings.append(f"Sell tax {sell_tax:.1f}%")

    summary = payload.get("summary") or {}
    risk_level = _safe(summary.get("riskLevel"))
    out["Risk Level"] = risk_level
    out["Risk Label"] = str(summary.get("risk") or "")
    if math.isfinite(risk_level):
        coverage.append("risk flags")
        if risk_level > rules["max_risk_level"]:
            blockers.append(f"Honeypot.is risk level {risk_level:.0f} is high")
        elif risk_level >= 20:
            warnings.append(f"Honeypot.is risk level {risk_level:.0f}")
    for flag in summary.get("flags") or []:
        severity = str((flag or {}).get("severity") or "").lower()
        desc = str((flag or {}).get("description") or (flag or {}).get("flag") or "risk flag")
        if severity in {"critical", "high"}:
            blockers.append("Honeypot.is: " + desc[:180])
        elif severity:
            warnings.append("Honeypot.is: " + desc[:180])

    code = payload.get("contractCode") or {}
    root_open = code.get("rootOpenSource")
    if isinstance(root_open, bool):
        out["Contract Open Source"] = "YES" if root_open else "NO"
        coverage.append("contract source")
        if not root_open:
            blockers.append("Root contract is not open source")

    holder_analysis = payload.get("holderAnalysis") or {}
    failed = _safe(holder_analysis.get("failed"), 0.0)
    siphoned = _safe(holder_analysis.get("siphoned"), 0.0)
    if failed > 0 or siphoned > 0:
        blockers.append("Holder analysis found failed/siphoned sells")

    critical_complete = (
        isinstance(is_honeypot, bool)
        and simulation_success is True
        and math.isfinite(sell_tax)
    )
    out["Safety Blockers"] = _join(blockers)
    out["Safety Warnings"] = _join(warnings)
    out["Safety Coverage"] = _join(coverage)
    if blockers:
        out["Safety Gate"] = "FAIL"
    elif critical_complete:
        out["Safety Gate"] = "PASS"
    else:
        out["Safety Gate"] = "UNKNOWN"
    return out


def merge_safety_results(*results: Dict[str, Any]) -> Dict[str, Any]:
    usable = [r for r in results if isinstance(r, dict) and r]
    if not usable:
        return _base_result("Unavailable")
    out = _base_result(" + ".join(
        dict.fromkeys(str(r.get("Safety Provider") or "") for r in usable if r.get("Safety Provider"))
    ))
    blockers = []
    warnings = []
    coverage = []
    gates = []
    for result in usable:
        gates.append(str(result.get("Safety Gate") or "UNKNOWN").upper())
        blockers.extend(str(result.get("Safety Blockers") or "").split("; "))
        warnings.extend(str(result.get("Safety Warnings") or "").split("; "))
        coverage.extend(str(result.get("Safety Coverage") or "").split("; "))
        for key in (
            "Sell Test", "Honeypot", "Risk Level", "Risk Label", "Buy Tax %",
            "Sell Tax %", "LP Locked %", "Largest Holder %", "Top 10 Holders %",
            "Creator %", "Owner %", "Mint Authority", "Freeze Authority",
            "Contract Open Source", "Insider Networks",
        ):
            current = out.get(key)
            incoming = result.get(key)
            if incoming in (None, "", "UNKNOWN"):
                continue
            if isinstance(incoming, float) and math.isnan(incoming):
                continue
            if current in (None, "", "UNKNOWN") or (
                isinstance(current, float) and math.isnan(current)
            ):
                out[key] = incoming

    out["Safety Blockers"] = _join(blockers)
    out["Safety Warnings"] = _join(warnings)
    out["Safety Coverage"] = _join(coverage)
    if "FAIL" in gates:
        out["Safety Gate"] = "FAIL"
    elif gates and all(gate == "PASS" for gate in gates):
        out["Safety Gate"] = "PASS"
    else:
        out["Safety Gate"] = "UNKNOWN"
    return out


def fetch_token_safety(
    chain_id: str,
    token_address: str,
    pair_address: str = "",
    timeout: int = 12,
) -> Dict[str, Any]:
    chain = str(chain_id or "").lower()
    address = str(token_address or "").strip()
    if not address:
        result = _base_result("Unavailable")
        result["Safety Coverage"] = "Token address missing"
        return result

    if chain == "solana":
        try:
            response = requests.get(
                f"{RUGCHECK_API}/tokens/{address}/report",
                headers={"Accept": "application/json", "User-Agent": "CL-Signal/1.0"},
                timeout=timeout,
            )
            response.raise_for_status()
            return parse_rugcheck_report(response.json() or {})
        except Exception as exc:
            result = _base_result("RugCheck")
            result["Safety Coverage"] = f"RugCheck unavailable: {type(exc).__name__}"
            return result

    gp_chain = GOPLUS_CHAIN_IDS.get(chain)
    gp_result = None
    if gp_chain:
        try:
            response = requests.get(
                f"{GOPLUS_API}/token_security/{gp_chain}",
                params={"contract_addresses": address},
                headers={"Accept": "application/json", "User-Agent": "CL-Signal/1.0"},
                timeout=timeout,
            )
            response.raise_for_status()
            gp_result = parse_goplus_security(response.json() or {}, address)
        except Exception as exc:
            gp_result = _base_result("GoPlus")
            gp_result["Safety Coverage"] = f"GoPlus unavailable: {type(exc).__name__}"

    hp_chain = HONEYPOT_CHAIN_IDS.get(chain)
    hp_result = None
    if hp_chain:
        try:
            params = {"address": address, "chainID": hp_chain}
            if pair_address:
                params["pair"] = pair_address
            response = requests.get(
                f"{HONEYPOT_API}/v2/IsHoneypot",
                params=params,
                headers={"Accept": "application/json", "User-Agent": "CL-Signal/1.0"},
                timeout=timeout,
            )
            response.raise_for_status()
            hp_result = parse_honeypot_check(response.json() or {})
        except Exception as exc:
            hp_result = _base_result("Honeypot.is")
            hp_result["Safety Coverage"] = f"Honeypot.is unavailable: {type(exc).__name__}"

    if gp_result and hp_result:
        return merge_safety_results(gp_result, hp_result)
    if gp_result:
        return gp_result
    if hp_result:
        return hp_result

    result = _base_result("Unsupported")
    result["Safety Coverage"] = "No supported live safety provider for this chain"
    return result



def finalize_entry_signal(
    technical_entry: str,
    safety_result: Dict[str, Any] | None,
    *,
    position_liquidity_pct: float = float("nan"),
    liquidity_change_pct: float = float("nan"),
) -> Dict[str, Any]:
    """Apply local liquidity protections and convert safety state into final entry state."""
    technical = str(technical_entry or "").upper()
    if technical != "QUALIFIED":
        out = dict(safety_result or {})
        out["Entry Signal"] = "AVOID" if technical == "AVOID" else "WAIT"
        return out

    out = dict(safety_result or _base_result("Unavailable"))
    blockers = [x for x in str(out.get("Safety Blockers") or "").split("; ") if x]
    warnings = [x for x in str(out.get("Safety Warnings") or "").split("; ") if x]

    if math.isfinite(position_liquidity_pct) and position_liquidity_pct > 0.5:
        blockers.append(
            f"Planned position is {position_liquidity_pct:.2f}% of pool liquidity (>0.50%)"
        )
        out["Safety Gate"] = "FAIL"

    if math.isfinite(liquidity_change_pct):
        if liquidity_change_pct <= -25:
            blockers.append(
                f"Liquidity fell {abs(liquidity_change_pct):.1f}% since previous scan"
            )
            out["Safety Gate"] = "FAIL"
        elif liquidity_change_pct <= -10:
            warnings.append(
                f"Liquidity fell {abs(liquidity_change_pct):.1f}% since previous scan"
            )

    out["Safety Blockers"] = _join(blockers)
    out["Safety Warnings"] = _join(warnings)
    gate = str(out.get("Safety Gate") or "UNKNOWN").upper()
    if gate == "PASS":
        out["Entry Signal"] = "ENTRY QUALIFIED"
    elif gate == "FAIL":
        out["Entry Signal"] = "SAFETY BLOCK"
    else:
        out["Entry Signal"] = "SAFETY UNKNOWN"
    return out
