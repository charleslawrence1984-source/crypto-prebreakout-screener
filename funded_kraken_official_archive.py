"""Offline official Kraken historical OHLCVT research ingestion.

Never download multi-gigabyte complete archives in PR CI. Use authenticated
SOURCE archive files supplied locally, exact checked ZIP members and
published Kraken SHA-256 independently verified by the operator. Ignore no
gaps, invent no quotes, infer no broker eligibility. Week begins Thursday
00:00 UTC as observed in Kraken's current 1w spot feed; verify parity on
overlap before using derived older weekly candles for strategy scoring.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile
import re

import pandas as pd

from funded_crypto_historical import normalize_bars


COLS=("timestamp","open","high","low","close","volume","trades")
THURSDAY_EPOCH=pd.Timestamp("1970-01-01T00:00:00Z")


def read_ohlcvt_member(archive_path, member_name, *, since=None, until=None):
    """Read one exact *headerless* 7-column Kraken historical OHLCVT CSV.

    Only an explicitly identified member is accepted, not fuzzy pair symbols
    whose naming conventions might otherwise silently route to other assets.
    """
    match = re.search(r"_(240|1440)\\.csv$", member_name, re.I)
    if not match:
        raise ValueError("Only explicit Kraken 240-minute or 1440-minute OHLCVT members")
    timeframe = {"240": "4h", "1440": "1d"}[match.group(1)]
    with ZipFile(archive_path) as z:
        if member_name not in z.namelist():
            raise ValueError("selected member absent in official Kraken archive")
        info=z.getinfo(member_name)
        if info.file_size > 200_000_000:
            raise ValueError("excessively large pair CSV requires partitioned ingestion")
        with z.open(member_name) as handle:
            chunks=[]
            for chunk in pd.read_csv(handle,header=None,names=list(COLS),
                                     chunksize=100000,low_memory=False):
                ts=pd.to_numeric(chunk["timestamp"],errors="raise")
                if not ts.map(lambda x: 0 < x < 2e10).all():
                    raise ValueError("timestamps must be Kraken Unix SECONDS")
                chunk["timestamp"]=pd.to_datetime(ts,unit="s",utc=True)
                if since is not None:
                    chunk=chunk.loc[chunk["timestamp"]>=pd.Timestamp(since)]
                if until is not None:
                    chunk=chunk.loc[chunk["timestamp"]<pd.Timestamp(until)]
                if not chunk.empty:
                    chunks.append(chunk[list(COLS)])
    if not chunks:
        raise ValueError("no pair candles exist in selected historical interval")
    data=pd.concat(chunks,ignore_index=True)
    return normalize_bars(data[list(COLS[:6])], timeframe)


def verify_archive_checksum(archive_path, expected_sha256):
    """Only accept authoritative *user supplied* Kraken full-archive hash."""
    expected=str(expected_sha256).lower().strip()
    if len(expected)!=64 or any(x not in "0123456789abcdef" for x in expected):
        raise ValueError("required 64-char published Kraken SHA-256")
    digest=sha256()
    with Path(archive_path).open("rb") as stream:
        for block in iter(lambda:stream.read(2**20),b""):
            digest.update(block)
    if digest.hexdigest()!=expected:
        raise ValueError("Kraken archive SHA-256 mismatch")
    return True


def merge_historic_segments(*frames, timeframe="4h"):
    """Overlap only when exact source bars agree; no fabricated missing bars."""
    if not frames:
        raise ValueError("at least one complete history segment required")
    result=pd.concat(frames,ignore_index=True)
    result["timestamp"]=pd.to_datetime(result["timestamp"],utc=True)
    duplicates=result[result.duplicated("timestamp",keep=False)]
    if not duplicates.empty:
        for _,part in duplicates.groupby("timestamp"):
            if not part.drop(columns=["timestamp"]).eq(part.iloc[0].drop("timestamp")).all().all():
                raise ValueError("conflicting historical overlap candles")
    result=result.drop_duplicates("timestamp",keep="first").sort_values("timestamp")
    return normalize_bars(result.reset_index(drop=True),timeframe)


def weekly_from_kraken_daily(daily):
    """Build ONLY 7-of-7 fully observed daily candles, Thursday to Thursday.

    Kraken sampled live 1w bar opens are Thursdays at 00:00 UTC.
    Reconstructed weeks need parity checks against overlapping original
    1w bars before being relied on for backtested signals.
    """
    d=normalize_bars(daily,"1d")
    dt=d["timestamp"]
    if not ((dt.dt.hour==0)&(dt.dt.minute==0)&(dt.dt.second==0)).all():
        raise ValueError("daily timestamps must be UTC midnight")
    starts=THURSDAY_EPOCH+pd.to_timedelta(
        (dt-THURSDAY_EPOCH).dt.days//7*7,unit="days",
    )
    d=d.assign(week_open=starts)
    out=[]
    for t,g in d.groupby("week_open",sort=True):
        if len(g)!=7 or not g["timestamp"].diff().iloc[1:].eq(pd.Timedelta(days=1)).all():
            continue
        if g["timestamp"].iloc[0]!=t:
            continue
        out.append({"timestamp":t,"open":float(g["open"].iloc[0]),
                    "high":float(g["high"].max()),
                    "low":float(g["low"].min()),
                    "close":float(g["close"].iloc[-1]),
                    "volume":float(g["volume"].sum())})
    if not out:
        raise ValueError("no 7/7 fully observed historical weekly candles")
    return normalize_bars(pd.DataFrame(out),"1w")


def weekly_overlap_parity(derived,spot_weekly,*,rtol=1e-7):
    """Reject false weekly alignment and unexplained source disagreements."""
    left=normalize_bars(derived,"1w")
    right=normalize_bars(spot_weekly,"1w")
    merged=left.merge(right,on="timestamp",how="inner",suffixes=("_archive","_kraken"))
    if merged.empty:
        raise ValueError("no overlapping completed weekly candles to validate")
    for field in ("open","high","low","close","volume"):
        a=merged[field+"_archive"]
        b=merged[field+"_kraken"]
        if not ((a-b).abs()<=rtol*b.abs().clip(lower=1e-9)).all():
            raise ValueError(f"weekly reconstructed {field} disagrees with Kraken native 1w")
    return len(merged)


def weekly_price_and_volume_parity(derived, spot_weekly, *,
                                   price_rtol=1e-7,
                                   max_volume_relative_difference=0.005,
                                   min_overlap=8):
    """Research-only OHLC-perfect weekly parity with separately audited volume.

    Prices must match native Kraken's weekly candles to 1e-7 relative error.
    Volume discrepancies <=0.5% are *reported* as data-vintage uncertainty,
    not concealed. This tolerance is not a trading rule and is NEVER proof
    that historic bid/ask, fills, or ticker volumes were available.

    The frozen original crypto_rule_engine.score_setup uses dfw timestamps,
    high/low/close and weekly ATR, but does not use dfw.volume for swing
    qualification. If that changes, re-audit before allowing any mismatch.
    """
    if not 0 <= max_volume_relative_difference <= 0.005:
        raise ValueError("weekly volume discrepancy must not exceed 0.5%")
    left=normalize_bars(derived,"1w")
    right=normalize_bars(spot_weekly,"1w")
    joined=left.merge(right,on="timestamp",how="inner",
                      suffixes=("_archive","_kraken")).sort_values("timestamp")
    if len(joined)<min_overlap:
        raise ValueError(f"only {len(joined)}/{min_overlap} overlap weeks")
    for name in ("open","high","low","close"):
        a=joined[name+"_archive"]
        b=joined[name+"_kraken"]
        error=(a-b).abs()/b.abs().clip(lower=1e-9)
        if (error>price_rtol).any():
            raise ValueError(f"weekly price {name} diverges from Kraken native 1w")
    a=joined["volume_archive"]
    b=joined["volume_kraken"]
    rel=(a-b).abs()/b.abs().clip(lower=1e-9)
    max_rel=float(rel.max())
    if max_rel>max_volume_relative_difference:
        raise ValueError(
            "weekly volume divergence exceeds research quarantine threshold: "
            f"{max_rel*100:.4f}% > {max_volume_relative_difference*100:.4f}%"
        )
    discrep=joined.loc[rel>price_rtol,["timestamp"]].copy()
    return {
        "status":"PRICE MATCH / VOLUME RECONCILED WITH WARNINGS",
        "matched_weeks":len(joined),
        "weekly_ohlc_price_parity_strict":True,
        "weekly_volume_exact_weeks":int((rel<=price_rtol).sum()),
        "weekly_volume_discrepancy_weeks":int((rel>price_rtol).sum()),
        "max_weekly_volume_relative_difference_pct":round(max_rel*100,6),
        "volume_difference_limit_pct":round(max_volume_relative_difference*100,3),
        "discrepant_week_opens_utc":[str(t) for t in discrep["timestamp"].tolist()[:10]],
        "assessed_as_data_source_vintage_uncertainty":True,
        "native_1w_eligibility_and_broker_execution_unverified":True,
    }
