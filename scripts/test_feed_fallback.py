"""Offline tests for the RSS-description fallback in source_article_text.

The point under test: the fallback must not become a hole in the quality bar.
It is held to the same min_chars threshold as a fetched article, it only
applies when the page itself could not be read, and it never overrides a page
that did load.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []
LONG = "Gia vang tang manh trong phien giao dich hom nay. " * 12   # > 240 chars
SHORT = "Gia vang tang."


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def main() -> int:
    saved = bot._source_article_text_http
    try:
        print("trang bai doc duoc -> dung trang bai")
        bot._source_article_text_http = lambda item, **k: ("noi dung tu trang bai that", "")
        text, reason = bot.source_article_text({"summary": LONG})
        check("khong dung mo ta RSS khi trang bai OK", text == "noi dung tu trang bai that", text[:40])
        check("ly do giu nguyen", reason == "", reason)

        print("trang bai hong + mo ta RSS day du -> dung mo ta")
        bot._source_article_text_http = lambda item, **k: ("", "article-text-too-thin")
        text, reason = bot.source_article_text({"summary": LONG})
        check("cuu duoc tin", len(text) >= 240, len(text))
        check("danh dau nguon la feed", reason == "feed-description", reason)

        print("mo ta RSS qua ngan -> VAN bi loai (khong ha chuan)")
        text, reason = bot.source_article_text({"summary": SHORT})
        check("mo ta ngan bi tu choi", text == "", text)
        check("giu ly do that bai goc", reason == "article-text-too-thin", reason)

        print("khong co mo ta -> giu nguyen hanh vi cu")
        text, reason = bot.source_article_text({})
        check("khong co mo ta thi bo", text == "" and reason == "article-text-too-thin", (text, reason))

        print("nguong min_chars duoc ton trong")
        bot._source_article_text_http = lambda item, **k: ("", "article-fetch-failed")
        text, _ = bot.source_article_text({"summary": "x" * 300}, min_chars=500)
        check("300 ky tu < nguong 500 -> loai", text == "", len(text))
        text, _ = bot.source_article_text({"summary": "x" * 600}, min_chars=500)
        check("600 ky tu >= nguong 500 -> nhan", len(text) == 600, len(text))

        print("cat theo max_chars")
        text, _ = bot.source_article_text({"summary": "y" * 5000}, max_chars=1000)
        check("cat dung max_chars", len(text) == 1000, len(text))

        print("chap nhan ca truong description")
        text, reason = bot.source_article_text({"description": LONG})
        check("doc duoc truong description", reason == "feed-description", reason)

        print("loc HTML trong mo ta")
        text, _ = bot.source_article_text({"summary": "<p>" + LONG + "</p><script>x</script>"})
        check("khong con the HTML", "<p>" not in text and "<script>" not in text, text[:40])
    finally:
        bot._source_article_text_http = saved

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All feed-fallback tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
