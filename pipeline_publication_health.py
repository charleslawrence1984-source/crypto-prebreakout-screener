"""Read-only CL Signal data publication audit — not a buy/trade engine.

Inspects saved provider manifests from the *published* GitHub data branches.
GitHub Actions success by itself is NOT evidence that fresh data was delivered.
This audit distinguishes missing/future-dated/stale/partial sources and can
fail CI without altering portfolios or rulebooks.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class PublishedFeed:
    key: str
    path: str
    max_age_minutes: int
    requires_exchange_coverage: bool = False


FEEDS = (
    PublishedFeed("crypto", "prepared_crypto/manifest.json", 20),
    PublishedFeed("meme", "prepared_meme/manifest.json", 30),
    PublishedFeed("trade_fundamentals", "prepared_trade_fundamentals/manifest.json", 96 * 60, True),
    PublishedFeed("trade_technicals", "prepared_trade_technicals/manifest.json", 96 * 60, True),
    PublishedFeed("investment_fundamentals", "prepared_scans/manifest.json", 7 * 24 * 60, True),
    PublishedFeed("investment_technicals", "prepared_investment_technicals/manifest.json", 96 * 60, True),
    PublishedFeed("investment_priority", "prepared_priority_investments/summary.json", 96 * 60),
)


def timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def inspect_feed(
    definition: PublishedFeed,
    payload: Mapping[str, object] | None,
    *,
    now: datetime,
) -> dict:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("audit clock must have an explicit timezone")
    result = {"feed": definition.key, "source": definition.path, "state": "UNVERIFIED",
              "age_minutes": None, "issues": [], "severity": "CRITICAL"}
    if not isinstance(payload, dict) or not payload:
        result["state"] = "MISSING"
        result["issues"].append("No readable published manifest; never assume prior data is fresh")
        return result
    raw_time = payload.get("refreshed_at") if definition.key == "investment_priority" else payload.get("updated_at")
    observed = timestamp(raw_time)
    if observed is None:
        result["state"] = "INVALID TIMESTAMP"
        result["issues"].append("Missing, malformed or timezone-naive source timestamp")
        return result
    age = round((now.astimezone(timezone.utc) - observed).total_seconds() / 60, 1)
    result["age_minutes"] = age
    if age < -5:
        result["state"] = "FUTURE DATED"
        result["issues"].append("Published observation timestamp lies in the future")
        return result
    if age > definition.max_age_minutes:
        result["state"] = "STALE"
        result["issues"].append(f"Published age {age}m exceeds {definition.max_age_minutes}m policy")
        return result

    status = str(payload.get("status") or "").upper()
    errors = payload.get("errors") or []
    if isinstance(errors, list) and errors:
        result["issues"].append(f"{len(errors)} recorded provider/pipeline error(s)")
    if status in {"PARTIAL", "FAILED", "DATA ISSUE", "ERROR", "UNAVAILABLE"}:
        result["issues"].append(f"Publisher status is {status}, not fully current")

    # Partial Crypto/Meme publications are not actionable live research; unlike
    # provider-deferred stock-market rows they are critical source failures.
    critical_structure = definition.key in {"crypto", "meme"} and status != "CURRENT"
    exchanges = payload.get("exchanges") or {}
    if definition.requires_exchange_coverage:
        if not isinstance(exchanges, dict) or not exchanges:
            result["issues"].append("Exchange-level coverage absent")
            critical_structure = True
        else:
            # Gettex/OTC are deliberately not in the default Trade/Investment
            # universe. Preserve diagnostic visibility without calling these
            # optional venues unresolved active markets.
            inactive = {"gettex", "otc"}
            active_incomplete = []
            optional_incomplete = []
            for name, info in exchanges.items():
                status_value = str(info.get("status") or "unknown") if isinstance(info, dict) else "unknown"
                if status_value.lower() != "complete":
                    item = f"{name}:{status_value}"
                    (optional_incomplete if name in inactive else active_incomplete).append(item)
            if active_incomplete:
                result["issues"].append("Incomplete active markets: " + ", ".join(active_incomplete[:12]))
            if optional_incomplete:
                result["issues"].append("Excluded non-default markets (informational): " + ", ".join(optional_incomplete[:12]))

    if definition.key == "investment_priority":
        try:
            total = int(payload.get("total"))
        except (ValueError, TypeError):
            total = -1
        if total <= 0 or not isinstance(payload.get("rows"), list) or len(payload["rows"]) != total:
            result["issues"].append("Priority summary count/rows are inconsistent or empty")
            critical_structure = True

    if definition.key == "crypto":
        try:
            scored=int(payload.get("deep_scores_current", -1))
            eligible=int(payload.get("unique_eligible_coins", -1))
        except (TypeError,ValueError):
            scored,eligible=-1,-1
        if eligible <= 0 or scored < 0:
            result["issues"].append("Crypto universe/deep-score coverage unverified")
            critical_structure = True
        if str(payload.get("status") or "").upper() not in {"CURRENT","PARTIAL"}:
            result["issues"].append("Crypto publisher status missing or unknown")

    if definition.key == "meme":
        try:
            tokens=int(payload.get("universe_tokens",-1))
        except (ValueError,TypeError):
            tokens=-1
        if tokens <= 0:
            result["issues"].append("No valid meme token universe published")
            critical_structure = True
        if status not in {"CURRENT","PARTIAL"}:
            result["issues"].append("Meme publisher status missing or unknown")

    if result["issues"]:
        result["state"] = "DEGRADED"
        result["severity"] = "CRITICAL" if critical_structure else "WARNING"
    else:
        result["state"] = "CURRENT"
        result["severity"] = "OK"
    return result


EXPECTED_CRYPTO_VENUES = frozenset({"binance", "okx", "bybit", "kraken", "cryptocom"})


def crypto_venue_audit_issues(root: Path, manifest: Mapping[str, object] | None) -> list[str]:
    """Cross-check the saved Crypto venue evidence independently of its manifest status.

    The previous producer could publish CURRENT while Bybit's runner region
    was blocked. Never rely only on the producer's status flag.
    """
    try:
        audit_data = json.loads((root / "prepared_crypto" / "audit.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return ["Crypto venue audit is unavailable or unreadable"]
    if not isinstance(audit_data, dict):
        return ["Crypto venue audit is not an object"]
    rows = audit_data.get("exchange_counts")
    if not isinstance(rows, list) or not rows:
        return ["Crypto venue audit is missing exchange details"]
    manifest_time = timestamp(manifest.get("updated_at")) if isinstance(manifest, dict) else None
    audit_time = timestamp(audit_data.get("generated_at"))
    if manifest_time is None or audit_time is None or abs((audit_time - manifest_time).total_seconds()) > 600:
        return ["Crypto venue audit timestamp does not match the published manifest"]
    observed = set()
    issues = []
    for record in rows:
        if not isinstance(record, dict):
            issues.append("Malformed Crypto venue audit record")
            continue
        venue = str(record.get("exchange_id") or "").strip().lower()
        if not venue:
            issues.append("Unnamed Crypto venue audit record")
            continue
        if venue in observed:
            issues.append(f"Duplicate Crypto venue audit: {venue}")
        observed.add(venue)
        status = str(record.get("status") or "").strip().upper()
        if status != "CURRENT":
            issues.append(f"Crypto venue {venue} is {status or 'UNVERIFIED'}")
    missing = EXPECTED_CRYPTO_VENUES - observed
    if missing:
        issues.append("Crypto venue audit missing expected venues: " + ", ".join(sorted(missing)))
    return issues


def audit(root: Path, *, now: datetime | None = None, definitions=FEEDS) -> dict:
    clock = now or datetime.now(timezone.utc)
    datasets = []
    for definition in definitions:
        path = root / definition.path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError,UnicodeError):
            payload = None
        health = inspect_feed(definition,payload,now=clock)
        if definition.key == "crypto" and health["state"] not in {"MISSING", "INVALID TIMESTAMP"}:
            venue_issues = crypto_venue_audit_issues(root, payload)
            if venue_issues:
                health["issues"].extend(venue_issues)
                health["severity"] = "CRITICAL"
                if health["state"] == "CURRENT":
                    health["state"] = "DEGRADED"
        datasets.append(health)
    worst = "CRITICAL" if any(d["severity"]=="CRITICAL" for d in datasets) else (
        "WARNING" if any(d["severity"]=="WARNING" for d in datasets) else "OK")
    return {"schema":"cl-signal-publication-health-v1",
            "audited_at_utc":clock.astimezone(timezone.utc).isoformat(),
            "overall":worst,"datasets":datasets}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root",default=".")
    parser.add_argument("--output",default="prepared_health/pipeline_publication_audit.json")
    parser.add_argument("--no-fail",action="store_true",help="Collect diagnostic evidence without failing this job")
    args=parser.parse_args()
    result=audit(Path(args.root))
    path=Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2))
    # Stock provider-deferred gaps are warnings; missing, stale, future-dated,
    # partial Crypto/Meme or unaudited exchanges are critical failures.
    return 0 if args.no_fail or result["overall"] != "CRITICAL" else 1


if __name__=="__main__":
    raise SystemExit(main())
