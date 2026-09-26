"""Offline tests for the 20-minute gap fill.

The channel must never stay quiet longer than posting.gapFillMinutes. When it
has, run_once may publish ONE item from below the normal score threshold (the
best fresh one), still through draft_post's gates; normal items always win.
"""
import datetime
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as n  # noqa: E402

TOKEN_ENV = "TEST_GAP_TOKEN"


def config(**posting):
    base = {"telegram": {"mode": "direct", "tokenEnv": TOKEN_ENV, "channelId": "@example"},
            "duplicateWindowHours": 72, "minimumScoreToDraft": 4, "minimumScoreToPost": 4,
            "maxPostsPerRun": 5, "requireSourceArticle": False,
            "gapFillMinutes": 20, "gapFillMinScore": 2}
    base.update(posting)
    return {"channel": {"stateDb": "state.json", "draftOutput": "draft.md"},
            "posting": base, "feeds": [{"name": "fixture", "url": "https://example.test/feed"}]}


def news(title, score, source="fixture"):
    return {"title": title, "source": source, "summary": "", "fake_score": score,
            "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "link": "https://example.test/" + str(abs(hash(title)))}


def last_post(root, minutes_ago):
    state = {"schemaVersion": 2, "seen": {"old": {
        "time": time.time() - minutes_ago * 60, "title": "An older unrelated story about shipping rates",
        "status": "confirmed"}}}
    (root / "state.json").write_text(json.dumps(state), encoding="utf-8")


def ok_draft(item, *args):
    item["vi_title"] = item["title"]
    return "draft " + item["title"], []


class GapFill(unittest.TestCase):
    def setUp(self):
        os.environ[TOKEN_ENV] = "123:fake"

    def run_with(self, root, items, draft=ok_draft, **posting):
        send = lambda *a: {"status": "confirmed", "reason": "telegram-message-ack",  # noqa: E731
                           "messageId": 1, "chatId": "-100"}
        with patch.object(n, "ROOT", root), patch.object(n, "parse_feed", return_value=items), \
                patch.object(n, "score_item", side_effect=lambda item, cfg: (item["fake_score"], [], "fixture")), \
                patch.object(n, "draft_post", side_effect=draft) as drafted, \
                patch.object(n, "telegram_post", side_effect=send) as sent:
            n.run_once(config(**posting), post=True)
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        return drafted, sent, state

    def test_quiet_channel_posts_one_below_threshold_item(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            last_post(root, 25)
            items = [news("Oil prices edge higher as traders weigh supply outlook", 3),
                     news("Regional lender reports steady quarterly deposit growth", 3),
                     news("Minor exchange lists a new token pair for spot trading", 2)]
            drafted, sent, state = self.run_with(root, items)
            self.assertEqual(sent.call_count, 1, "exactly one gap-fill post")
            posted = [v for k, v in state["seen"].items() if k != "old"]
            self.assertEqual(len(posted), 1)
            self.assertTrue(posted[0]["gapFill"])
            self.assertEqual(posted[0]["score"], 3, "the best-scoring leftover is chosen")

    def test_recent_post_means_no_gap_fill(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            last_post(root, 5)
            drafted, sent, _ = self.run_with(root, [news("Oil prices edge higher as traders weigh supply", 3)])
            self.assertEqual(sent.call_count, 0)
            self.assertEqual(drafted.call_count, 0, "below-threshold items are not even drafted")

    def test_normal_items_win_and_suppress_gap_fill(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            last_post(root, 40)
            items = [news("Fed holds rates steady and signals patience on cuts", 5),
                     news("Oil prices edge higher as traders weigh supply outlook", 3)]
            _, sent, state = self.run_with(root, items)
            self.assertEqual(sent.call_count, 1)
            posted = [v for k, v in state["seen"].items() if k != "old"]
            self.assertEqual(posted[0]["score"], 5)
            self.assertFalse(posted[0]["gapFill"])

    def test_gap_fill_respects_the_gates(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            last_post(root, 30)
            items = [news("Weak item one about general market mood today", 3),
                     news("Good item two about gold rising after a data release", 3)]

            def gated(item, *args):
                if "Weak" in item["title"]:
                    return "", ["main-event-not-explicit"]
                return ok_draft(item)

            drafted, sent, state = self.run_with(root, items, draft=gated)
            self.assertEqual(sent.call_count, 1, "a gated-out item is skipped, the next one fills the gap")
            posted = [v for k, v in state["seen"].items() if k != "old"]
            self.assertIn("Good item two", posted[0]["title"])

    def test_empty_history_counts_as_a_gap(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, sent, _ = self.run_with(root, [news("Oil prices edge higher as traders weigh supply", 3)])
            self.assertEqual(sent.call_count, 1)

    def test_disabled_when_minutes_is_zero(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            last_post(root, 300)
            _, sent, _ = self.run_with(root, [news("Oil prices edge higher as traders weigh supply", 3)],
                                       gapFillMinutes=0)
            self.assertEqual(sent.call_count, 0)


if __name__ == "__main__":
    unittest.main()
