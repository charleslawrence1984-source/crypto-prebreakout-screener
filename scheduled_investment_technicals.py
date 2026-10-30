from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

logging.getLogger("streamlit").setLevel(logging.CRITICAL)
import stock_app
from investment_live_readiness import TECH_MAX_AGE_HOURS, _recent

BUILD = "investment-technicals-2026.09.19.1-6d6fd387c691f8ed"
# This build identifier is now intentionally stable. UI/UX-only edits in stock_app.py
# must not invalidate prepared technical rows. Bump BUILD only when the investment
# technical calculation or its interpretation changes.


def recovery_analysis(symbol):
    """Retry the same listing; never substitute another exchange or currency."""
    ticker = stock_app.yf.Ticker(symbol)
    counts = []
    for period in ("1y", "2y"):
        history = ticker.history(period=period, interval="1d", auto_adjust=False)
        if history is None or history.empty:
            counts.append(f"{period}: empty history")
            continue
        # A wider request can recover provider range failures. Keep the original
        # one-year calculation window and reject stale last prices.
        dates = pd.to_datetime(history.index, utc=True)
        now = pd.Timestamp.now(tz="UTC")
        history = history.loc[dates >= now - pd.Timedelta(days=366)].copy()
        if history.empty or pd.to_datetime(history.index, utc=True).max() < now - pd.Timedelta(days=14):
            raise ValueError("No recent price history for this listing")
        result = stock_app.technical_from_df(history)
        if result:
            return {**result, "History request period": period, "History source ticker": symbol}
        counts.append(f"{period}: {len(history)} rows, insufficient usable history/indicators")
    raise ValueError("; ".join(counts))


def eligible_rows(frame):
    required = {"Ticker", "Action", "Hard gates", "Price"}
    if not required.issubset(frame.columns):
        if frame.empty:
            return frame.iloc[0:0]
        raise ValueError("Fundamental file lacks required eligibility columns")
    return frame[
        frame["Hard gates"].eq("PASS")
        & frame["Action"].isin(["BUY CANDIDATE", "WAIT"])
        & pd.to_numeric(frame["Price"], errors="coerce").gt(0)
        & frame["Ticker"].notna()
    ].drop_duplicates("Ticker", keep="last")


def complete_exchange(meta, frame):
    """Permit a source-complete batch of VERIFIED rows, not missing provider data.

    The fundamental collector counts temporarily provider-deferred symbols in
    eligible_symbols, but excludes them from coverage_target_symbols. Its
    status can be 'complete' when every *non-deferred* symbol is stored. Allow
    technicals for those present rows only, after verifying the full count
    contract. The published technical manifest must still be PARTIAL while
    provider-deferred fundamentals remain missing.
    """
    if meta.get("status") != "complete" or meta.get("remaining_symbols") != 0:
        return False
    total = meta.get("eligible_symbols")
    covered = meta.get("coverage_symbols")
    deferred = meta.get("provider_deferred_symbols", 0)
    target = meta.get("coverage_target_symbols", total)
    if any(type(n) is not int or n < 0 for n in (total, covered, deferred, target)):
        return False
    if target + deferred != total or covered != target or "Ticker" not in frame:
        return False
    return (
        len(frame) == covered
        and frame["Ticker"].notna().all()
        and frame["Ticker"].nunique() == covered
    )


def fingerprint(row):
    return hashlib.sha256(row.to_json().encode()).hexdigest()


def insufficient_price_history(exc):
    """Back off only explicit short-history responses, never provider outages.

    technical_from_df requires at least 80 usable daily candles. The same
    listing has already been queried for both 1y and 2y by recovery_analysis.
    Generic errors and indicator failures with >=80 bars remain retryable.
    """
    if not isinstance(exc, ValueError):
        return False
    counts = re.findall(
        r"(?:1y|2y): (\d+) rows, insufficient usable history/indicators",
        str(exc),
    )
    return len(counts) == 2 and all(int(count) < 80 for count in counts)


