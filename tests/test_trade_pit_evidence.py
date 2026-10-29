"""No-hindsight original Trade source receipt gates: deterministic fixtures."""
import json
import tempfile
import unittest
from pathlib import Path

from trade_pit_evidence import audited_manifest, audit_receipt

def sample():
    return {
        "ticker":"AAPL", "venue_mic":"XNAS", "isin":"US0378331005",
        "decision_close_utc":"2024-05-01T20:00:00Z",
        "technical_last_bar_session":"2024-05-01",
        "technical_source_capture_utc":"2024-05-01T20:15:00Z",
        "membership_source":"NASDAQ_20240501_LIST", "membership_asof_utc":"2024-05-01T19:00:00Z",
        "listing_effective_date":"1980-12-12",
        "financial_source":"ISSUER_FILING_2024_04", "financial_published_utc":"2024-04-26T15:00:00Z",
        "financial_period_end":"2024-03-31",
        "earnings_calendar_source":"ISSUER_EVENTS_ARCHIVE", "earnings_calendar_observed_utc":"2024-05-01T13:00:00Z",
        "official_event_review_source":"OFFICIAL_RELEASES_ARCHIVE",
        "official_event_review_observed_utc":"2024-05-01T17:00:00Z",
        "fx_source":"USDGBP_20240501_ARCHIVE", "fx_session":"2024-05-01",
        "fx_observed_utc":"2024-05-01T19:00:00Z",
        "trailing_liquidity_window_end":"2024-05-01",
        "trailing_liquidity_source":"EXCHANGE_HISTORY_20_SESSIONS",
        "historical_cost_schedule_id":"BROKER_FEES_20240501",
        "source_evidence_sha256":"a"*64,
    }


class PitEvidenceTests(unittest.TestCase):
    def test_absent_manifest_is_explicitly_not_live_ready(self):
        r=audited_manifest(None)
        self.assertEqual(r["input_rows"],0)
        self.assertEqual(r["readiness"],"NOT_VERIFIED")
        self.assertIn("NO_AUTHENTIC",r["blocking_reason"])

    def test_complete_claimed_metadata_never_is_authenticated(self):
        result=audit_receipt(sample())
        self.assertEqual(result["status"],"FORMAT_COMPLETE_SOURCE_AUTHENTICITY_UNVERIFIED")
        self.assertFalse(result["independent_reference_verified"])
        self.assertFalse(result["approved_trade_buy_authorised"])

    def test_postdecision_financial_release_is_lookahead(self):
        r=sample();r["financial_published_utc"]="2024-05-02T00:00:00Z"
        self.assertIn("LOOKAHEAD_FINANCIAL_PUBLISHED_UTC", audit_receipt(r)["issues"])

    def test_future_membership_archive_without_pit_vintage_blocks(self):
        r=sample();r["membership_asof_utc"]="2026-10-29T10:00:00Z"
        self.assertIn("LOOKAHEAD_MEMBERSHIP_ASOF_UTC",audit_receipt(r)["issues"])

    def test_same_period_end_does_not_make_future_filing_safe(self):
        r=sample();r["financial_period_end"]="2024-03-31";r["financial_published_utc"]="2024-05-15T00:00:00Z"
        self.assertIn("LOOKAHEAD_FINANCIAL_PUBLISHED_UTC",audit_receipt(r)["issues"])

    def test_late_earnings_calendar_is_a_hindsight_leak(self):
        r=sample();r["earnings_calendar_observed_utc"]="2024-05-02T08:00:00Z"
        self.assertIn("LOOKAHEAD_EARNINGS_CALENDAR_OBSERVED_UTC",audit_receipt(r)["issues"])

    def test_incomplete_official_event_window_blocks(self):
        r=sample();r["official_event_review_observed_utc"]=""
        self.assertIn("MISSING_OFFICIAL_EVENT_REVIEW_OBSERVED_UTC",audit_receipt(r)["issues"])

    def test_fx_known_only_after_signal_is_not_asof(self):
        r=sample();r["fx_observed_utc"]="2024-05-02T08:00:00Z"
        self.assertIn("LOOKAHEAD_FX_OBSERVED_UTC",audit_receipt(r)["issues"])

    def test_opening_history_after_decision_is_not_allowed(self):
        r=sample();r["technical_last_bar_session"]="2024-05-02"
        self.assertIn("FUTURE_DATE_TECHNICAL_LAST_BAR_SESSION",audit_receipt(r)["issues"])

    def test_price_source_capture_after_asof_is_not_accepted(self):
        r=sample();r["technical_source_capture_utc"]="2024-05-01T21:30:00Z"
        self.assertIn("LOOKAHEAD_TECHNICAL_SOURCE_CAPTURE_UTC",audit_receipt(r)["issues"])

    def test_listing_effective_date_from_future_is_not_allowed(self):
        r=sample();r["listing_effective_date"]="2024-05-10"
        self.assertIn("FUTURE_DATE_LISTING_EFFECTIVE_DATE",audit_receipt(r)["issues"])

    def test_time_without_offset_cannot_qualify(self):
        r=sample();r["financial_published_utc"]="2024-04-26T15:00:00"
        self.assertIn("BAD_TIMESTAMP_FINANCIAL_PUBLISHED_UTC",audit_receipt(r)["issues"])

    def test_prepared_manifest_without_genuine_evidence_never_approves_buy(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/"claims.json"
            p.write_text(json.dumps({"records":[sample()]}))
            report=audited_manifest(p)
            self.assertEqual(report["input_rows"],1)
            self.assertEqual(report["blocked"],0)
            self.assertEqual(report["format_complete_not_authenticated"],1)
            self.assertEqual(report["readiness"],"NOT_VERIFIED")
            self.assertFalse(report["records"][0]["approved_trade_buy_authorised"])

    def test_invalid_manifest_records_are_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/"claims.json"
            p.write_text(json.dumps({"records":[None,{"ticker":"AAPL"}]}))
            report=audited_manifest(p)
            self.assertEqual(report["blocked"],2)


if __name__=="__main__":
    unittest.main()
