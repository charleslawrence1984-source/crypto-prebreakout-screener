"""Read-only source/manifest audit of Stocks TRADE UI contract; never a readiness certificate.

Run: python trade_surface_contract_audit.py --output trade_surface_contract_audit.json
Does not import Streamlit, request market data, edit caches or place orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def audit(root: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    files = ("stock_app.py", "stocks_page.py", "stock_app_v2.py",
             "two_strategy.py", "trade_rules.py")
    source = {name: (root / name).read_text(encoding="utf-8") for name in files}
    digests = {name: hashlib.sha256(value.encode("utf-8")).hexdigest()
               for name, value in source.items()}
    app, overlay, nav, legacy, engine = (source[name] for name in files)

    # Explicitly separate source-observed risks from verified runtime behaviour.
    observations = {
        "legacy_direct_ticker_can_emit_buy_without_approved_state":
            'action = "BUY" if qualifies else "WAIT"' in legacy
            and "technical_state" not in legacy.split("def trade_decision(", 1)[1].split("def _dynamic_sell_premium(", 1)[0],
        "quick_analyse_can_emit_buy_without_approved_state":
            'def quick_trade_decision(' in app
            and '"action": "BUY"' in app.split("def quick_trade_decision(", 1)[1].split("def load_investment_publication_evidence(", 1)[0],
        "overlay_ranks_trade_buy_using_legacy_thresholds":
            'trade_ranked["Trade Action"]' in overlay
            and 'r["Trade"] >= 85' in overlay,
        "approved_trade_feed_labels_ready_to_verify_not_buy":
            '"AWAITING NEXT OPEN": "READY TO VERIFY"' in app
            and '"PRE-CROSS READY": "READY TO VERIFY"' in app,
        "stocks_route_still_uses_mixed_overlay":
            '"stocks_page.py"' in nav and 'url_path="stocks"' in nav,
        "separate_pension_route_present":
            'url_path="pension"' in nav,
        "investment_section_still_in_stocks_overlay":
            'st.subheader("INVESTMENT")' in overlay,
        "browser_position_type_still_mixed":
            'PORTFOLIO_LOCAL_STORAGE_KEY = "cl_signal_stock_portfolio_v2"' in app
            and '"Position type": "INVESTMENT"' in app,
        "approved_technical_engine_has_explicit_ready_state":
            'state = "ENTRY READY" if entry_index is not None else "AWAITING NEXT OPEN"' in engine,
    }
    manifests = {}
    for name in ("prepared_trade_technicals", "prepared_trade_fundamentals"):
        path = root / name / "manifest.json"
        raw = path.read_bytes()
        data = json.loads(raw)
        expected = data.get("rulebook_build")
        venues = {}
        for venue, row in sorted(data.get("exchanges", {}).items()):
            flags = []
            if row.get("rulebook_build") != expected:
                flags.append("OLD_RULEBOOK_BUILD")
            if row.get("status") != "complete" or int(row.get("remaining_symbols") or 0):
                flags.append("PARTIAL_OR_INCOMPLETE")
            stamp = row.get("updated_at") or row.get("completed_at")
            try:
                dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    raise ValueError("timezone missing")
                if (now - dt.astimezone(timezone.utc)).total_seconds() > 4 * 86400:
                    flags.append("MANIFEST_OLDER_THAN_4_DAYS")
            except (TypeError, ValueError):
                flags.append("INVALID_MANIFEST_TIMESTAMP")
            venues[venue] = {
                "status": row.get("status"),
                "coverage_symbols": row.get("coverage_symbols"),
                "eligible_or_universe_symbols": row.get("eligible_symbols", row.get("universe_symbols")),
                "remaining_symbols": row.get("remaining_symbols"),
                "timestamp": stamp,
                "flags": flags,
            }
        manifests[name] = {"sha256": hashlib.sha256(raw).hexdigest(),
                           "rulebook_build": expected, "venues": venues}
    return {
        "as_of_utc": now.isoformat(),
        "scope": "READ_ONLY_SOURCE_AND_MANIFEST_AUDIT",
        "stocks_trade_production_readiness": "NOT_VERIFIED",
        "source_sha256": digests,
        "source_observations": observations,
        "manifest_audit": manifests,
        "not_verified": [
            "rendered cross-surface action agreement for identical as-of data",
            "individual latest completed exchange bar timestamps and vendor lag",
            "PIT issuer fundamentals, earnings/news event availability and historical membership",
            "historical GBP FX, liquidity, spreads, commissions, realistic portfolio exits/drawdowns",
            "adequate independent prospective Trade paper outcomes",
            "owner-approved production release and deployment receipt",
        ],
        "next_gate": "Before release, no Stocks surface may display BUY from legacy score-only paths; require approved full Trade gate, verified completed bar, PIT event/fundamental and liquidity evidence, identical action labels and isolated UI regression tests. Pension route and lossless local holdings migration need separate approval.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("trade_surface_contract_audit.json"))
    args = parser.parse_args()
    report = audit(Path(__file__).resolve().parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"readiness": report["stocks_trade_production_readiness"],
                      "source_observations": report["source_observations"],
                      "flagged_venues": {k: [v for v, row in m["venues"].items() if row["flags"]]
                                         for k, m in report["manifest_audit"].items()}}, indent=2))


if __name__ == "__main__":
    main()
