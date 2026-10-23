"""Bounded public Kraken OHLCVT ZIP range reader, research-only.

HTTPS byte-ranges permit reading ZIP central-directory and exact small members
without downloading the multi-GB full archive. Partial downloads CANNOT
verify the full archive's published SHA-256; independently preserve selected
member SHA-256, CRC, range metadata, official provider URL and MANIFEST.
No market order, credential, deployed cron or funded trade authority.
"""
from __future__ import annotations

import argparse
from collections import Counter
from bisect import bisect_right
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen
from zipfile import ZipFile, BadZipFile

BASE = "https://assets.kraken.com/marketing/institutions/"
ALLOWED = {
    "2026Q1": BASE + "Kraken_OHLCVT_2026Q1.zip",
    "2026Q2": BASE + "Kraken_OHLCVT_2026Q2.zip",
    "2026Q3": BASE + "Kraken_OHLCVT_2026Q3.zip",
}
FULL_PARTS = tuple(BASE + f"Kraken_OHLCVT_Full.zip.part{i:02d}" for i in range(5))
CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
SIZES = {"240": "4h", "1440": "1d"}
TARGET_BASES = ("BTC", "ETH", "SOL", "NEAR", "HBAR")


class ArchiveUnavailable(RuntimeError):
    pass


class HTTPRangeReader:
    """Seekable bounded read-only HTTP ZIP, fail-closed on ignored Range.

    Readers never allow a server to respond with a full-file HTTP 200. Every
    range response must have an accurate Content-Range and Content-Length,
    and requests consume a fixed cumulative research byte budget.
    """
    def __init__(self, url, *, max_transfer=50_000_000, opener=urlopen):
        if url not in ALLOWED.values() and url not in FULL_PARTS:
            raise ValueError("only documented Kraken official quarter archives")
        self.url = url
        self.opener = opener
        self.limit = max_transfer
        self.transferred = 0
        self.position = 0
        request = Request(url, headers={"User-Agent": "CLSignal-Research-ReadOnly/1.0",
                                        "Accept-Encoding": "identity"}, method="HEAD")
        with opener(request, timeout=25) as result:
            self._origin_check(result)
            code = getattr(result, "status", result.getcode())
            if code != 200:
                raise ArchiveUnavailable(f"Kraken archive HEAD returned HTTP {code}")
            length = result.headers.get("Content-Length")
            if not length or not length.isdecimal() or int(length) < 22:
                raise ArchiveUnavailable("Archive byte size unknown; refuse range-only path")
            self.size = int(length)
        if self.size > 30_000_000_000:
            raise ArchiveUnavailable("Unexpected full archive size >30GB")
        if max_transfer < 1024:
            raise ValueError("range budget below minimum")
    def _origin_check(self, result):
        final = result.geturl()
        if not final.startswith(BASE):
            raise ArchiveUnavailable("Unexpected ZIP CDN redirect; reject external host")
    def seekable(self):
        return True
    def readable(self):
        return True
    def tell(self):
        return self.position
    def seek(self, offset, whence=io.SEEK_SET):
        p = (offset if whence == io.SEEK_SET
             else self.position+offset if whence == io.SEEK_CUR
             else self.size+offset if whence == io.SEEK_END
             else None)
        if p is None or p < 0:
            raise ValueError("bad ZIP offset")
        self.position = p
        return p
    def read(self, count=-1):
        if self.position >= self.size:
            return b""
        if count is None or count < 0:
            count = self.size - self.position
        count = min(count, self.size - self.position)
        if count == 0:
            return b""
        if count > self.limit - self.transferred:
            raise ArchiveUnavailable("Partial ZIP byte budget exceeded")
        first = self.position
        last = first + count - 1
        headers = {"Range": f"bytes={first}-{last}",
                   "Accept-Encoding": "identity",
                   "User-Agent": "CLSignal-Research-ReadOnly/1.0"}
        with self.opener(Request(self.url, headers=headers), timeout=35) as result:
            self._origin_check(result)
            status = getattr(result, "status", result.getcode())
            if status != 206:
                raise ArchiveUnavailable(
                    f"Range ignored/unavailable HTTP {status}; refusing full download")
            span = result.headers.get("Content-Range", "")
            match = CONTENT_RANGE.fullmatch(span)
            if not match or (int(match.group(1)),int(match.group(2)),int(match.group(3))) != (
                    first,last,self.size):
                raise ArchiveUnavailable("Provider Content-Range mismatched request")
            payload = result.read(count+1)
            if len(payload) != count:
                raise ArchiveUnavailable("Range byte count incomplete/mismatched")
        self.transferred += len(payload)
        self.position += len(payload)
        return payload


