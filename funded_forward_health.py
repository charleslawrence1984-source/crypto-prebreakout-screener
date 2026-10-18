"""Read-only health audit for frozen Kraken Funded forward-research snapshots.

Coverage or technical gate PASS is NEVER funded-account trade permission.
Does not edit an observation, initiate an order, or infer realized performance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path

from funded_forward_capture import RESEARCH_ONLY, funded_universe

BAD_DATA = frozenset({"DATA UNAVAILABLE", "SCAN ERROR"})
RESEARCH_STOCK_TRIGGERS = frozenset({
    "FUNDED BREAKOUT PENDING OPEN", "FUNDED BREAKOUT ENTRY CHECK",
    "FUNDED CORE ENTRY CHECK", "ENTRY READY", "AWAITING NEXT OPEN",
})
RESEARCH_CRYPTO_TRIGGER = "BUY"


def validate_snapshot(snapshot: dict, *, universe=None) -> dict:
    """Verify authentic, complete *row accounting*, not perfect price coverage."""
    if not isinstance(snapshot, dict):
        raise ValueError("Snapshot is not an object")
    payload = dict(snapshot)
    digest = payload.pop("payload_sha256", None)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False)
    if not isinstance(digest, str) or sha256(canonical.encode("utf-8")).hexdigest() != digest:
        raise ValueError("Frozen snapshot SHA-256 mismatch: evidence changed")
    if payload.get("schema") != "cl-signal-funded-forward-snapshot-v1":
        raise ValueError("Unsupported frozen research snapshot schema")
    kind = payload.get("asset_type")
    if kind not in {"stocks", "crypto"}:
        raise ValueError("Invalid funded asset class")
    assets = list((universe or funded_universe())[kind])
    rows = payload.get("rows")
    if (not isinstance(rows, list) or len(rows) != len(assets)
            or [x.get("asset") for x in rows if isinstance(x, dict)] != assets):
        raise ValueError("Frozen rows do not match approved funded universe, in order")
    if (payload.get("expected_universe") != len(assets)
            or payload.get("observed_rows") != len(assets)
            or payload.get("missing_rows") != 0):
        raise ValueError("Snapshot has missing, duplicate or unaccounted assets")
    if payload.get("research_only") is not True or payload.get("broker_account_connected") is not False:
        raise ValueError("Research-only or disconnected-broker guarantee broken")
    observed = datetime.fromisoformat(str(payload.get("captured_at_utc") or "").replace("Z", "+00:00"))
    if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
        raise ValueError("Capture timestamp is not UTC-aware")

    states = Counter()
    unavailable = []
    incomplete_history = []
    trigger_candidates = []
    bar_issues = []
    for row in rows:
        name = row["asset"]
        state = str(row.get("status") or "").strip().upper()
        if not state:
            raise ValueError(f"{name}: missing original engine state")
        if row.get("trade_authorization") != RESEARCH_ONLY:
            raise ValueError(f"{name}: funded trade permission illegally present")
        meta = row.get("meta") or {}
        if not isinstance(meta, dict) or any(
            meta.get(key) is True
            for key in ("funded_bid_ask_verified", "broker_market_access_verified")
        ):
            raise ValueError(f"{name}: unverified broker data presented as verified")
        states[state] += 1
        if state in BAD_DATA:
            unavailable.append(name)
            reason = str(row.get("reason") or "").upper()
            if "PRICE HISTORY" in reason and "252 COMPLETED BARS" in reason:
                incomplete_history.append(name)
            continue
        bar = str(row.get("bar_timestamp") or "").strip()
        if not bar:
            bar_issues.append(f"{name}: completed candle timestamp missing")
        else:
            try:
                seen = datetime.fromisoformat(bar.replace("Z", "+00:00"))
                if kind == "stocks" and len(bar) == 10:
                    seen = seen.replace(tzinfo=timezone.utc)
                if seen.tzinfo is None or seen > observed + timedelta(minutes=5):
                    raise ValueError("future or timezone-naive bar")
            except (ValueError, TypeError):
                bar_issues.append(f"{name}: invalid or future candle timestamp")
        if ((kind == "crypto" and state == RESEARCH_CRYPTO_TRIGGER)
                or (kind == "stocks" and state in RESEARCH_STOCK_TRIGGERS)):
            trigger_candidates.append(name)

    valid_count = len(assets) - len(unavailable)
    return {
        "asset_type": kind,
        "run_id": payload.get("run_id"),
        "captured_at_utc": payload.get("captured_at_utc"),
        "archive_sha256_verified": True,
        "approved_universe": len(assets),
        "accounted_rows": len(rows),
        "usable_price_histories": valid_count,
        "unavailable_or_error": len(unavailable),
        "unavailable_assets": unavailable,
        "insufficient_252_bar_history": incomplete_history,
        "original_engine_states": dict(sorted(states.items())),
        "rejected_pass_rows": states.get("PASS", 0) if kind == "crypto" else 0,
        "research_trigger_candidates_NOT_funded_ready": trigger_candidates,
        "bar_evidence_warnings": bar_issues,
        "coverage": "FULL" if not unavailable and not bar_issues else "PARTIAL",
        "funded_trade_ready_confirmed": 0,
        "note": ("PASS on crypto is a rejected technical setup, not an approved trade. "
                 "Even an original engine BUY lacks funded quotes, account risk, and "
                 "broker confirmation; this audit cannot authorize trades."),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", required=True, choices=("stocks", "crypto"))
    parser.add_argument("--path", default="funded-forward-artifacts")
    args = parser.parse_args()
    root = Path(args.path)
    matches = sorted(root.glob(f"funded-{args.asset}-*.json"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one frozen {args.asset} snapshot in {root}, got {len(matches)}")
    snapshot = json.loads(matches[0].read_text(encoding="utf-8"))
    report = validate_snapshot(snapshot)
    diagnostic = root / f"funded-health-{args.asset}.json"
    diagnostic.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        coverage = report["coverage"]
        with open(summary_file, "a", encoding="utf-8") as out:
            out.write(
                f"### Kraken Funded {args.asset} — RESEARCH ONLY\n"
                f"- Data coverage: **{coverage}**, "
                f"{report['usable_price_histories']}/{report['approved_universe']} usable histories\n"
                f"- Unavailable/error assets: **{report['unavailable_or_error']}**: "
                f"{', '.join(report['unavailable_assets']) or 'none'}\n"
                f"- Original Crypto PASS (= rejected), not BUY: **{report['rejected_pass_rows']}**\n"
                f"- Research-only technical trigger candidates: "
                f"**{len(report['research_trigger_candidates_NOT_funded_ready'])}**\n"
                f"- Funded-account trade-ready confirmed: **0** (no connected account/quotes)\n"
                f"- Frozen archive SHA-256: **VERIFIED**\n"
            )
    if report["coverage"] != "FULL":
        print("::warning::Kraken Funded research coverage PARTIAL; missing histories are not no-signals")
    # Partial coverage is a prominent warning, not fabricated full coverage.
    # Invalid archive structure/digest, however, fails the job.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
