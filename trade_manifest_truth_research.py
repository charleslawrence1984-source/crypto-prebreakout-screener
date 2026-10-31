"""Read-only, standard-library audit of CURRENT Trade cache manifest semantics.

Coverage and last write timestamps are not proof of a completed-session
price bar, immutable provider bar hash, original-time issuer fundamentals,
historical membership, event calendar, FX, liquidity, next-open execution or
a paper broker fill. This script NEVER emits BUY or changes cache files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

RELEASE_VERDICT = "BLOCKED_MANIFEST_NOT_COMPLETED_BAR_OR_PIT_EVIDENCE"


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def audit(fundamentals: dict, technicals: dict, *, observed_at: datetime) -> dict:
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must include timezone")
    if not isinstance(fundamentals, dict) or not isinstance(technicals, dict):
        raise ValueError("both manifests must be JSON objects")
    f = fundamentals.get("exchanges")
    t = technicals.get("exchanges")
    if not isinstance(f, dict) or not isinstance(t, dict) or not f or set(f) != set(t):
        raise ValueError("missing or inconsistent venue coverage")
    rows = []
    for venue in sorted(t):
        x, y = t[venue], f[venue]
        if not isinstance(x, dict) or not isinstance(y, dict):
            raise ValueError("malformed venue: " + venue)
        for field in ("coverage_symbols", "eligible_symbols", "requested_this_run", "returned_this_run"):
            if type(x.get(field)) is not int or x[field] < 0:
                raise ValueError("missing/noninteger technical count: " + venue + ":" + field)
        for field in ("coverage_symbols", "fresh_symbols"):
            if type(y.get(field)) is not int or y[field] < 0:
                raise ValueError("missing/noninteger fundamental count: " + venue + ":" + field)
        if x["coverage_symbols"] > x["eligible_symbols"]:
            raise ValueError("coverage exceeds eligible symbols: " + venue)
        updated = _utc(x["updated_at"])
        if updated > observed_at.astimezone(timezone.utc):
            raise ValueError("manifest update timestamp is in the future: " + venue)
        age_hours = round((observed_at - updated).total_seconds() / 3600, 2)
        rows.append({
            "venue": venue,
            "technical_eligible_symbols": x["eligible_symbols"],
            "technical_coverage_symbols": x["coverage_symbols"],
            "technical_requested_this_run": x["requested_this_run"],
            "technical_returned_this_run": x["returned_this_run"],
            "technical_refresh_mode": x.get("refresh_mode", "UNKNOWN"),
            "technical_manifest_updated_at": x["updated_at"],
            "technical_manifest_age_hours": age_hours,
            "technical_no_rows_requested": x["requested_this_run"] == 0,
            "technical_manifest_older_than_7d": age_hours > 168,
            "fundamental_coverage_symbols": y["coverage_symbols"],
            "fundamental_fresh_symbols_reported": y["fresh_symbols"],
            "fundamental_status": y.get("status", "UNKNOWN"),
            # No top-level venue totals can authenticate individual issuer/source bar time.
            "completed_session_price_provenance_from_manifest": "NOT_PROVIDED",
            "per_issuer_publication_asof_from_manifest": "NOT_PROVIDED",
        })
    return {
        "scope": "RESEARCH_ONLY_MANIFEST_COUNTS_NOT_PRODUCTION_READINESS",
        "observed_at_utc": observed_at.astimezone(timezone.utc).isoformat(),
        "venue_count": len(rows),
        "technical_eligible_total": sum(v["technical_eligible_symbols"] for v in rows),
        "technical_coverage_total": sum(v["technical_coverage_symbols"] for v in rows),
        "technical_requested_total": sum(v["technical_requested_this_run"] for v in rows),
        "technical_returned_total": sum(v["technical_returned_this_run"] for v in rows),
        "venues_zero_technical_requests": [v["venue"] for v in rows if v["technical_no_rows_requested"]],
        "venues_technical_manifest_older_than_7d": [v["venue"] for v in rows if v["technical_manifest_older_than_7d"]],
        "fundamental_venues_reporting_zero_fresh": [v["venue"] for v in rows if v["fundamental_fresh_symbols_reported"] == 0],
        "fundamental_manifest_updated_at": fundamentals.get("updated_at"),
        "technical_manifest_updated_at": technicals.get("updated_at"),
        "rows": rows,
        "completed_bar_source_session_verified": False,
        "per_symbol_source_hash_verified": False,
        "issuer_publication_asof_verified": False,
        "historical_membership_verified": False,
        "event_fx_liquidity_verified": False,
        "broker_paper_entries_or_fills_verified": False,
        "trade_rules_modified": False,
        "trading_orders": 0,
        "release_verdict": RELEASE_VERDICT,
        "next_acceptance": (
            "For every would-be Trade BUY, obtain independently dated completed price-bar "
            "session and provider hash, original-time published issuer fundamentals/events, "
            "historical universe, venue FX/liquidity, original-rule next-open gap and "
            "fees/spread, realistic exits/drawdowns and actual eligible broker paper fills. "
            "Aggregate manifest coverage and refresh timestamps alone never suffice."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fundamentals", type=Path, default=Path("prepared_trade_fundamentals/manifest.json"))
    parser.add_argument("--technicals", type=Path, default=Path("prepared_trade_technicals/manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("trade_manifest_truth_research.json"))
    args = parser.parse_args()
    fb, tb = args.fundamentals.read_bytes(), args.technicals.read_bytes()
    now = datetime.now(timezone.utc)
    result = audit(json.loads(fb), json.loads(tb), observed_at=now)
    result["source_sha256"] = {
        "fundamental_manifest": hashlib.sha256(fb).hexdigest(),
        "technical_manifest": hashlib.sha256(tb).hexdigest(),
    }
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in (
        "venue_count", "technical_coverage_total", "technical_eligible_total",
        "technical_requested_total", "technical_returned_total",
        "venues_zero_technical_requests", "venues_technical_manifest_older_than_7d",
        "fundamental_venues_reporting_zero_fresh", "release_verdict"
    )}, sort_keys=True))
    summary = __import__("os").environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).open("a", encoding="utf-8").write(
            "## Trade cache manifest truth (read-only)\n\n"
            + f"**{result['release_verdict']}**\n\n"
            + f"Venues: {result['venue_count']}; coverage totals: "
            + f"{result['technical_coverage_total']}/{result['technical_eligible_total']}; "
            + f"zero-request venues: {len(result['venues_zero_technical_requests'])}; "
            + f"older-than-7d venue manifests: {len(result['venues_technical_manifest_older_than_7d'])}.\n\n"
            + "Coverage and timestamps are NOT dated per-symbol completed bars or PIT records. "
            + "No trading orders, paper fills or signals.\n"
        )


if __name__ == "__main__":
    main()
