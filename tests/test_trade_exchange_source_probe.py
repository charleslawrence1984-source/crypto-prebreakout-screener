"""Primary exchange evidence availability: no self-declared price authorisation."""
from datetime import datetime,timezone
import json
import unittest

from trade_exchange_source_probe import EXAMINED,listing_receipt,probe

NOW=datetime(2026,10,29,15,45,tzinfo=timezone.utc)


def sample_directory():
    headers="Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    rows="".join(f"{s}|{s} Common Stock|Q|N|N|100|N|N\n" for s in EXAMINED)
    rows+="".join(f"ZZZ{i}|Example Corp|Q|N|N|100|N|N\n" for i in range(110))
    return (headers+rows+"File Creation Time: 102920261545|||||||\n").encode()


class Response:
    def __init__(self,status,data,content_type="text/plain"):
        self.status_code=status
        self.content=data
        self.headers={"Content-Type":content_type}


class FakeSession:
    def __init__(self,historical=None):
        self.historical=historical
        self.calls=[]
    def get(self,url,params,headers,timeout,allow_redirects):
        self.calls.append((url,params))
        if "nasdaqlisted.txt" in url:
            return Response(200,sample_directory())
        if self.historical is not None:
            return self.historical
        return Response(403,b"not authorised")


class ExchangeProbeTests(unittest.TestCase):
    def test_real_structure_and_timestamp_are_only_current_symbol_evidence(self):
        r=listing_receipt(sample_directory(),NOW)
        self.assertEqual(r["status"],"OFFICIAL_FORMAT_CURRENT_LISTING_EVIDENCE_ONLY")
        self.assertEqual(len(r["listing_symbols_observed"]),5)
        self.assertFalse(r["issuer_prices_authenticated"])
        self.assertFalse(r["original_historical_membership_verified"])
        self.assertFalse(r["approved_trade_buy"])

    def test_fabricated_html_is_not_authenticated_listing(self):
        r=listing_receipt(b"<html>blocked</html>",NOW)
        self.assertEqual(r["status"],"BLOCKED_UNRECOGNISED_DIRECTORY")

    def test_source_missing_or_test_issue_never_accepted(self):
        dirty=sample_directory().replace(b"AAPL|AAPL Common Stock|Q|N|N|",
                                        b"AAPL|AAPL Common Stock|Q|Y|N|")
        r=listing_receipt(dirty,NOW)
        self.assertEqual(r["status"],"INCOMPLETE_OR_NONSTANDARD_LISTING_EVIDENCE")
        self.assertFalse(r["approved_trade_buy"])

    def test_403_price_endpoint_does_not_evade_denial_or_create_trades(self):
        fake=FakeSession()
        report=probe(session=fake,now=NOW)
        self.assertEqual(report["nasdaq_public_history"]["status"],
                         "ACCESS_REQUIRES_PERMISSION_OR_UNAVAILABLE")
        self.assertEqual(len(fake.calls),2)
        self.assertFalse(report["price_feed_independently_authenticated"])
        self.assertEqual(report["approved_paper_entries"],0)
        self.assertEqual(report["broker_orders"],0)

    def test_public_json_rows_are_not_licensed_market_data(self):
        body=json.dumps({"data":{"tradesTable":{"rows":[
            {"date":"10/28/2026","close/last":"$100.00","volume":"42"}
        ]}}}).encode()
        r=probe(session=FakeSession(Response(200,body,"application/json")),now=NOW)
        self.assertEqual(r["nasdaq_public_history"]["status"],
                         "PUBLIC_JSON_ROWS_OBSERVED_NOT_LICENSED")
        self.assertFalse(r["nasdaq_public_history"]["price_feed_authenticated_for_trading"])

    def test_rate_limit_is_not_retried_or_relabelled(self):
        fake=FakeSession(Response(429,b"limit"))
        p=probe(fake,NOW)
        self.assertEqual(p["nasdaq_public_history"]["http_status"],429)
        self.assertEqual(len(fake.calls),2)
        self.assertFalse(p["authorised_price_feed_for_product"])


if __name__=="__main__":
    unittest.main()
