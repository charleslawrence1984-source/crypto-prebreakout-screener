"""UI-only, fail-closed guard for background Crypto BUY and READY labels.

Prepared feeds may be current at publisher level while individual deep scores
are old. Do not alter saved sources, trading strategy, holdings or Quick Analysis.
Show historical rows as RESEARCH ONLY instead.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from crypto_publication_status import publication_status

SWING_SCORE_MAX_AGE_MINUTES = 90
ACCUMULATION_SCORE_MAX_AGE_MINUTES = 24 * 60
CLOCK_SKEW_MINUTES = 5


def _current_score_rows(frame: pd.DataFrame, now: datetime, max_age_minutes: int) -> pd.Series:
    if "deep_scored_at" not in frame.columns:
        return pd.Series(False, index=frame.index, dtype=bool)
    values = pd.to_datetime(frame["deep_scored_at"], utc=True, errors="coerce")
    age_minutes = (pd.Timestamp(now) - values).dt.total_seconds().div(60)
    return (values.notna() & age_minutes.ge(-CLOCK_SKEW_MINUTES)
            & age_minutes.le(max_age_minutes)).fillna(False)


EXPECTED_VENUES = frozenset({"binance", "okx", "bybit", "kraken", "cryptocom"})


def verified_crypto_publisher_health(
    label: str, manifest: dict, venue_audit: dict
) -> str:
    """Independent source-evidence veto for a misleading CURRENT manifest.

    The production pipeline previously claimed CURRENT even when the separate
    exchange audit showed a provider unavailable. Missing, malformed, duplicated
    or mismatched sidecar evidence also fails closed; no trade scoring changes.
    """
    if label != "CURRENT":
        return label

    if not isinstance(manifest, dict) or not isinstance(venue_audit, dict):
        return "UNVERIFIED"

    def parsed_time(raw):
        if not isinstance(raw, str):
            return None
        try:
            result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if result.tzinfo is None or result.utcoffset() is None:
            return None
        return result.astimezone(timezone.utc)

    published = parsed_time(manifest.get("updated_at"))
    audited = parsed_time(venue_audit.get("generated_at"))
    if published is None or audited is None or abs((published - audited).total_seconds()) > 600:
        return "UNVERIFIED"

    rows = venue_audit.get("exchange_counts")
    if not isinstance(rows, list) or not rows:
        return "UNVERIFIED"
    venues = [str(item.get("exchange_id") or "").strip().lower()
              for item in rows if isinstance(item, dict)]
    if len(venues) != len(rows) or len(set(venues)) != len(venues):
        return "UNVERIFIED"
    if not EXPECTED_VENUES.issubset(venues):
        return "UNVERIFIED"
    status, warnings = publication_status(rows, venue_audit.get("errors") or [])
    return "CURRENT" if status == "CURRENT" and not warnings else "PARTIAL"


def mask_prepared_crypto_signals(
    swing: pd.DataFrame, accumulation: pd.DataFrame, *,
    pipeline_health: str,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return independent screen-only copies without unverified BUY/READY labels."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("The publication audit clock must include a timezone")
    clock = clock.astimezone(timezone.utc)
    health = str(pipeline_health or "NOT READY").strip().upper()

    def protect(data: pd.DataFrame, age_minutes: int, *, kind: str) -> pd.DataFrame:
        output = data.copy()
        if output.empty:
            return output

        verified = (_current_score_rows(output, clock, age_minutes)
                    if health == "CURRENT" else pd.Series(False, index=output.index))
        bad = ~verified
        evidence = pd.Series("CURRENT", index=output.index, dtype="object")
        evidence.loc[bad] = (
            "RESEARCH ONLY — publication " + health
            if health != "CURRENT" else "RESEARCH ONLY — per-coin score stale or unverified"
        )
        output["Publication evidence"] = evidence
        if not bad.any():
            return output

        status_col = "Swing status" if kind == "swing" else "Accumulation status"
        if status_col in output.columns:
            output.loc[bad, status_col] = "RESEARCH ONLY"
        if "Opportunity stage" in output.columns:
            output.loc[bad, "Opportunity stage"] = "HISTORICAL — DO NOT TRADE"
        if "Entry timing" in output.columns:
            output.loc[bad, "Entry timing"] = "UNVERIFIED"
        if "Entry mode" in output.columns:
            output.loc[bad, "Entry mode"] = "UNVERIFIED"
        if kind == "swing":
            for column in ("Active entry", "Active entry low", "Active entry high"):
                if column in output.columns:
                    output.loc[bad, column] = float("nan")
        if kind == "accumulation" and "Accumulation verdict" in output.columns:
            output.loc[bad, "Accumulation verdict"] = "RESEARCH ONLY"
        return output

    return (protect(swing, SWING_SCORE_MAX_AGE_MINUTES, kind="swing"),
            protect(accumulation, ACCUMULATION_SCORE_MAX_AGE_MINUTES, kind="accumulation"))
