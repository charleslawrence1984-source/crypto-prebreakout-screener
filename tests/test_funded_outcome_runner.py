"""GitHub archive and headline regressions for read-only funded forward testing."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import sys
import types
import unittest
from unittest.mock import patch
import zipfile

from funded_forward_capture import create_snapshot, funded_universe
from funded_forward_outcome_runner import evaluate_archive_batch, restore_github_archives

NOW = datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc)


def full_crypto_snapshot(buy=False):
    names = funded_universe()["crypto"]
    records = []
    for i, coin in enumerate(names):
        original = {
            "asset": coin, "status": "PASS",
            "bar_timestamp": "2026-10-18 20:00:00+00:00",
        }
        if i == 0 and buy:
            original.update({
                "status": "BUY", "score": 90, "entry": 100, "stop": 95,
                "target": 115, "rr": 3,
                "meta": {"pair": "BTC/USD", "swing_eligible": True,
                         "entry_timing_actionable": True,
                         "execution_liquidity_pass_public_spot": True,
                         "funded_bid_ask_verified": False},
            })
        records.append(original)
    return create_snapshot("crypto", records, observed_at=NOW,
                           run_id="original-archive-123",
                           universe={"crypto": names})


def frozen_zip(snapshot):
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("funded-crypto-20261019T010000Z-123-1.json",
                    json.dumps(snapshot, allow_nan=False))
    return payload.getvalue()


class FakeResponse:
    def __init__(self, *, data=None, content=b""):
        self.data = data
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, archive, *, source_name="funded-crypto-123-1"):
        self.headers = {}
        self.archive = archive
        self.source_name = source_name
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if url.endswith("/actions/artifacts"):
            return FakeResponse(data={"artifacts": [{
                "id": 11, "name": self.source_name,
                "created_at": "2026-10-19T01:01:00Z", "expired": False,
                "workflow_run": {"head_branch": "main"},
                "archive_download_url": "https://api.github.com/test-artifact.zip"
            }]})
        return FakeResponse(content=self.archive)


class ProspectiveArtifactRunner(unittest.TestCase):
    def test_full_real_universe_rejected_pass_remains_zero_trades(self):
        snap = full_crypto_snapshot()
        journal = evaluate_archive_batch(
            [snap], evaluated_at=NOW, prices={"crypto": {}, "stocks": {}})
        self.assertEqual(journal["source_snapshot_count"], 1)
        self.assertEqual(journal["source_assets_accounted"], 33)
        self.assertEqual(journal["original_technical_trigger_observations"], 0)
        self.assertEqual(journal["broker_trades_executed"], 0)
        paper = journal["provisional_challenge_risk"]
        self.assertEqual(paper["admitted_unique_paper_cases"], 0)
        self.assertEqual(paper["modeled_realized_equity_proxy_usd"], 1000)
        self.assertFalse(paper["kraken_funded_challenge_pass_confirmed"])
        self.assertTrue(journal["strategy_success_unproven"])
        self.assertEqual(journal["shadow_status_counts"]["NO RESEARCH ENTRY"], 33)

    def test_original_buy_and_missing_future_quote_not_called_a_loss(self):
        snap = full_crypto_snapshot(buy=True)
        journal = evaluate_archive_batch([snap], evaluated_at=NOW,
            prices={"crypto": {"BTC": None}, "stocks": {}})
        self.assertEqual(journal["original_technical_trigger_observations"], 1)
        self.assertEqual(journal["shadow_status_counts"]["DATA UNVERIFIED"], 1)
        self.assertIsNone(journal["aggregate_account_pnl"])
        self.assertEqual(journal["broker_trades_executed"], 0)

    def test_no_frozen_sources_is_not_reported_as_no_signals(self):
        with self.assertRaisesRegex(RuntimeError, "No archived snapshots"):
            evaluate_archive_batch([], evaluated_at=NOW,
                                   prices={"crypto": {}, "stocks": {}})

    def test_github_reads_original_archived_crypto_and_sha(self):
        snapshot = full_crypto_snapshot()
        session = FakeSession(frozen_zip(snapshot))
        mock_requests = types.SimpleNamespace(Session=lambda: session)
        with patch.dict(sys.modules, {"requests": mock_requests}):
            found = restore_github_archives(
                repository="sample/repo", token="read-only-token",
                min_created=NOW-timedelta(hours=1))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["payload_sha256"], snapshot["payload_sha256"])

    def test_nonproduction_artifact_is_not_promoted_to_source(self):
        session = FakeSession(frozen_zip(full_crypto_snapshot()),
                              source_name="funded-crypto-public-provider-PR-123")
        mock_requests = types.SimpleNamespace(Session=lambda: session)
        with patch.dict(sys.modules, {"requests": mock_requests}):
            with self.assertRaisesRegex(RuntimeError, "No authentic"):
                restore_github_archives(repository="sample/repo",
                                        token="read-only-token",
                                        min_created=NOW-timedelta(hours=1))
        self.assertEqual(len(session.calls), 1)

    def test_source_digest_tamper_stops_all_evaluation(self):
        snapshot = full_crypto_snapshot()
        snapshot["rows"][0]["status"] = "BUY"
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            evaluate_archive_batch([snapshot], evaluated_at=NOW,
                                   prices={"crypto": {}, "stocks": {}})


if __name__ == "__main__":
    unittest.main()
