"""Pure, local-only, READ-ONLY migration preflight for existing stock holdings.

The live app stores a JSON list of {'Ticker','Position type','Shares',
'Average cost'} in browser localStorage key cl_signal_stock_portfolio_v2
(with cookie v1 fallback). This module NEVER accesses those browser stores,
persists changes, calls APIs or performs any holdings migration.

It preserves raw row identity (including duplicate tickers across separate
strategies), and blocks any unknown strategy rather than silently reallocating
a real user's positions.
"""
from __future__ import annotations

import copy
import hashlib
import json


def readonly_partition_preflight(payload: str | list | None) -> dict:
    result={
        "status":"BLOCKED_UNVERIFIED_LEGACY_INPUT",
        "preview_only":True,"migration_performed":False,
        "source_browser_store_untouched":True,
        "original_sha256":None,
        "raw_rows":0,"trade_rows":[],"investment_rows":[],
        "review_flags":[],
    }
    try:
        records=json.loads(payload) if isinstance(payload,str) else payload
        if not isinstance(records,list) or any(not isinstance(x,dict) for x in records):
            result["review_flags"].append("INVALID_LEGACY_JSON_LIST")
            return result
        canonical=json.dumps(records,sort_keys=True,ensure_ascii=True,
                             separators=(",",":")).encode()
        result["original_sha256"]=hashlib.sha256(canonical).hexdigest()
        result["raw_rows"]=len(records)
        for i,record in enumerate(records):
            name=str(record.get("Ticker") or "").strip().upper()
            raw_type=record.get("Position type")
            if raw_type is None or not str(raw_type).strip():
                strat="INVESTMENT"
                result["review_flags"].append(
                    "LEGACY_ROW_MISSING_POSITION_TYPE_DEFAULTS_INVESTMENT_REVIEW:"+str(i))
            else:
                strat=str(raw_type).strip().upper()
            if strat not in ("TRADE","INVESTMENT"):
                result["review_flags"].append("UNKNOWN_POSITION_TYPE_REVIEW:"+str(i))
                continue
            if not name and any(record.get(k) not in (None,"",0,0.0,"0")
                                for k in ("Shares","Average cost")):
                result["review_flags"].append("POSITION_WITHOUT_TICKER_REVIEW:"+str(i))
            # Exact unmodified raw record plus original ordinal, no collapsing
            # duplicate ticker rows or converting numeric cost/quantity types.
            row={"original_row_number":i,"original":copy.deepcopy(record)}
            if strat=="TRADE":
                result["trade_rows"].append(row)
            else:
                result["investment_rows"].append(row)
        if result["review_flags"]:
            result["status"]="BLOCKED_MANUAL_REVIEW_REQUIRED"
        else:
            result["status"]="PREVIEW_PARTITION_COMPLETE_NOT_APPLIED"
        if len(result["trade_rows"])+len(result["investment_rows"])!=len(records):
            result["status"]="BLOCKED_UNASSIGNED_LEGACY_POSITION"
        return result
    except (ValueError,TypeError,OverflowError):
        result["review_flags"].append("LEGACY_PAYLOAD_PARSE_FAILED")
        return result
