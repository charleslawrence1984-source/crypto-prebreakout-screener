"""UI health helpers are isolated without importing Streamlit screens."""
import ast
import math
from datetime import datetime,timedelta,timezone
from pathlib import Path
import unittest

import numpy as np
import pandas as pd


ROOT=Path(__file__).resolve().parent.parent


def isolated(path,*names):
    tree=ast.parse((ROOT/path).read_text(encoding="utf-8"))
    functions=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    if len(functions)!=len(names):
        raise RuntimeError("Missing expected Crypto/Meme health helpers")
    node=ast.Module(body=functions,type_ignores=[])
    ns={"pd":pd,"np":np,"math":math}
    exec(compile(ast.fix_missing_locations(node),str(path),"exec"),ns)
    return ns


class UiFreshness(unittest.TestCase):
    def test_future_dated_crypto_data_cannot_be_current(self):
        f=isolated("app.py","crypto_pipeline_age_minutes","crypto_pipeline_health_label")
        future=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
        self.assertEqual(f["crypto_pipeline_health_label"]({
            "updated_at":future,"status":"CURRENT"}),"INVALID CLOCK")

    def test_missing_crypto_status_cannot_be_current(self):
        f=isolated("app.py","crypto_pipeline_age_minutes","crypto_pipeline_health_label")
        recent=datetime.now(timezone.utc).isoformat()
        self.assertEqual(f["crypto_pipeline_health_label"]({
            "updated_at":recent}),"NOT READY")
        self.assertEqual(f["crypto_pipeline_health_label"]({
            "updated_at":recent,"status":"PARTIAL"}),"PARTIAL")

    def test_stale_crypto_status_is_not_green(self):
        f=isolated("app.py","crypto_pipeline_age_minutes","crypto_pipeline_health_label")
        stale=(datetime.now(timezone.utc)-timedelta(minutes=150)).isoformat()
        self.assertEqual(f["crypto_pipeline_health_label"]({
            "updated_at":stale,"status":"CURRENT"}),"STALE")

    def test_meme_future_timestamp_remains_negative_age(self):
        f=isolated("meme_app.py","meme_pipeline_age_minutes")
        future=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
        self.assertLess(f["meme_pipeline_age_minutes"]({"updated_at":future}),-5)


if __name__=="__main__":
    unittest.main()
