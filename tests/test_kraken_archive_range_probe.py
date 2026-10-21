"""Offline HTTP range ZIP member catalog fail-closed tests."""
import io
import json
import re
import unittest
from zipfile import ZipFile
from unittest.mock import patch

from scripts.kraken_archive_range_probe import (
    ArchiveUnavailable, HTTPRangeReader, catalog_quarter, ALLOWED,
)


class FakeResponse(io.BytesIO):
    def __init__(self, content, *, code, headers, url):
        super().__init__(content)
        self.status=code
        self.headers=headers
        self.url=url
    def geturl(self):
        return self.url
    def getcode(self):
        return self.status


def make_provider(payload, *, ignore_ranges=False, break_content_range=False, redirect=None):
    def opener(request, timeout):
        url=request.full_url
        dest=redirect or url
        if request.get_method()=="HEAD":
            return FakeResponse(b"",code=200,
                                headers={"Content-Length":str(len(payload))},url=dest)
        rng=request.get_header("Range")
        assert rng and rng.startswith("bytes=")
        start,end=map(int,re.fullmatch(r"bytes=(\d+)-(\d+)",rng).groups())
        if ignore_ranges:
            return FakeResponse(payload,code=200,
                                headers={"Content-Length":str(len(payload))},url=dest)
        b=payload[start:end+1]
        boundary=(f"bytes {start}-{end}/{len(payload)}"
                  if not break_content_range else "bytes 0-1/2")
        return FakeResponse(b,code=206,headers={
            "Content-Length":str(len(b)),
            "Content-Range":boundary,
        },url=dest)
    return opener


def fixture_zip():
    f=io.BytesIO()
    with ZipFile(f,"w") as z:
        z.writestr("MANIFEST.json",json.dumps({"provider":"Kraken", "quarter":"Q3"}))
        z.writestr("BTCUSD_240.csv","1790000000,100,110,90,105,1,4\n")
        z.writestr("NEARUSD_1440.csv","1790000000,2,3,1,2,1000,99\n")
    return f.getvalue()


class KrakenArchiveHTTPRangeTests(unittest.TestCase):
    def test_exact_range_catalog_never_downloads_entire_zip(self):
        payload=fixture_zip()
        result=catalog_quarter("2026Q3",max_transfer=500_000,
                               opener=make_provider(payload))
        self.assertEqual(result["members_total"],3)
        self.assertEqual(result["csv_members"],2)
        self.assertEqual(result["csv_intervals"],{"240":1,"1440":1})
        self.assertTrue(result["manifest_member_sha256"])
        self.assertFalse(result["full_zip_sha256_verified"])
        self.assertTrue(result["zip_central_directory_verified"])
        self.assertLess(result["transfer_bytes"],500_000)

    def test_reject_server_ignoring_range_before_full_archive_download(self):
        payload=fixture_zip()
        with self.assertRaisesRegex(ArchiveUnavailable,"Range ignored"):
            catalog_quarter("2026Q3",opener=make_provider(payload,ignore_ranges=True))

    def test_reject_unreliable_range_metadata(self):
        payload=fixture_zip()
        with self.assertRaisesRegex(ArchiveUnavailable,"Content-Range"):
            catalog_quarter("2026Q3",opener=make_provider(payload,break_content_range=True))

    def test_reject_unexpected_redirect_host(self):
        payload=fixture_zip()
        with self.assertRaisesRegex(ArchiveUnavailable,"Unexpected ZIP CDN redirect"):
            catalog_quarter("2026Q3",opener=make_provider(
                payload,redirect="https://example.com/untrusted.zip"))

    def test_disallow_official_looking_but_unapproved_arbitrary_host(self):
        with self.assertRaisesRegex(ValueError,"only documented"):
            HTTPRangeReader("https://other.example/kraken.zip",
                            opener=make_provider(fixture_zip()))

    def test_cumulative_range_cap(self):
        payload=fixture_zip()
        reader=HTTPRangeReader(ALLOWED["2026Q3"],max_transfer=1024,
                               opener=make_provider(payload))
        read_len=min(len(payload),512)
        repetitions=1024//read_len
        for _ in range(repetitions):
            reader.seek(0)
            reader.read(read_len)
        reader.seek(0)
        with self.assertRaisesRegex(ArchiveUnavailable,"budget"):
            reader.read(read_len)


if __name__ == "__main__":
    unittest.main()
