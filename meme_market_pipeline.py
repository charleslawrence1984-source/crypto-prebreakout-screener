from __future__ import annotations

import argparse
import gzip
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import requests

from meme_discovery_utils import (
    GECKO_TO_DEX_CHAIN,
    merge_discovery_universes,
    parse_gecko_new_pool_tokens,
    trim_discovery_universe,
)


DEX_API = "https://api.dexscreener.com"
GECKO_API = "https://api.geckoterminal.com/api/v2"
HEADERS = {"User-Agent": "CL-Signal-Meme-Discovery/1.0"}
GECKO_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "application/json;version=20230203",
}

NETWORKS = {
    "solana": "solana",
    "base": "base",
    "ethereum": "eth",
    "bsc": "bsc",
    "robinhood": "robinhood",
    "arbitrum": "arbitrum",
    "polygon": "polygon_pos",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(payload, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_universe(path: Path) -> Dict[str, Dict]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            rows = json.load(handle)
    except Exception:
        return {}

    out: Dict[str, Dict] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        chain = str(row.get("chainId") or "").lower()
        address = str(row.get("tokenAddress") or "")
        if not chain or not address:
            continue
        item = dict(row)
        item["sources"] = set(item.get("sources") or [])
        out[f"{chain}:{address}".lower()] = item
    return out


def save_universe(universe: Dict[str, Dict], path: Path) -> None:
    rows = []
    for item in (universe or {}).values():
        row = dict(item)
        row["sources"] = sorted(set(row.get("sources") or []))
        rows.append(row)
    rows.sort(
        key=lambda row: float(row.get("_last_seen_ts") or 0),
        reverse=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(rows, handle, separators=(",", ":"))


def get_json(url: str, *, headers=None, params=None, timeout=20):
    response = requests.get(
        url,
        headers=headers or HEADERS,
        params=params or {},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def fetch_dex_discovery() -> tuple[Dict[str, Dict], list[str]]:
    sources: Dict[str, Dict] = {}
    errors: list[str] = []
    endpoints = {
        "Profile": "/token-profiles/latest/v1",
        "Boost": "/token-boosts/latest/v1",
        "Top boost": "/token-boosts/top/v1",
        "Community takeover": "/community-takeovers/latest/v1",
    }

    for source, endpoint in endpoints.items():
        try:
            data = get_json(DEX_API + endpoint)
        except Exception as exc:
            errors.append(f"DexScreener {source}: {type(exc).__name__}: {str(exc)[:160]}")
            continue

        if isinstance(data, dict):
            data = [data]
        for item in data or []:
            chain = str(item.get("chainId") or "").lower()
            address = str(item.get("tokenAddress") or "")
            if not chain or not address:
                continue
            key = f"{chain}:{address}".lower()
            row = sources.setdefault(
                key,
                {
                    "chainId": chain,
                    "tokenAddress": address,
                    "sources": set(),
                    "profile_description": "",
                    "profile_links": [],
                    "boost_amount": 0.0,
                    "boost_total": 0.0,
                    "community_takeover": False,
                },
            )
            row["sources"].add(source)
            if item.get("description"):
                row["profile_description"] = item.get("description") or ""
            if item.get("links"):
                row["profile_links"] = item.get("links") or []
            try:
                row["boost_amount"] = max(
                    float(row.get("boost_amount") or 0),
                    float(item.get("amount") or 0),
                )
            except Exception:
                pass
            try:
                row["boost_total"] = max(
                    float(row.get("boost_total") or 0),
                    float(item.get("totalAmount") or 0),
                )
            except Exception:
                pass
            if source == "Community takeover":
                row["community_takeover"] = True

    return sources, errors


def fetch_gecko_page(network: str, page: int) -> dict:
    response = requests.get(
        f"{GECKO_API}/networks/{network}/new_pools",
        params={
            "page": max(1, min(10, int(page))),
            "include": "base_token,quote_token,dex",
        },
        headers=GECKO_HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json() or {}
    return payload if isinstance(payload, dict) else {}


def run(output_dir: str, max_tokens: int) -> int:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    manifest_path = output / "manifest.json"
    universe_path = output / "universe.json.gz"

    previous_manifest = load_json(manifest_path, {})
    previous_universe = load_universe(universe_path)
    cursor = dict(previous_manifest.get("page_cursor") or {})

    seen_ts = now_ts()
    dex_universe, errors = fetch_dex_discovery()
    for meta in dex_universe.values():
        meta["_last_seen_ts"] = seen_ts

    gecko_universe: Dict[str, Dict] = {}
    pages_used: Dict[str, int] = {}
    gecko_calls = 0

    for chain, network in NETWORKS.items():
        page = max(1, min(10, int(cursor.get(chain, 1) or 1)))
        try:
            payload = fetch_gecko_page(network, page)
            discovered = parse_gecko_new_pool_tokens(
                payload,
                default_network=network,
                allowed_chains=set(NETWORKS),
            )
            for meta in discovered.values():
                meta["_last_seen_ts"] = seen_ts
            gecko_universe = merge_discovery_universes(
                gecko_universe,
                discovered,
            )
            pages_used[chain] = page
            cursor[chain] = 1 if page >= 10 else page + 1
            gecko_calls += 1
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            errors.append(f"Gecko {chain} page {page}: HTTP {status or 'error'}")
        except Exception as exc:
            errors.append(
                f"Gecko {chain} page {page}: {type(exc).__name__}: {str(exc)[:160]}"
            )

        # Stay comfortably inside the public rate limit even if requests bunch.
        time.sleep(0.35)

    merged = merge_discovery_universes(
        previous_universe,
        dex_universe,
        gecko_universe,
    )
    merged = trim_discovery_universe(merged, max_tokens=max_tokens)
    save_universe(merged, universe_path)

    manifest = {
        "updated_at": now_iso(),
        "status": "PARTIAL" if errors else "CURRENT",
        "universe_tokens": len(merged),
        "previous_tokens": len(previous_universe),
        "dex_surface_tokens": len(dex_universe),
        "gecko_new_pool_tokens": len(gecko_universe),
        "gecko_calls": gecko_calls,
        "pages_used": pages_used,
        "page_cursor": cursor,
        "max_tokens": int(max_tokens),
        "errors": errors,
    }
    save_json(manifest, manifest_path)

    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if merged else 1


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build the persistent rotating meme discovery universe."
    )
    parser.add_argument("--output-dir", default="prepared_meme")
    parser.add_argument("--max-tokens", type=int, default=2000)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(run(args.output_dir, args.max_tokens))
