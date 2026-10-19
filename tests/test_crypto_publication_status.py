"""Test exchange-publication truthfulness without market access."""
import unittest

from crypto_publication_status import publication_status


class CryptoPublication(unittest.TestCase):
    def test_every_expected_exchange_current(self):
        audits=[
            {"exchange_id":"kraken","status":"CURRENT"},
            {"exchange_id":"cryptocom","status":"CURRENT"},
        ]
        self.assertEqual(publication_status(audits,[]),("CURRENT",[]))

    def test_blocked_bybit_source_must_be_reported(self):
        state,warnings=publication_status([
            {"exchange_id":"kraken","status":"CURRENT"},
            {"exchange_id":"bybit","status":"VENUE UNAVAILABLE",
             "error":"GitHub cloud region blocked"},
        ],[])
        self.assertEqual(state,"PARTIAL")
        self.assertEqual(warnings,["bybit: VENUE UNAVAILABLE"])

    def test_provider_errors_cannot_become_current(self):
        state,warnings=publication_status([
            {"exchange_id":"kraken","status":"CURRENT"},
        ],["Kraken OHLCV timeout"])
        self.assertEqual(state,"PARTIAL")

    def test_empty_exchange_audit_is_never_current(self):
        state,warnings=publication_status([],[])
        self.assertEqual(state,"PARTIAL")
        self.assertTrue(warnings)


if __name__=="__main__":
    unittest.main()
