"""Meme provider rate-limit protection and honest partial-publication tests."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
import meme_market_pipeline as meme


def provider_429():
    response = requests.Response()
    response.status_code = 429
    response.url = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"
    return requests.HTTPError("429 Too Many Requests", response=response)


class MemeRateLimitTests(unittest.TestCase):
    def test_provider_429_stops_further_network_requests_and_retains_existing_data(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            old={
                "chainId":"solana","tokenAddress":"safe-old-address",
                "_last_seen_ts":100,"sources":{"Previously observed"},
            }
            meme.save_universe({"solana:safe-old-address":old},root/"universe.json.gz")
            calls=[]
            def limited(network,page):
                calls.append((network,page))
                raise provider_429()
            with patch.object(meme,"NETWORKS",{"solana":"solana","base":"base","ethereum":"eth"}):
                with patch.object(meme,"fetch_dex_discovery",return_value=({},[])):
                    with patch.object(meme,"fetch_gecko_page",side_effect=limited):
                        with patch.object(meme.time,"sleep",return_value=None):
                            result=meme.run(str(root),100)
            self.assertEqual(result,1)
            self.assertEqual(len(calls),1,"Should stop calls after first confirmed 429")
            state=json.loads((root/"manifest.json").read_text())
            self.assertEqual(state["status"],"PARTIAL")
            self.assertTrue(any("429" in error for error in state["errors"]))
            universe=meme.load_universe(root/"universe.json.gz")
            self.assertIn("solana:safe-old-address",universe)

    def test_fully_successful_discovery_still_returns_success(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            payload={"solana:abc":{
                "chainId":"solana","tokenAddress":"abc",
                "sources":{"Dex Profile"},
            }}
            with patch.object(meme,"NETWORKS",{"solana":"solana"}):
                with patch.object(meme,"fetch_dex_discovery",return_value=(payload,[])):
                    with patch.object(meme,"fetch_gecko_page",return_value={"data":[]}):
                        with patch.object(meme.time,"sleep",return_value=None):
                            self.assertEqual(meme.run(str(root),100),0)
            state=json.loads((root/"manifest.json").read_text())
            self.assertEqual(state["status"],"CURRENT")


if __name__=="__main__":
    unittest.main()
