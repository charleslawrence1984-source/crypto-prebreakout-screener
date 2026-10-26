"""Research-only first-observation timeliness / no-execution safety regressions."""
import unittest
from datetime import datetime

from funded_forward_capture import create_snapshot
from funded_forward_health import validate_snapshot
from funded_capture_candle_identity import audit
from funded_forward_signal_safety import assess

UNIVERSE = {"crypto": ("BTC", "CRV"), "stocks": ("NVDA",)}


def snap(time, run, crv="BUY", bar="2026-10-21T16:00:00Z", pair="CRV/USD"):
    at = datetime.fromisoformat(time.replace("Z", "+00:00"))
    return create_snapshot(
        "crypto",
        [
            {"asset": "BTC", "status": "PASS", "bar_timestamp": bar,
             "meta": {"pair": "BTC/USD"}},
            {"asset": "CRV", "status": crv, "bar_timestamp": bar,
             "meta": {"pair": pair}},
        ],
        observed_at=at, run_id=run, universe=UNIVERSE,
    )


def validated(source):
    return validate_snapshot(source, universe=UNIVERSE)


def evidence(*sources, as_of="2026-10-22T01:00:00Z"):
    return audit(list(sources), as_of=as_of, validator=validated)


class OriginalForwardSafetyTests(unittest.TestCase):
    def test_realistic_prompt_crv_and_late_repeat_are_one_research_episode(self):
        a = snap("2026-10-21T20:22:00Z", "capt1")
        b = snap("2026-10-21T23:36:00Z", "capt2")
        r = assess(evidence(a, b))
        self.assertEqual(r["original_buy_observations"], 2)
        self.assertEqual(r["distinct_original_buy_episodes"], 1)
        self.assertEqual(r["prompt_observed_unique_research_buy_episodes"], 1)
        self.assertEqual(r["late_duplicate_observations_NOT_extra_trades"], 1)
        self.assertEqual(r["episodes"][0]["first_observation_lag_seconds"], 1320)
        self.assertEqual(r["episodes"][0]["first_observed_run_id"], "capt1")
        self.assertEqual(r["funded_ready_signals"], 0)
        self.assertFalse(r["episodes"][0]["funded_order_permission"])

    def test_first_observation_later_than_research_window_is_not_safe(self):
        a = snap("2026-10-21T23:36:00Z", "late")
        r = assess(evidence(a))
        self.assertEqual(r["prompt_observed_unique_research_buy_episodes"], 0)
        self.assertEqual(r["late_first_observation_episodes"], 1)
        self.assertEqual(r["episodes"][0]["timeliness_classification"],
                         "LATE_FIRST_OBSERVATION_RESEARCH_ONLY")

    def test_contradictory_state_never_qualifies_prompt_research(self):
        a = snap("2026-10-21T20:22:00Z", "buy")
        b = snap("2026-10-21T20:30:00Z", "watch", crv="WATCH")
        r = assess(evidence(a, b))
        self.assertEqual(r["quarantined_source_episodes"], 1)
        self.assertEqual(r["prompt_observed_unique_research_buy_episodes"], 0)
        self.assertFalse(r["episodes"][0]["research_timeliness_qualified"])

    def test_switched_pair_quarantines_even_if_first_snapshot_looks_valid(self):
        a = snap("2026-10-21T20:22:00Z", "spot-a")
        b = snap("2026-10-21T20:30:00Z", "spot-b", pair="CRV/USDT")
        r = assess(evidence(a, b))
        self.assertEqual(r["quarantined_source_episodes"], 1)
        self.assertEqual(r["funded_ready_signals"], 0)

    def test_cannot_claim_missing_slots_were_no_signals(self):
        a = snap("2026-10-21T20:22:00Z", "first")
        b = snap("2026-10-22T04:22:00Z", "third", crv="WATCH",
                 bar="2026-10-22T00:00:00Z")
        r = assess(evidence(a, b, as_of="2026-10-22T05:00:00Z"))
        self.assertEqual(r["unobserved_4h_bins_UNKNOWN"], 1)
        self.assertTrue(r["missing_capture_bins_not_backfilled"])

    def test_rejects_forged_buy_count(self):
        a = snap("2026-10-21T20:22:00Z", "a")
        doc = evidence(a)
        doc["original_technical_BUY_observations"] = 99
        with self.assertRaisesRegex(ValueError, "counts inconsistent"):
            assess(doc)

    def test_rejects_future_observation_inserted_into_identity_report(self):
        a = snap("2026-10-21T20:22:00Z", "a")
        doc = evidence(a)
        doc["episodes"][0]["observations"][0]["captured_at_utc"] = "2026-10-23T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "future original"):
            assess(doc)

    def test_rejects_report_without_disconnected_broker_guarantee(self):
        a = snap("2026-10-21T20:22:00Z", "a")
        doc = evidence(a)
        doc["broker_funded_quote_or_account_verified"] = True
        with self.assertRaisesRegex(ValueError, "fail-closed"):
            assess(doc)

    def test_rejects_unbounded_timing_or_fake_report(self):
        with self.assertRaisesRegex(ValueError, "authenticated"):
            assess({})
        with self.assertRaisesRegex(ValueError, "1..240"):
            assess(evidence(snap("2026-10-21T20:22:00Z", "a")),
                   max_research_lag_minutes=241)

    def test_invalid_source_snapshot_fails_sha_validation_before_latency(self):
        a = snap("2026-10-21T20:22:00Z", "a")
        a["rows"][1]["status"] = "WATCH"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            assess(evidence(a))


if __name__ == "__main__":
    unittest.main()
