"""Offline tests for the guards that keep a paid editor affordable.

1. An item the gates rejected is not re-drafted (re-paid) every 2 minutes.
2. The per-source cap is applied before drafting, not after paying for it.
3. Vietnamese titles are compared with what was already published, so an
   English source and a Vietnamese one about the same event post only once.
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

TOKEN_ENV = "TEST_GUARD_TOKEN"
POSTED_VI = "Vàng tăng 2,5% sau khi Fed giữ nguyên lãi suất trong cuộc họp tháng 9"


def config(**posting):
    base = {"telegram": {"mode": "direct", "tokenEnv": TOKEN_ENV, "channelId": "@example"},
            "duplicateWindowHours": 72, "minimumScoreToDraft": 4, "minimumScoreToPost": 4,
            "maxPostsPerRun": 5, "requireSourceArticle": False}
    base.update(posting)
    return {"channel": {"stateDb": "state.json", "draftOutput": "draft.md"},
            "posting": base, "feeds": [{"name": "fixture", "url": "https://example.test/feed"}]}


def news(title, source="fixture"):
    return {"title": title, "source": source, "summary": "",
            "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "link": "https://example.test/" + str(abs(hash(title)))}


def seed_posted(root):
    state = {"schemaVersion": 2, "seen": {"old": {
        "time": time.time(), "title": "Gold climbs after Fed holds", "status": "confirmed",
        "postedTitle": POSTED_VI}}}
    (root / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


class Guards(unittest.TestCase):
    def setUp(self):
        os.environ[TOKEN_ENV] = "123:fake"

    def run_with(self, root, items, draft, send=None, **posting):
        send = send or (lambda *a: {"status": "confirmed", "reason": "telegram-message-ack",
                                    "messageId": 1, "chatId": "-100"})
        with patch.object(n, "ROOT", root), patch.object(n, "parse_feed", return_value=items), \
                patch.object(n, "score_item", return_value=(4, [], "fixture")), \
                patch.object(n, "draft_post", side_effect=draft) as drafted, \
                patch.object(n, "telegram_post", side_effect=send) as sent:
            n.run_once(config(**posting), post=True)
        return drafted, sent

    def test_gate_rejection_is_remembered_then_retried_after_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            items = [news("Fed holds rates at 5 percent after the September meeting")]
            reject = lambda item, *a: ("", ["headline-too-thin"])  # noqa: E731
            drafted, _ = self.run_with(root, items, reject)
            self.assertEqual(drafted.call_count, 1)
            drafted, _ = self.run_with(root, items, reject)
            self.assertEqual(drafted.call_count, 0, "rejected item was re-drafted within the window")
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            state["rejected"] = {key: 0 for key in state["rejected"]}
            (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
            drafted, _ = self.run_with(root, items, reject)
            self.assertEqual(drafted.call_count, 1, "expired rejection was not retried")

    def test_per_source_cap_applies_before_drafting(self):
        with tempfile.TemporaryDirectory() as d:
            items = [news("Gold rises 2 percent as the dollar slips on Fed bets"),
                     news("Oil climbs 3 percent after OPEC agrees to extend cuts")]
            ok = lambda item, *a: (item.__setitem__("vi_title", item["title"]) or ("draft", []))  # noqa: E731
            drafted, sent = self.run_with(Path(d), items, ok, maxPostsPerSourcePerRun=1)
            self.assertEqual(drafted.call_count, 1)
            self.assertEqual(sent.call_count, 1)

    def test_english_item_duplicating_a_posted_vietnamese_title_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_posted(root)
            items = [news("Bullion gains 2.5 percent as Federal Reserve keeps rates unchanged", "other")]

            def rewrite(item, *a):
                item["vi_title"] = POSTED_VI
                return "draft", []

            drafted, sent = self.run_with(root, items, rewrite)
            self.assertEqual(drafted.call_count, 1)
            self.assertEqual(sent.call_count, 0)
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["rejected"]), 1, "duplicate should be remembered, not re-paid")

    def test_vietnamese_source_duplicate_is_dropped_before_paying(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_posted(root)
            drafted, sent = self.run_with(root, [news(POSTED_VI, "Coin369")], lambda item, *a: ("draft", []))
            self.assertEqual(drafted.call_count, 0)
            self.assertEqual(sent.call_count, 0)

    def test_posted_title_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            vi = "Dầu tăng 3% sau khi OPEC đồng ý gia hạn cắt giảm sản lượng"
            ok = lambda item, *a: (item.__setitem__("vi_title", vi) or ("draft", []))  # noqa: E731
            self.run_with(root, [news("Oil climbs 3 percent after OPEC agrees to extend output cuts")], ok)
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual([v.get("postedTitle") for v in state["seen"].values()], [vi])

    def test_helper(self):
        state = {"seen": {"a": {"postedTitle": POSTED_VI}}}
        self.assertTrue(n.duplicates_posted_vietnamese(POSTED_VI, state, []))
        self.assertTrue(n.duplicates_posted_vietnamese(POSTED_VI, {"seen": {}}, [{"vi_title": POSTED_VI}]))
        self.assertFalse(n.duplicates_posted_vietnamese("", state, []))
        self.assertFalse(n.duplicates_posted_vietnamese(
            "Dầu tăng 3% sau khi OPEC đồng ý gia hạn cắt giảm sản lượng", state, []))


if __name__ == "__main__":
    unittest.main()