class MultipartKrakenZIP:
    """Read the logical >10GB Kraken full ZIP over five bounded HTTP ranges.

    Strictly maps ZIP offsets to documented part files. Only tiny exact
    ranges are read and never more than a global budget. Full ZIP integrity
    SHA cannot be attested without all original bytes.
    """
    def __init__(self, *, max_transfer=60_000_000, opener=urlopen,
                 part_urls=FULL_PARTS):
        if tuple(part_urls) != FULL_PARTS:
            raise ValueError("only exact official Kraken full-archive part list")
        self.readers=[HTTPRangeReader(url,max_transfer=max_transfer,
                                       opener=opener) for url in part_urls]
        self.ends=[]
        total=0
        for reader in self.readers:
            total+=reader.size
            self.ends.append(total)
        self.size=total
        self.position=0
        self.limit=max_transfer
        self.transferred=0
    def seekable(self):
        return True
    def readable(self):
        return True
    def tell(self):
        return self.position
    def seek(self, offset, whence=io.SEEK_SET):
        p=(offset if whence==io.SEEK_SET
           else self.position+offset if whence==io.SEEK_CUR
           else self.size+offset if whence==io.SEEK_END
           else None)
        if p is None or p<0:
            raise ValueError("bad multipart ZIP seek")
        self.position=p
        return p
    def read(self,count=-1):
        if self.position>=self.size:
            return b""
        count=(self.size-self.position if count is None or count<0
               else min(count,self.size-self.position))
        if count>self.limit-self.transferred:
            raise ArchiveUnavailable("Multipart source exceeds global byte budget")
        chunks=[]
        remaining=count
        while remaining>0:
            part=bisect_right(self.ends,self.position)
            begin=self.ends[part-1] if part else 0
            limit=self.ends[part]
            take=min(remaining,limit-self.position)
            reader=self.readers[part]
            reader.seek(self.position-begin)
            segment=reader.read(take)
            if len(segment)!=take:
                raise ArchiveUnavailable("Incomplete multipart HTTP range")
            chunks.append(segment)
            self.position+=take
            self.transferred+=take
            remaining-=take
        return b"".join(chunks)


def catalog_quarter(quarter, *, max_transfer=20_000_000, opener=urlopen):
    if quarter not in ALLOWED:
        raise ValueError("unsupported Kraken published quarter")
    remote = HTTPRangeReader(ALLOWED[quarter],max_transfer=max_transfer,opener=opener)
    with ZipFile(remote) as archive:
        all_members = archive.infolist()
        files = [x for x in all_members if x.filename.lower().endswith(".csv")]
        grouped = Counter(re.search(r"_(\d+)\.csv$",x.filename,re.I).group(1)
                          for x in files if re.search(r"_(\d+)\.csv$",x.filename,re.I))
        # Only named files are listed, never blindly extracted.
        observed = [dict(member=x.filename,compressed_bytes=x.compress_size,
                         uncompressed_bytes=x.file_size,crc32=f"{x.CRC:08x}")
                    for x in files if re.search(
                        r"(BTC|XBT|ETH|SOL|NEAR|HBAR)",x.filename.upper())
                    and re.search(r"_(240|1440)\.csv$",x.filename,re.I)]
        manifest_name=next((x.filename for x in all_members
                            if x.filename.endswith("MANIFEST.json")),None)
        manifest_sha=None
        if manifest_name:
            info=archive.getinfo(manifest_name)
            if info.file_size <= 1_000_000 and info.compress_size <= 1_000_000:
                manifest_sha=sha256(archive.read(manifest_name)).hexdigest()
        return {
            "quarter":quarter,"url":ALLOWED[quarter],"http_object_size":remote.size,
            "members_total":len(all_members),"csv_members":len(files),
            "csv_intervals":dict(grouped),
            "candidate_coin_4h_1d_members":observed[:160],
            "manifest_name":manifest_name,
            "manifest_member_sha256":manifest_sha,
            "transfer_bytes":remote.transferred,
            "zip_central_directory_verified":True,
            "full_zip_sha256_verified":False,
            "conclusion":"PARTIAL_RANGE_CATALOG_ONLY_NOT_COMPLETE_HISTORY",
        }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--quarter",default="2026Q3",choices=tuple(ALLOWED))
    parser.add_argument("--out",default="funded-archive-catalog")
    args=parser.parse_args()
    out=Path(args.out)
    out.mkdir(exist_ok=True,parents=True)
    payload=catalog_quarter(args.quarter)
    (out / f"kraken_{args.quarter}_range_catalog.json").write_text(
        json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in payload.items()
                      if k not in {"candidate_coin_4h_1d_members"}},sort_keys=True))
    print("CANDIDATE_COIN_MEMBERS",
          json.dumps(payload["candidate_coin_4h_1d_members"][:35]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
