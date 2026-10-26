import unittest
from scripts.audit_funded_missed_episodes import episodes


def row(hour=0, **overrides):
    x = dict(asset="ETH", bar_utc=f"2026-10-01T{hour:02d}:00:00Z",
             source_bar_utc=f"2026-10-01T{hour:02d}:00:00Z",
             original_label="WATCH", outcome="MOVER", horizon_bars="3",
             fold="TRAIN", eligible="true", reason="")
    x.update(overrides)
    return x


class MissedEpisodesTests(unittest.TestCase):
    def test_overlapping_anchors_not_independent_trades(self):
        self.assertEqual(len(episodes([row(0), row(4), row(8)])["episodes"]), 1)

    def test_nonoverlap_creates_second_episode(self):
        self.assertEqual(len(episodes([row(0), row(12)])["episodes"]), 2)

    def test_repeated_source_bar_deduplicated(self):
        self.assertEqual(len(episodes([row(0), row(4, source_bar_utc=row(0)["source_bar_utc"])])["episodes"]), 1)

    def test_original_labels_preserved(self):
        result = episodes([row(0, original_label="PASS"), row(12, original_label="BUY")])
        self.assertEqual([r["original_label"] for r in result["episodes"]], ["PASS", "BUY"])

    def test_nonmovers_not_discarded(self):
        result = episodes([row(0), row(4, outcome="NONMOVER")])
        self.assertEqual(len(result["episodes"]), 2)

    def test_unknown_kept_separate(self):
        self.assertEqual(len(episodes([row(0), row(4, outcome="UNKNOWN")])["episodes"]), 2)

    def test_ineligible_is_not_a_rejected_pass(self):
        r = episodes([row(0, eligible="false", reason="PRELISTING")])
        self.assertEqual(len(r["episodes"]), 0)
        self.assertEqual(r["excluded"]["PRELISTING"], 1)

    def test_future_source_rejected(self):
        with self.assertRaises(ValueError):
            episodes([row(0, source_bar_utc=row(4)["source_bar_utc"])])

    def test_no_naive_timestamp(self):
        with self.assertRaises(ValueError):
            episodes([row(0, bar_utc="2026-10-01T00:00:00")])

    def test_invalid_label_rejected(self):
        with self.assertRaises(ValueError):
            episodes([row(0, original_label="READY")])


if __name__ == "__main__":
    unittest.main()
