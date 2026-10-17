"""Immutable prospective Kraken Funded research snapshot contract.

This does NOT confirm Kraken Funded broker prices, account equity, fills, or
earnings events. The research scan must not produce executable READY orders.
Assets are exactly the lists currently defined in kraken_funded.py.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

SOURCE = Path(__file__).resolve().parent / "kraken_funded.py"
UNIVERSE_NAMES = {"stocks": "KRAKEN_FUNDED_STOCKS", "crypto": "KRAKEN_FUNDED_CRYPTO"}
RESEARCH_ONLY = "RESEARCH ONLY — BROKER/ACCOUNT/EXECUTION NOT VERIFIED"


def funded_universe(path: Path = SOURCE) -> dict[str, tuple[str, ...]]:
    """Read original approved universe without running Streamlit side effects."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = {}
    for item in tree.body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and target.id in UNIVERSE_NAMES.values():
                    found[target.id] = ast.literal_eval(item.value)
    if set(found) != set(UNIVERSE_NAMES.values()):
        raise ValueError("Kraken Funded stock/crypto universe definitions missing")
    result = {}
    for kind, name in UNIVERSE_NAMES.items():
        assets = found[name]
        if not isinstance(assets, list) or not assets or any(not isinstance(s,str) or not s.strip() for s in assets):
            raise ValueError(f"Invalid Kraken funded {kind} universe")
        if len(assets) != len(set(assets)):
            raise ValueError(f"Duplicate Kraken funded {kind} assets")
        result[kind] = tuple(assets)
    return result


def _serializable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Naive timestamps are not valid")
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_serializable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _serializable(v) for k,v in value.items()}
    # pandas/numpy numeric and timestamps, when present in the upstream engine
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    if hasattr(value, "item"):
        return _serializable(value.item())
    raise TypeError(f"Unsupported snapshot type: {type(value).__name__}")


def create_snapshot(
    kind: str,
    records: Sequence[Mapping[str, Any]],
    *,
    observed_at: datetime,
    run_id: str,
    universe: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Require full named-universe coverage; retain failed rows as failures.

    Do NOT backfill older decisions or promote a paper result to a broker trade.
    """
    if kind not in UNIVERSE_NAMES:
        raise ValueError("kind must be stocks or crypto")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("capture clock requires timezone-aware actual UTC time")
    if observed_at.utcoffset().total_seconds() != 0:
        raise ValueError("capture timestamp must use UTC")
    if not run_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in run_id):
        raise ValueError("invalid run ID")
    allowed = tuple((universe or funded_universe())[kind])
    lookup: dict[str, Mapping[str, Any]] = {}
    for record in records:
        symbol = str(record.get("asset") or "").strip()
        if symbol in lookup:
            raise ValueError(f"duplicate prospective {kind} snapshot asset {symbol}")
        if symbol not in allowed:
            raise ValueError(f"unapproved prospective {kind} snapshot asset {symbol}")
        lookup[symbol] = record
    rows = []
    for asset in allowed:
        item = lookup.get(asset)
        if item is None:
            item = {"asset": asset, "status": "DATA UNAVAILABLE", "reason": "Not returned by snapshot scanner"}
        state = str(item.get("status") or "").strip()
        if not state:
            raise ValueError(f"{asset}: original engine outcome missing")
        row = {
            "asset": asset, "asset_type": kind,
            "status": state,
            "reason": str(item.get("reason") or "")[:500],
            "source": str(item.get("source") or "NOT PROVIDED")[:160],
            "bar_timestamp": str(item.get("bar_timestamp") or ""),
            "score": _serializable(item.get("score")),
            "entry": _serializable(item.get("entry")),
            "stop": _serializable(item.get("stop")),
            "target": _serializable(item.get("target")),
            "rr": _serializable(item.get("rr")),
            "meta": _serializable(dict(item.get("meta") or {})),
            "trade_authorization": RESEARCH_ONLY,
        }
        if any(k in item for k in ("broker_order","account_verified","realized_pnl")):
            raise ValueError(f"{asset}: forbidden broker/account information in research-only archive")
        rows.append(row)
    content = {
        "schema": "cl-signal-funded-forward-snapshot-v1",
        "run_id": run_id, "captured_at_utc": observed_at.isoformat(),
        "asset_type": kind, "expected_universe": len(allowed),
        "observed_rows": len(lookup),
        "missing_rows": len(allowed) - len(lookup),
        "research_only": True, "broker_account_connected": False,
        "rows": rows,
    }
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",",":"), allow_nan=False)
    content["payload_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
    return content


def write_snapshot_once(path: Path, payload: dict[str, Any]) -> Path:
    """Exclusive create prevents accidental post-event overwriting/backfill."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as output:
        output.write(serialized)
    return path
