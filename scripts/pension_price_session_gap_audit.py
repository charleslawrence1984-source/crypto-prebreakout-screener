"""Research-only Pension Investment daily-bar session identity audit.

An operator supplies the independently verified last completed venue session.
No exchange calendar is inferred, no market data is fetched, and no BUY rule,
provider, portfolio, or stored data is modified. This does NOT approve a BUY.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone

from investment_live_readiness import daily_price_session_issue


def _date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def audit_session(*, now_utc: datetime, candle_date: str, expected_session: str) -> dict:
    """Compare the existing calendar-age guard to an externally supplied session.

    `expected_session` must come from an independent, venue-specific calendar or
    completed-bar receipt; this audit deliberately does not manufacture one.
    """
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")
    now_utc = now_utc.astimezone(timezone.utc)
    expected = _date(expected_session)
    if expected > now_utc.date():
        raise ValueError("expected completed session cannot be in the future")
    existing_issue = daily_price_session_issue(candle_date, now_utc)
    try:
        actual = _date(candle_date)
    except (TypeError, ValueError):
        actual = None
    matches = actual == expected
    return {
        "as_of_utc": now_utc.isoformat(),
        "observed_candle_date": candle_date,
        "expected_last_completed_session": expected_session,
        "existing_calendar_guard_issue": existing_issue,
        "existing_calendar_guard_accepts": not bool(existing_issue),
        "matches_independently_supplied_session": matches,
        "gap_accepted_by_existing_guard": not bool(existing_issue) and not matches,
        "buy_readiness_assessed": False,
        "data_or_rules_modified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now-utc", required=True, help="ISO 8601 aware UTC clock")
    parser.add_argument("--candle-date", required=True, help="Observed YYYY-MM-DD")
    parser.add_argument("--expected-session", required=True, help="Independently verified YYYY-MM-DD")
    args = parser.parse_args()
    clock = datetime.fromisoformat(args.now_utc.replace("Z", "+00:00"))
    print(json.dumps(audit_session(now_utc=clock, candle_date=args.candle_date,
                                   expected_session=args.expected_session), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
