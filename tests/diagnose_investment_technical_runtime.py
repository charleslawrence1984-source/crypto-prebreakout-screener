"""Read-only diagnosis of Investment technical-cache calculation failures.

Original production calculation; two targeted public price lookups only.
No orders, user data, cache writes, or rule changes. Exit nonzero if broken
so this PR cannot be mistaken for a working historical Investment pipeline.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scheduled_investment_technicals import recovery_analysis


def main() -> int:
    failure=0
    for ticker in ("ASML.AS","EXO.AS"):
        print(f"--- {ticker}: read-only public Investment technical diagnosis ---",flush=True)
        try:
            result=recovery_analysis(ticker)
            print(f"SUCCESS {ticker}: RSI {result.get('rsi')}; score {result.get('trade_score')}",flush=True)
        except Exception:
            failure+=1
            traceback.print_exc()
    print(f"Investigation: {failure}/2 original Investment technical calls failed.",flush=True)
    return 1 if failure else 0


if __name__=="__main__":
    raise SystemExit(main())
