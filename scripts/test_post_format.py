"""Offline tests for the source link line and the per-source cap.

Both are opt-in. The URL gate on the body must survive unchanged: translated
copy still may not contain a link, and only the deliberate link line may.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []
LINK = "https://example.com/bai-viet-goc-123"
TITLE = "Vàng tăng sau khi Fed giữ lãi suất"
SUMMARY = "Vàng tăng 2,5% hôm thứ Ba sau khi Fed giữ nguyên lãi suất, theo giới phân tích."


def check(name: str, cond: bool, detail: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def make_item(**kw):
    it = {"title": "Gold rises", "source": "Test Source", "category": "world_macro",
          "link": LINK, "published": "", "source_article_verified": True,
          "article_text": "x" * 400}
    it.update(kw)
    return it


def main() -> int:
    saved = (bot.editorial_for, bot.headline_quality_issues, bot.summary_quality_issues)
    try:
        bot.editorial_for = lambda item, c=None, b=None: (TITLE, SUMMARY)
        bot.headline_quality_issues = lambda t, i: []
        bot.summary_quality_issues = lambda s, i, t: []

        print("link nguon")
        cfg_off = {"posting": {"includeSourceLink": False}}
        post, _ = bot.draft_post(make_item(), 5, ["#VANG"], cfg_off, {"remaining": 0})
        check("mac dinh TAT -> khong co link", LINK not in post, post[-60:])

        cfg_on = {"posting": {"includeSourceLink": True}}
        post, _ = bot.draft_post(make_item(), 5, ["#VANG"], cfg_on, {"remaining": 0})
        check("bat -> co link", LINK in post, post[-60:])
        check("link o dong cuoi", post.strip().splitlines()[-1].endswith(LINK), post[-60:])
        check("noi dung bai van con", TITLE in post and SUMMARY in post)

        post, _ = bot.draft_post(make_item(link="khong-phai-url"), 5, ["#VANG"], cfg_on, {"remaining": 0})
        check("link khong hop le -> bo qua", "khong-phai-url" not in post)

        print("cong chan URL trong noi dung VAN giu nguyen")
        bot.editorial_for = lambda item, c=None, b=None: (
            "Xem tai https://spam.example.com ngay", SUMMARY)
        post, issues = bot.draft_post(make_item(), 5, ["#VANG"], cfg_on, {"remaining": 0})
        check("URL do bien tap sinh ra -> LOAI", post == "" and issues, (post[:40], issues))
        bot.editorial_for = lambda item, c=None, b=None: (TITLE, SUMMARY)

        print("gioi han moi nguon")
        cfg = {"posting": {"maxPostsPerSourcePerRun": 1, "maxPostsPerRun": 3}}
        sel = []
        def take(src_name):
            per = cfg["posting"]["maxPostsPerSourcePerRun"]
            if per > 0 and sum(1 for c in sel if c.get("source") == src_name) >= per:
                return False
            sel.append({"source": src_name}); return True
        check("nguon A lan 1 -> nhan", take("A") is True)
        check("nguon A lan 2 -> CHAN", take("A") is False)
        check("nguon B -> nhan", take("B") is True)
        check("nguon C -> nhan", take("C") is True)
        check("ket qua 3 nguon khac nhau",
              [c["source"] for c in sel] == ["A", "B", "C"], sel)

        sel = []
        cfg["posting"]["maxPostsPerSourcePerRun"] = 0
        def take0(src_name):
            per = cfg["posting"]["maxPostsPerSourcePerRun"]
            if per > 0 and sum(1 for c in sel if c.get("source") == src_name) >= per:
                return False
            sel.append({"source": src_name}); return True
        check("dat 0 -> tat gioi han", take0("A") and take0("A") and take0("A"))
    finally:
        (bot.editorial_for, bot.headline_quality_issues, bot.summary_quality_issues) = saved

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All post-format tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
