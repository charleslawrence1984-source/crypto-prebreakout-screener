"""Investment publication failures must not produce live-looking BUY labels."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from investment_readiness_guard import (
    investment_publication_readiness,
    withhold_unverified_investment_buys,
)

NOW = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
STAMP = "2026-10-17T14:30:00+00:00"


def save(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def valid_fixture(root, *, technical_status="complete", timestamp=STAMP):
    save(root, "prepared_scans/manifest.json", {
        "updated_at": timestamp, "exchanges": {"nasdaq": {"status": "complete"}}
    })
    save(root, "prepared_investment_technicals/manifest.json", {
        "updated_at": timestamp, "exchanges": {"nasdaq": {"status": technical_status}}
    })
    save(root, "prepared_priority_investments/summary.json", {
        "refreshed_at": timestamp, "total": 1, "rows": [{"Ticker": "TEST"}]
    })


class InvestmentReadinessGuard(unittest.TestCase):
    def test_fresh_complete_inputs_only_allow_actionable_label(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            valid_fixture(root)
            readiness = investment_publication_readiness(root, now=NOW)
            self.assertTrue(readiness["ready"], readiness)
            frame = pd.DataFrame([{"Ticker": "TEST", "Action": "BUY CANDIDATE"}])
            self.assertEqual(withhold_unverified_investment_buys(frame, readiness).iloc[0]["Action"], "BUY CANDIDATE")

    def test_partial_technical_data_blocks_buys_but_preserves_research(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            valid_fixture(root, technical_status="partial")
            readiness = investment_publication_readiness(root, now=NOW)
            self.assertFalse(readiness["ready"])
            self.assertIn("investment_technicals", " ".join(readiness["issues"]))
            original = pd.DataFrame([
                {"Ticker": "BUY", "Action": "BUY CANDIDATE", "Decision reason": "quality passed"},
                {"Ticker": "WAIT", "Action": "WAIT", "Decision reason": "margin too thin"},
            ])
            guarded = withhold_unverified_investment_buys(original, readiness)
            self.assertEqual(guarded["Action"].tolist(), ["REVALUE", "WAIT"])
            self.assertIn("PUBLICATION NOT VERIFIED", guarded.iloc[0]["Decision reason"])
            self.assertEqual(original.iloc[0]["Action"], "BUY CANDIDATE")

    def test_missing_priority_and_stale_fundamental_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            valid_fixture(root, timestamp="2026-01-01T00:00:00+00:00")
            (root / "prepared_priority_investments/summary.json").unlink()
            readiness = investment_publication_readiness(root, now=NOW)
            self.assertFalse(readiness["ready"])
            self.assertIn("investment_priority", " ".join(readiness["issues"]))
            self.assertIn("STALE", " ".join(readiness["issues"]))
            frame = pd.DataFrame([{"Action": "BUY CANDIDATE"}])
            self.assertEqual(withhold_unverified_investment_buys(frame, readiness).iloc[0]["Action"], "REVALUE")

    def test_unverified_readiness_fails_closed(self):
        frame = pd.DataFrame([{"Action": "BUY CANDIDATE"}])
        self.assertEqual(
            withhold_unverified_investment_buys(frame, {}).iloc[0]["Action"], "REVALUE"
        )


if __name__ == "__main__":
    unittest.main()
