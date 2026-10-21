"""Selectively acquire exact official Kraken 240m/1440m OHLCVT members.

Only approved names and a bounded exact HTTP-range reader. Extracts sparse
provider CSV verbatim, checks ZIP CRC via zipfile.read and hashes every saved
member. No full-ZIP SHA, no historical market eligibility, no orders.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from scripts.kraken_archive_range_probe import (
    HTTPRangeReader, ALLOWED, ArchiveUnavailable,
)

ALIASES = {
    "BTC": ("BTCUSD","XBTUSD","XXBTZUSD"),
    "ETH": ("ETHUSD","XETHZUSD"),
}
INTERVALS = {"240": "4h","1440":"1d"}


def approved_pair_candidates(base):
    """No fuzzy symbol guessing or substitution to a different quote asset."""
    code=str(base).upper().strip()
    if not code.isalnum() or len(code)>14:
        raise ValueError("bad approved coin symbol")
    return tuple(x+f"_{interval}.csv"
                 for interval in INTERVALS
                 for x in ALIASES.get(code,(code+"USD",)))


def _members_for_coin(all_members, coin):
    expected=set(x.upper() for x in approved_pair_candidates(coin))
    found={}
    for member in all_members:
        name=PurePosixPath(member.filename).name.upper()
        if name not in expected:
            continue
        interval=name.rsplit("_",1)[-1].split(".")[0]
        if interval in found:
            raise ValueError(f"ambiguous approved {coin} archive {interval} members")
        if member.is_dir() or member.file_size > 20_000_000 or member.compress_size > 12_000_000:
            raise ValueError("compressed/uncompressed CSV too large")
        found[interval]=member
    return found


def extract_quarter(quarter, coins, destination, *, opener=None,
                    byte_budget=60_000_000, min_pairs=1):
    if quarter not in ALLOWED:
        raise ValueError("unsupported official Kraken quarter")
    if not coins or len(coins)!=len(set(coins)):
        raise ValueError("coins must be unique")
    output=Path(destination)
    output.mkdir(parents=True,exist_ok=True)
    kwargs={"max_transfer":byte_budget}
    if opener is not None:
        kwargs["opener"]=opener
    remote=HTTPRangeReader(ALLOWED[quarter],**kwargs)
    coverage=[]
    sources={}
    with ZipFile(remote) as z:
        names=z.infolist()
        for base in coins:
            members=_members_for_coin(names,base)
            if set(members)!={"240","1440"}:
                coverage.append({
                    "coin":base, "status":"MISSING_PAIRED_INTERVALS",
                    "present_intervals":sorted(members),
                    "missing_intervals":sorted(set(INTERVALS)-set(members)),
                })
                continue
            details={}
            for interval in ("240","1440"):
                info=members[interval]
                # ZIP's CRC-32 is validated by ZipFile.read, but is NOT a
                # cryptographic proof of original exchange archive creation.
                raw=z.read(info)
                if len(raw)!=info.file_size:
                    raise ArchiveUnavailable("ZIP member changed / incomplete")
                if not raw or b"\x00" in raw:
                    raise ArchiveUnavailable("Invalid binary content in OHLCVT CSV")
                name=f"{quarter}_{base}_{interval}.csv"
                path=output/name
                path.write_bytes(raw)
                details[interval]={
                    "file":name,"archive_member":info.filename,
                    "bytes":len(raw),"sha256":sha256(raw).hexdigest(),
                    "zip_crc32":f"{info.CRC:08x}",
                    "source_quarter":quarter,
                    "partial_archive_hash_verified":False,
                }
                sources[f"{base}:{interval}"]=details[interval]
            coverage.append({"coin":base,"status":"BOTH_INTERVALS_CAPTURED",
                             "pair_source_member":details["240"]["archive_member"]})
    valid=sum(x["status"]=="BOTH_INTERVALS_CAPTURED" for x in coverage)
    report={
        "quarter":quarter,"official_url":ALLOWED[quarter],
        "total_archive_object_bytes":remote.size,
        "http_range_bytes_transferred":remote.transferred,
        "byte_budget":byte_budget,"requested_coins":list(coins),
        "captured_paired_coins":valid,"coverage":coverage,
        "selected_sources":sources,
        "source_type":"KRAKEN_OFFICIAL_ZIP_PARTIAL_RANGE",
        "source_zip_full_sha256_verified":False,
        "zip_crc_integrity_verified":True,
        "historical_market_eligibility_verified":False,
        "funded_broker_tradeable_verified":False,
        "exchange_rules_unmodified":True,
    }
    (output/f"{quarter}_selection_manifest.json").write_text(
        json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    if valid<min_pairs:
        raise ArchiveUnavailable(f"only {valid}/{min_pairs} required exact paired pairs")
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--quarter",choices=tuple(ALLOWED),default="2026Q3")
    parser.add_argument("--coins",default="BTC,ETH,SOL,NEAR,HBAR")
    parser.add_argument("--output",default="funded-kraken-quarter-sample")
    parser.add_argument("--min-pairs",type=int,default=2)
    args=parser.parse_args()
    bases=[x.strip().upper() for x in args.coins.split(",") if x.strip()]
    report=extract_quarter(args.quarter,bases,args.output,min_pairs=args.min_pairs)
    print(json.dumps({k:v for k,v in report.items()
                      if k not in {"selected_sources"}},sort_keys=True))


if __name__=="__main__":
    main()
