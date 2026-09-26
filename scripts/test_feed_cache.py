"""Offline tests for conditional GET on feeds.

A 2-minute timer over 50 feeds is only polite if unchanged feeds answer 304.
The contract: validators from the last 200 are sent back, a 304 returns the
cached items (never an empty list - an item skipped last run for budget
reasons must stay a candidate), and feeds without validators are not cached.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []
FEED = {"name": "fixture", "url": "https://example.test/rss"}
RSS = (b"<rss><channel><item><title>Gold rises 2 percent on Fed bets</title>"
       b"<pubDate>Fri, 25 Sep 2026 03:00:00 GMT</pubDate><link>https://example.test/a</link></item>"
       b"</channel></rss>")


class _Response(io.BytesIO):
    def __init__(self, body: bytes, headers: dict):
        super().__init__(body)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def opener(*steps):
    def _open(request, timeout=None):
        _open.requests.append(request)
        step = steps[len(_open.requests) - 1]
        if isinstance(step, Exception):
            raise step
        return step
    _open.requests = []
    return _open


def not_modified():
    return urllib.error.HTTPError(FEED["url"], 304, "Not Modified", {}, io.BytesIO(b""))


def main() -> int:
    real = urllib.request.urlopen
    try:
        bot.FEED_CACHE.clear()
        print("first fetch stores validators and items")
        urllib.request.urlopen = opener(_Response(RSS, {"ETag": '"v1"', "Last-Modified": "Fri, 25 Sep 2026 03:00:00 GMT"}))
        items = bot.parse_feed(FEED)
        check("one item parsed", len(items) == 1, items)
        cached = bot.FEED_CACHE.get(FEED["url"], {})
        check("etag cached", cached.get("etag") == '"v1"', cached)
        check("items cached", len(cached.get("items", [])) == 1, cached)
        check("no validators on a cold fetch",
              urllib.request.urlopen.requests[0].get_header("If-none-match") is None)

        print("304 returns cached items as copies")
        urllib.request.urlopen = opener(not_modified())
        again = bot.parse_feed(FEED)
        sent = urllib.request.urlopen.requests[0]
        check("If-None-Match sent", sent.get_header("If-none-match") == '"v1"', sent.headers)
        check("If-Modified-Since sent", sent.get_header("If-modified-since") == "Fri, 25 Sep 2026 03:00:00 GMT",
              sent.headers)
        check("cached items returned", [i["title"] for i in again] == [items[0]["title"]], again)
        again[0]["title"] = "mutated by run_once"
        check("returned items are copies", bot.FEED_CACHE[FEED["url"]]["items"][0]["title"] != "mutated by run_once")

        print("no validators -> not cached")
        urllib.request.urlopen = opener(_Response(RSS, {}))
        bot.parse_feed(FEED)
        check("entry dropped", FEED["url"] not in bot.FEED_CACHE, bot.FEED_CACHE.keys())

        print("other HTTP errors still raise")
        urllib.request.urlopen = opener(urllib.error.HTTPError(FEED["url"], 500, "err", {}, io.BytesIO(b"")))
        try:
            bot.parse_feed(FEED)
            check("500 raises", False)
        except urllib.error.HTTPError as exc:
            check("500 raises", exc.code == 500)

        print("run_once loads and saves the cache, keeping only fresh items")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_file = root / bot.FEED_CACHE_PATH
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({
                FEED["url"]: {"etag": '"v9"', "items": [
                    {"title": "old", "published": "Mon, 01 Jan 2024 00:00:00 GMT"}]},
                "https://gone.test/rss": {"etag": '"x"', "items": []}}), encoding="utf-8")
            config = {"channel": {"stateDb": "state.json", "draftOutput": "draft.md"},
                      "posting": {"telegram": {"mode": "direct"}, "duplicateWindowHours": 72,
                                  "minimumScoreToDraft": 4, "minimumScoreToPost": 4, "maxPostsPerRun": 1,
                                  "maximumAgeMinutes": 480},
                      "feeds": [FEED]}
            loaded = {}

            def fake_parse(feed):
                loaded.update(bot.FEED_CACHE)
                return []

            with patch.object(bot, "ROOT", root), patch.object(bot, "parse_feed", side_effect=fake_parse):
                bot.run_once(config, post=False)
            saved = json.loads(cache_file.read_text(encoding="utf-8"))
            check("cache loaded before fetching", loaded.get(FEED["url"], {}).get("etag") == '"v9"', loaded)
            check("feeds no longer configured are dropped", "https://gone.test/rss" not in saved, saved.keys())
            check("stale items dropped on save", saved[FEED["url"]]["items"] == [], saved[FEED["url"]])
    finally:
        urllib.request.urlopen = real
        bot.FEED_CACHE.clear()

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All feed-cache tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
