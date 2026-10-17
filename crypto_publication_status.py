"""Pure exchange coverage status contract, separate from crypto trading scores."""
from __future__ import annotations

from typing import Mapping, Sequence


def publication_status(
    exchange_audits: Sequence[Mapping[str, object]],
    pipeline_errors: Sequence[str],
) -> tuple[str, list[str]]:
    warnings = [
        f"{item.get('exchange_id', 'unknown')}: {item.get('status', 'unavailable')}"
        for item in exchange_audits
        if str(item.get("status") or "").upper() != "CURRENT"
    ]
    if not exchange_audits:
        warnings.append("No exchange audit records reported")
    status = "CURRENT" if not pipeline_errors and not warnings else "PARTIAL"
    return status, warnings