def reconcile(frame, fingerprints, build):
    required = {"Ticker", "Fundamental fingerprint", "Technical build"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    return frame[
        frame["Ticker"].map(fingerprints).eq(frame["Fundamental fingerprint"])
        & frame["Ticker"].isin(fingerprints)
        & frame["Technical build"].eq(build)
    ].drop_duplicates("Ticker", keep="last")


def prepare(source_dir, output_dir, limit, analyse, build):
    source = json.loads((source_dir / "manifest.json").read_text())
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"exchanges": {}}
    failures = 0
    kinds = set(source["exchanges"]) | set(manifest["exchanges"])
    for kind in sorted(kinds):
        meta = source["exchanges"].get(kind, {})
        target = output_dir / f"{kind}.csv.gz"
        src = source_dir / f"{kind}.csv.gz"
        try:
            frame = pd.read_csv(src) if src.exists() else pd.DataFrame()
            eligible = eligible_rows(frame)
            fingerprints = {str(row["Ticker"]): fingerprint(row) for _, row in eligible.iterrows()}
            ready = complete_exchange(meta, frame)
            timestamp = meta.get("completed_at") or source.get("updated_at")
            cached = pd.read_csv(target) if target.exists() else pd.DataFrame()
            cached = reconcile(cached, fingerprints, build)
            # A matching SHA256 means this exact source row is unchanged. It is
            # safe to reconcile ONLY the source-version stamp to a completed,
            # verified fundamental publication; this does NOT update the time
            # the technical calculation was actually run.
            if ready and not cached.empty and timestamp:
                cached["Fundamental source updated at"] = timestamp
            # Persist invalidation even when zero companies remain eligible.
            if cached.empty:
                target.unlink(missing_ok=True)
            else:
                cached.to_csv(target, index=False, compression="gzip")
            covered = set(cached["Ticker"]) if not cached.empty else set()
            previous_meta = manifest["exchanges"].get(kind, {})
            attempts = previous_meta.get("dispatch_counts", {})
            missing = sorted(set(fingerprints) - covered, key=lambda s: (attempts.get(s, 0), s))
            now = datetime.now(timezone.utc)
            # Keep short-history listings outside the request budget until a
            # bounded retry, but immediately retry when source or build changes.
            history_backoff = {}
            for symbol, detail in (previous_meta.get("insufficient_history_backoff") or {}).items():
                if (symbol not in missing
                        or detail.get("fundamental_fingerprint") != fingerprints.get(symbol)
                        or detail.get("technical_build") != build):
                    continue
                try:
                    retry_at = datetime.fromisoformat(str(detail.get("retry_after", "")))
                    if retry_at.tzinfo is not None and retry_at > now:
                        history_backoff[symbol] = detail
                except (ValueError, TypeError):
                    pass
            # Source-matching rows can still be technically stale. Retry them
            # first on the next bounded hourly batch instead of indefinitely
            # keeping an old candle/RSI under a fresh manifest timestamp.
            stale = []
            if ready and not cached.empty:
                stale = sorted(
                    str(row["Ticker"]) for _, row in cached.iterrows()
                    if not _recent(row.get("Technical calculated at"), now, hours=TECH_MAX_AGE_HOURS)
                )
            retryable_missing = [symbol for symbol in missing if symbol not in history_backoff]
            selected = (stale + retryable_missing)[:limit] if ready else []
            rows = []
            # Preserve an explicit explanation for any deferred missing symbol.
            errors = [
                e for e in previous_meta.get("errors", [])
                if e.get("symbol") in missing
                and (limit == 0 or e.get("symbol") in history_backoff)
            ]
            for symbol in selected:
                attempts[symbol] = attempts.get(symbol, 0) + 1
                try:
                    technical = analyse(symbol)
                    if not technical:
                        raise ValueError("No usable technical price history")
                    rows.append({
                        **technical, "Ticker": symbol,
                        "Technical build": build,
                        "Fundamental fingerprint": fingerprints[symbol],
                        "Fundamental source updated at": timestamp,
                        "Technical calculated at": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception as exc:
                    errors.append({"symbol": symbol, "error": str(exc)[:200]})
                    if symbol not in covered and insufficient_price_history(exc):
                        history_backoff[symbol] = {
                            "reason": "insufficient_price_history",
                            "retry_after": (now + timedelta(days=7)).isoformat(),
                            "fundamental_fingerprint": fingerprints[symbol],
                            "technical_build": build,
                        }
                    if "ratelimit" in type(exc).__name__.lower() or "too many requests" in str(exc).lower():
                        # Stop this batch when the provider explicitly throttles.
                        break
            combined = pd.concat([cached, pd.DataFrame(rows)], ignore_index=True)
            if not combined.empty:
                # A successfully refreshed stale symbol supersedes its previous
                # record; a failed refresh keeps old evidence but the BUY gate
                # still refuses it until its calculation timestamp is fresh.
                combined = combined.drop_duplicates("Ticker", keep="last")
                combined.to_csv(target, index=False, compression="gzip")
            coverage = len(combined)
            remaining = len(fingerprints) - coverage
            fresh_coverage = sum(
                _recent(row.get("Technical calculated at"), datetime.now(timezone.utc),
                        hours=TECH_MAX_AGE_HOURS)
                for _, row in combined.iterrows()
            ) if not combined.empty else 0
            stale_coverage = coverage - fresh_coverage
            manifest["exchanges"][kind] = {
                # Verified source rows may be technically complete while other
                # fundamentals are provider-deferred. Never publish a globally
                # complete technical feed until those source gaps are resolved.
                "status": ("complete" if remaining == 0 and stale_coverage == 0 and
                           meta.get("provider_deferred_symbols", 0) == 0
                           else "partial") if ready else "deferred_fundamentals",
                "source_provider_deferred_symbols": meta.get("provider_deferred_symbols", 0),
                "source_total_eligible_symbols": meta.get("eligible_symbols"),
                "source_coverage_target_symbols": meta.get("coverage_target_symbols"),
                "eligible_symbols": len(fingerprints),
                "coverage_symbols": coverage,
                "fresh_coverage_symbols": fresh_coverage,
                "stale_coverage_symbols": stale_coverage,
                "remaining_symbols": remaining,
                "requested_this_run": previous_meta.get("requested_this_run", 0) if limit == 0 else len(rows) + len(errors),
                "returned_this_run": previous_meta.get("returned_this_run", 0) if limit == 0 else len(rows),
                "errors": errors,
                "source_fundamentals_updated_at": timestamp,
                "technical_build": build,
                "dispatch_counts": {s: attempts.get(s, 0) for s in missing if s not in {r["Ticker"] for r in rows}},
                "insufficient_history_backoff": history_backoff,
                "insufficient_history_deferred_symbols": len(history_backoff),
            }
            print(f"{kind}: {manifest['exchanges'][kind]['status']} {coverage}/{len(fingerprints)} (fresh {fresh_coverage}, stale {stale_coverage})", flush=True)
        except Exception as exc:
            # Unreadable fundamentals cannot support a usable technical cache.
            target.unlink(missing_ok=True)
            manifest["exchanges"][kind] = {"status": "failed", "error": str(exc)[:500]}
            failures += 1
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["technical_build"] = build
        manifest["fundamental_coverage_mode"] = "completed-exchanges-only"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return int(failures > 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fundamentals-dir", default="prepared_scans")
    parser.add_argument("--output-dir", default="prepared_investment_technicals")
    parser.add_argument("--max-symbols-per-exchange", type=int, default=50)
    parser.add_argument("--reconcile-only", action="store_true")
    args = parser.parse_args()
    if args.max_symbols_per_exchange < 1:
        parser.error("batch size must be positive")
    # UI changes in stock_app.py are deliberately excluded from cache identity.
    # BUILD is bumped manually whenever the investment technical calculation changes.
    return prepare(Path(args.fundamentals_dir), Path(args.output_dir),
                   0 if args.reconcile_only else args.max_symbols_per_exchange,
                   recovery_analysis, BUILD)


if __name__ == "__main__":
    raise SystemExit(main())
