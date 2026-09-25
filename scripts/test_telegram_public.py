"""Offline tests for the public Telegram channel source (t.me/s/<channel>).

The fixture copies the markup t.me served for coin369channel on 2026-09-25.
The contract: each message becomes one item with its own link and timestamp,
the leading "🔹 10:35:" marker is not part of the text, messages without text
are skipped, and run_once uses the message itself as the article - it never
tries to fetch a page for it.
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []
FEED = {"name": "Coin369", "type": "telegram_public", "url": "https://t.me/s/coin369channel",
        "category": "world_macro", "priority": 2, "sourceTier": "repost", "minimumTextChars": 80}


def message(post: str, stamp: str, inner: str) -> str:
    text = (f'<div class="tgme_widget_message_text js-message_text" dir="auto">{inner}</div>'
            if inner is not None else '<div class="tgme_widget_message_photo_wrap"></div>')
    return (
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        f'<div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="{post}" data-view="x">'
        '<div class="tgme_widget_message_bubble">'
        '<div class="tgme_widget_message_author accent_color"><a class="tgme_widget_message_owner_name" '
        'href="https://t.me/coin369channel"><span dir="auto">Tin nhanh - 369</span></a></div>'
        f'{text}'
        '<div class="tgme_widget_message_footer compact js-message_footer"><div class="tgme_widget_message_info">'
        '<span class="tgme_widget_message_views">45</span><span class="tgme_widget_message_meta">'
        f'<a class="tgme_widget_message_date" href="https://t.me/{post}"><time datetime="{stamp}" class="time">'
        '03:35</time></a></span></div></div></div></div></div>')


EMOJI = ('<i class="emoji" style="background-image:url(\'//telegram.org/img/emoji/40/F09F94B9.png\')">'
         '<b>🔹</b></i>')
PAGE = ("<html><body><section>"
        + message("coin369channel/354808", "2026-09-25T03:35:11+00:00",
                  f"{EMOJI}  10:35:  Lợi suất trái phiếu chính phủ chuẩn kỳ hạn 10 năm của Ấn Độ là 7,1347%; "
                  "tăng lên mức cao nhất kể từ ngày 20 tháng 5.")
        + message("coin369channel/354809", "2026-09-25T03:44:12+00:00", None)
        + message("coin369channel/354810", "2026-09-25T03:47:12+00:00",
                  f"{EMOJI} 10:47: Thủ tướng &quot;Netanyahu&quot; phát biểu tại Liên hợp quốc.<br/>"
                  "Đa số đại diện các quốc gia đã rời khỏi hội trường để phản đối.")
        + "</section></body></html>").encode("utf-8")


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def main() -> int:
    print("parser")
    items = bot.parse_telegram_channel(PAGE, FEED)
    check("two text messages, photo-only skipped", len(items) == 2, len(items))
    first, second = items
    check("marker stripped from title", first["title"].startswith("Lợi suất trái phiếu"), first["title"])
    check("title is the first sentence", first["title"].endswith("20 tháng 5."), first["title"])
    check("link per message", first["link"] == "https://t.me/coin369channel/354808", first["link"])
    check("timestamp kept and parseable", bot.parse_time(first["published"]) > 0, first["published"])
    check("inline article is the text", first["inline_article"].startswith("Lợi suất"), first["inline_article"])
    check("feed metadata copied", first["source"] == "Coin369" and first["priority"] == 2
          and first["minimumTextChars"] == 80, first)
    check("entities decoded", '"Netanyahu"' in second["title"], second["title"])
    check("<br> splits title from body", second["title"].endswith("Liên hợp quốc."), second["title"])
    check("body keeps both lines", "rời khỏi hội trường" in second["inline_article"], second["inline_article"])
    check("title has no emoji marker", "🔹" not in second["title"] and "10:47" not in second["title"],
          second["title"])

    print("parse_feed dispatches on type")
    with patch.object(bot, "fetch_url", return_value=PAGE):
        check("telegram_public routed to channel parser", len(bot.parse_feed(FEED)) == 2)

    print("run_once uses the inline article and never fetches a page")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    fresh = dict(first, published=now)
    thin = dict(second, published=now, title="Tin ngắn khác hẳn", inline_article="Quá ngắn.",
                link="https://t.me/coin369channel/1")
    seen_articles = []

    def fake_draft(item, *args):
        seen_articles.append(item.get("article_text", ""))
        return "draft", []

    config = {"channel": {"stateDb": "state.json", "draftOutput": "draft.md"},
              "posting": {"telegram": {"mode": "direct", "tokenEnv": "X", "channelId": "@x"},
                          "duplicateWindowHours": 72, "minimumScoreToDraft": 4, "minimumScoreToPost": 4,
                          "maxPostsPerRun": 5, "requireSourceArticle": True},
              "articleContent": {"preferHtml": True}, "feeds": [FEED]}
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(bot, "ROOT", Path(directory)), \
            patch.object(bot, "parse_feed", return_value=[fresh, thin]), \
            patch.object(bot, "score_item", return_value=(4, [], "fixture")), \
            patch.object(bot, "source_article_text", side_effect=AssertionError("must not fetch")), \
            patch.object(bot, "draft_post", side_effect=fake_draft):
        bot.run_once(config, post=False)
        report = (Path(directory) / "draft.md").read_text(encoding="utf-8")
    check("drafted from the message text", seen_articles and seen_articles[0].startswith("Lợi suất"),
          seen_articles)
    check("too-thin message rejected", len(seen_articles) == 1 and "article-text-too-thin" in report, report[-400:])

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All Telegram channel tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
