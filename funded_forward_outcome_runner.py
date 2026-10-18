"""Collect real PRIOR frozen Kraken Funded snapshots and evaluate LATER public candles.

Never synthesise signals by looking back. GitHub artifacts must already exist and
must pass SHA/universe validation. This creates NEW audit artifacts, never writes
to a broker, user account, originals, main, or a Git branch. Repeated snapshots
of an asset are NOT independent funded positions or compoundable returns.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import re
import zipfile

from funded_forward_health import (
    RESEARCH_CRYPTO_TRIGGER, RESEARCH_STOCK_TRIGGERS, validate_snapshot,
)
from funded_forward_outcomes import evaluate_snapshot, write_outcome_once

NAME = re.compile(r"^funded-(crypto|stocks)-[0-9]+-[0-9]+$")
MAX_ARCHIVE_BYTES = 3_000_000


def load_local_snapshots(root: Path):
    snapshots = []
    for path in sorted(root.glob("funded-*.json")):
        if not re.match(r"^funded-(stocks|crypto)-[0-9]{8}T", path.name):
            continue
        snap = json.loads(path.read_text(encoding="utf-8"))
        validate_snapshot(snap)
        snapshots.append(snap)
    return snapshots


def restore_github_archives(*, repository: str, token: str, min_created: datetime,
                            max_artifacts=250):
    """Retrieve only original default-branch research artifacts, not CI smoke."""
    import requests
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("GitHub repository name is invalid")
    if not token:
        raise RuntimeError("GitHub Actions READ token required for archived source evidence")
    client = requests.Session()
    client.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    endpoint = f"https://api.github.com/repos/{repository}/actions/artifacts"
    archives = []
    exhausted = False
    for page in range(1, 51):
        response = client.get(endpoint, params={"per_page": 100, "page": page}, timeout=30)
        response.raise_for_status()
        items = response.json().get("artifacts", [])
        for item in items:
            created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            if created < min_created:
                exhausted = True
                break
            if (not NAME.fullmatch(str(item.get("name") or ""))
                    or item.get("expired")
                    or (item.get("workflow_run") or {}).get("head_branch") != "main"):
                continue
            if len(archives) >= max_artifacts:
                raise RuntimeError("Frozen archive cap reached; fail closed rather than silently omit older observations")
            archives.append(item)
        if len(items) < 100:
            exhausted = True
        if exhausted:
            break
    if not exhausted:
        raise RuntimeError("GitHub archive pagination unbounded: source evidence could be incomplete")
    if not archives:
        raise RuntimeError("No authentic default-branch frozen Funded archives; zero is NOT a clean no-trade scan")
    snapshots = []
    ids = {}
    for artifact in archives:
        response = client.get(artifact["archive_download_url"], timeout=50)
        response.raise_for_status()
        payload_bytes = response.content
        if len(payload_bytes) > MAX_ARCHIVE_BYTES:
            raise RuntimeError("Frozen artifact unexpectedly large")
        with zipfile.ZipFile(BytesIO(payload_bytes)) as container:
            names = [name for name in container.namelist()
                     if re.fullmatch(r"funded-(crypto|stocks)-[0-9]{8}T[0-9]{6}Z-.*\.json", Path(name).name)]
            if len(names) != 1:
                raise RuntimeError(f"Artifact {artifact['id']}: expected exactly one frozen source snapshot")
            raw = container.read(names[0])
            if len(raw) > MAX_ARCHIVE_BYTES:
                raise RuntimeError("Frozen JSON unexpectedly large")
            snap = json.loads(raw.decode("utf-8"))
        validate_snapshot(snap)
        key = str(snap["run_id"])
        if key in ids:
            if ids[key] != snap["payload_sha256"]:
                raise RuntimeError(f"Conflicting archived frozen snapshots for {key}")
            continue
        ids[key] = snap["payload_sha256"]
        snapshots.append(snap)
    return snapshots


def _qualifying_rows(snap):
    kind = snap["asset_type"]
    return [
        row for row in snap["rows"]
        if (str(row["status"]).upper() == RESEARCH_CRYPTO_TRIGGER
            if kind == "crypto"
            else str(row["status"]).upper() in RESEARCH_STOCK_TRIGGERS)
    ]


def public_stock_daily(asset):
    import pandas as pd
    import yfinance as yf
    raw = yf.download(asset, period="6mo", interval="1d",
                      auto_adjust=False, threads=False, progress=False)
    if raw is None or raw.empty:
        raise ValueError("Yahoo returned no stock candles")
    if isinstance(raw.columns, pd.MultiIndex):
        if asset in raw.columns.get_level_values(0):
            raw = raw[asset]
        elif asset in raw.columns.get_level_values(1):
            raw = raw.xs(asset, axis=1, level=1)
        else:
            raise ValueError("Ticker missing from downloaded stock OHLCV")
    raw.columns = [str(c).title() for c in raw.columns]
    if not {"Open", "High", "Low", "Close"}.issubset(raw.columns):
        raise ValueError("Yahoo stock prices missing OHLC fields")
    return [
        {"timestamp": str(pd.Timestamp(t).date()),
         "open": float(r["Open"]), "high": float(r["High"]),
         "low": float(r["Low"]), "close": float(r["Close"])}
        for t, r in raw.iterrows()
    ]


async def public_kraken_4h(requests_by_asset):
    import ccxt.async_support as ccxt
    exchange = ccxt.kraken({"enableRateLimit": True})
    histories = {}
    try:
        await asyncio.wait_for(exchange.load_markets(), timeout=75)
        for asset, details in requests_by_asset.items():
            pair, first_observed = details
            if pair not in exchange.markets:
                histories[asset] = None
                continue
            since = int((first_observed - timedelta(hours=4)).timestamp() * 1000)
            try:
                raw = await asyncio.wait_for(
                    exchange.fetch_ohlcv(pair, timeframe="4h", since=since, limit=720), timeout=75)
                histories[asset] = [
                    {"timestamp": datetime.fromtimestamp(c[0]/1000, tz=timezone.utc).isoformat(),
                     "open": c[1], "high": c[2], "low": c[3], "close": c[4]}
                    for c in raw
                ]
            except Exception as exc:
                print(f"::warning::Kraken public spot OHLCV unavailable for {asset}: {type(exc).__name__}")
                histories[asset] = None
    finally:
        await exchange.close()
    return histories


def collect_future_public_candles(snapshots):
    """Use actual market providers ONLY for genuine pre-frozen trigger rows."""
    seen_stocks = set()
    coins = {}
    for snap in snapshots:
        for row in _qualifying_rows(snap):
            if snap["asset_type"] == "stocks":
                seen_stocks.add(row["asset"])
            else:
                pair = str((row.get("meta") or {}).get("pair") or "")
                if not pair:
                    continue
                captured = datetime.fromisoformat(snap["captured_at_utc"])
                old = coins.get(row["asset"])
                if old and old[0] != pair:
                    raise RuntimeError(f"Funded Crypto {row['asset']} switched public spot pairs; refuse to mix price histories")
                if not old or captured < old[1]:
                    coins[row["asset"]] = (pair, captured)
    result = {"stocks": {}, "crypto": {}}
    for symbol in sorted(seen_stocks):
        try:
            result["stocks"][symbol] = public_stock_daily(symbol)
        except Exception as exc:
            print(f"::warning::Yahoo public daily OHLCV unavailable for {symbol}: {type(exc).__name__}")
            result["stocks"][symbol] = None
    if coins:
        result["crypto"] = asyncio.run(public_kraken_4h(coins))
    return result


def evaluate_archive_batch(snapshots, *, evaluated_at, prices=None):
    if not snapshots:
        raise RuntimeError("No archived snapshots; forward-testing health is UNKNOWN")
    snapshots = sorted(snapshots, key=lambda s: (s["captured_at_utc"], s["run_id"]))
    kinds = Counter()
    for snap in snapshots:
        validate_snapshot(snap)
        kinds[snap["asset_type"]] += 1
    candle_inputs = prices if prices is not None else collect_future_public_candles(snapshots)
    results = []
    all_status = Counter()
    triggering = 0
    for snap in snapshots:
        triggering += len(_qualifying_rows(snap))
        report = evaluate_snapshot(
            snap, candle_inputs.get(snap["asset_type"], {}),
            evaluated_at=evaluated_at,
        )
        all_status.update(report["statuses"])
        # Every evaluation references original SHA; no changed snapshot, no
        # hypothetical 'broker fills' added to its source evidence.
        results.append(report)
    return {
        "schema": "cl-signal-funded-forward-daily-journal-v1",
        "evaluated_at_utc": evaluated_at.isoformat(),
        "source_snapshot_count": len(snapshots),
        "stock_snapshots": kinds["stocks"],
        "crypto_snapshots": kinds["crypto"],
        "source_assets_accounted": sum(kinds.get(k, 0) * n for k, n in (("stocks", 30), ("crypto", 33))),
        "original_technical_trigger_observations": triggering,
        "shadow_status_counts": dict(sorted(all_status.items())),
        "paper_case_count_is_not_distinct_trades": True,
        "aggregate_account_pnl": None,
        "strategy_success_unproven": True,
        "broker_trades_executed": 0,
        "funded_challenge_pass_claimed": False,
        "research_only": True,
        "observations": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-github", action="store_true")
    parser.add_argument("--snapshot-dir", default="funded-forward-artifacts")
    parser.add_argument("--out-dir", default="funded-outcome-artifacts")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--max-artifacts", type=int, default=250)
    args = parser.parse_args()
    if not 1 <= args.window_days <= 90:
        raise ValueError("Window days must be 1–90")
    clock = datetime.now(timezone.utc)
    if args.from_github:
        snapshots = restore_github_archives(
            repository=os.environ.get("GITHUB_REPOSITORY", ""),
            token=os.environ.get("GITHUB_TOKEN", ""),
            min_created=clock - timedelta(days=args.window_days),
            max_artifacts=args.max_artifacts,
        )
    else:
        snapshots = load_local_snapshots(Path(args.snapshot_dir))
    journal = evaluate_archive_batch(snapshots, evaluated_at=clock)
    suffix = f"{os.environ.get('GITHUB_RUN_ID','manual')}-{os.environ.get('GITHUB_RUN_ATTEMPT','1')}"
    name = f"funded-forward-outcomes-{clock:%Y%m%dT%H%M%SZ}-{suffix}.json"
    path = write_outcome_once(Path(args.out_dir) / name, journal)
    print(json.dumps({
        "file": str(path), "frozen_snapshots": journal["source_snapshot_count"],
        "stocks": journal["stock_snapshots"], "crypto": journal["crypto_snapshots"],
        "original_research_triggers": journal["original_technical_trigger_observations"],
        "statuses": journal["shadow_status_counts"],
        "funded_trades": 0, "research_only": True,
    }, sort_keys=True))
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write(
                "### Kraken Funded — prospective public-candle shadow outcomes\n"
                f"- Authentic frozen snapshots examined: **{journal['source_snapshot_count']}** "
                f"(stocks {journal['stock_snapshots']}; crypto {journal['crypto_snapshots']})\n"
                f"- Original research entry triggers (not verified broker orders): "
                f"**{journal['original_technical_trigger_observations']}**\n"
                f"- Shadow states: **{journal['shadow_status_counts']}**\n"
                "- Recorded broker trades: **0**; funded account equity/P&L: **NOT CONNECTED**\n"
                "- Outcomes are correlated, public-market proxies with hypothetical "
                "0.5% cost, not trade confirmations or an achieved challenge.\n"
            )


if __name__ == "__main__":
    main()
