"""Read-only official Kraken 33-coin historical archive coverage inventory.

Only ZIP central directories (not OHLCV members) are inspected. Current
approved Kraken Funded universe is known, but historical Kraken spot listing
and Kraken Funded instrument eligibility are NOT authenticated by an entry
existing in any historical archive.
"""
from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from funded_forward_capture import funded_universe
from scripts.kraken_archive_range_probe import ALLOWED,HTTPRangeReader,MultipartKrakenZIP
from scripts.kraken_archive_selective_extract import _members_for_coin


def inventory(output_dir):
    coins=funded_universe()["crypto"]
    full_reader=MultipartKrakenZIP(max_transfer=30_000_000)
    q3_reader=HTTPRangeReader(ALLOWED["2026Q3"],max_transfer=15_000_000)
    reports=[]
    with ZipFile(full_reader) as full,ZipFile(q3_reader) as q3:
        a=full.infolist()
        b=q3.infolist()
        for coin in coins:
            report={"Coin":coin}
            for name,members in (("FULL",a),("Q3_2026",b)):
                try:
                    selection=_members_for_coin(members,coin)
                    has4="240" in selection
                    hasd="1440" in selection
                    report[name+" 4h"]=has4
                    report[name+" daily"]=hasd
                    report[name+" paired"]=bool(has4 and hasd)
                    report[name+" 4h member"]=(
                        selection["240"].filename if has4 else "")
                    report[name+" daily member"]=(
                        selection["1440"].filename if hasd else "")
                except ValueError as exc:
                    report[name+" paired"]=False
                    report[name+" error"]=str(exc)[:140]
            report["Both source generations paired"]=bool(
                report.get("FULL paired") and report.get("Q3_2026 paired"))
            reports.append(report)
    path=Path(output_dir)
    path.mkdir(exist_ok=True,parents=True)
    frame=pd.DataFrame(reports)
    frame.to_csv(path/"all_33_current_universe_official_archive_index.csv",
                 index=False)
    summary={
        "current_funded_coin_count":len(coins),
        "full_archive_4h_and_daily_pairs":int(frame["FULL paired"].sum()),
        "q3_archive_4h_and_daily_pairs":int(frame["Q3_2026 paired"].sum()),
        "full_plus_q3_exact_paired_count":int(frame["Both source generations paired"].sum()),
        "official_full_archive_size_bytes":full_reader.size,
        "official_q3_size_bytes":q3_reader.size,
        "full_catalog_bytes_transferred":full_reader.transferred,
        "q3_catalog_bytes_transferred":q3_reader.transferred,
        "only_index_member_names_verified":True,
        "actual_full_OHLCV_member_sha_verified":False,
        "actual_q3_OHLCV_member_sha_verified":False,
        "historic_funded_market_eligibility_verified":False,
        "provider_notice":"Kraken omits intervals with zero trades; archive presence alone is not sufficient backtest coverage",
    }
    (path/"catalog_summary.json").write_text(
        json.dumps(summary,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(summary,sort_keys=True))
    return summary


if __name__=="__main__":
    inventory("funded-kraken-33-coin-coverage")
