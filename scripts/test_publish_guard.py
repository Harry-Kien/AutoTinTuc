"""Offline tests for the URL guard inside telegram_post.

Regression cover for a real outage: draft_post gained a source-link line, but
telegram_post has its own URL guard that rejected the whole post before
sending. Eight items were written to state as 'pending' and never reached the
channel. draft_post alone was tested, the publisher was not.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []
LINK = "https://example.com/bai-goc-123"
BODY = "🚨 Tin nhanh 247 | 🇺🇸 USD\n⏰ 21/09 13:00 | 🔥 Độ HOT: ⭐️⭐️⭐️⭐️⭐️\n\n🔹 Vàng tăng\n📝 Vàng tăng 2,5%.\n\n🧾 Nguồn: Test\n#VANG"
CFG = {"posting": {"telegram": {"enabled": True, "mode": "bridge", "channelId": "@x"}}}


def check(name: str, cond: bool, detail: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def try_post(text):
    """Return None when telegram_post accepts, else the error message."""
    sent = {}
    saved = bot.telegram_bridge_post
    bot.telegram_bridge_post = lambda tg, t: sent.setdefault("text", t) or {"status": "confirmed"}
    try:
        bot.telegram_post(CFG, text)
        return None
    except RuntimeError as exc:
        return str(exc)
    finally:
        bot.telegram_bridge_post = saved


def main() -> int:
    print("editorial_body tach dung dong link")
    check("bo dong link cuoi",
          bot.editorial_body(BODY + "\n" + bot.SOURCE_LINK_PREFIX + LINK) == BODY)
    check("khong co link thi giu nguyen", bot.editorial_body(BODY) == BODY)

    print("telegram_post")
    check("bai khong link -> gui duoc", try_post(BODY) is None, try_post(BODY))

    withlink = BODY + "\n" + bot.SOURCE_LINK_PREFIX + LINK
    err = try_post(withlink)
    check("bai CO dong link nguon -> gui duoc (loi cu da sua)", err is None, err)

    # URL trong noi dung bien tap van phai bi chan
    smuggled = BODY.replace("📝 Vàng tăng 2,5%.", "📝 Xem tai https://spam.example.com")
    err = try_post(smuggled)
    check("URL trong noi dung -> VAN bi chan", err is not None and "URL" in err, err)

    err = try_post(smuggled + "\n" + bot.SOURCE_LINK_PREFIX + LINK)
    check("URL trong noi dung + co link nguon -> VAN bi chan", err is not None, err)

    print("draft_post va telegram_post dung chung tien to")
    saved = (bot.editorial_for, bot.headline_quality_issues, bot.summary_quality_issues)
    try:
        bot.editorial_for = lambda item, c=None, b=None: ("Vàng tăng", "Vàng tăng 2,5%.")
        bot.headline_quality_issues = lambda t, i: []
        bot.summary_quality_issues = lambda s, i, t: []
        item = {"title": "Gold", "source": "Test", "category": "world_macro",
                "link": LINK, "source_article_verified": True, "article_text": "x" * 400}
        post, _ = bot.draft_post(item, 5, ["#VANG"],
                                 {"posting": {"includeSourceLink": True}}, {"remaining": 0})
        check("draft_post sinh dong link", bot.SOURCE_LINK_PREFIX in post, post[-50:])
        # day moi la diem quan trong: bai do phai qua duoc publisher
        check("bai do telegram_post CHAP NHAN", try_post(post) is None, try_post(post))
    finally:
        (bot.editorial_for, bot.headline_quality_issues, bot.summary_quality_issues) = saved

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All publish-guard tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
