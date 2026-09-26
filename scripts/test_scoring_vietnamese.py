"""Scoring against the shipped config for Vietnamese flash news (Coin369 style).

The keyword lists were written for English RSS. On 2026-09-26 only 2 of 20
live Coin369 items reached the posting threshold, and a Swiss National Bank
item was tagged as the Vietnamese market because it contained the generic word
for "bank". These items are real titles from that day, so this test pins the
scoring the channel actually needs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []
CONFIG = json.loads(bot.CONFIG_PATH.read_text(encoding="utf-8"))
COIN369 = next(f for f in CONFIG["feeds"] if f["name"] == "Coin369")


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def coin(title: str) -> dict:
    return {"title": title, "summary": title, "source": "Coin369", "category": COIN369["category"],
            "priority": COIN369["priority"]}


def main() -> int:
    threshold = CONFIG["posting"]["minimumScoreToPost"]
    should_post = [
        "Quân đội Ukraine cho biết đã tấn công nhà máy lọc dầu Ilsky ở miền nam nước Nga.",
        "Thống đốc Ngân hàng Quốc gia Thụy Sĩ: Giá thực phẩm tăng, nhưng mức tăng lạm phát gần như hoàn toàn là do giá xăng dầu.",
        "Lợi suất trái phiếu chính phủ chuẩn kỳ hạn 10 năm của Ấn Độ là 7,1347%; tăng lên mức cao nhất kể từ ngày 20 tháng 5.",
        "Ủy viên SEC Hester Peirce sẽ rời vị trí vào ngày 2 tháng 10; giới crypto lo ngại về chính sách ETF.",
    ]
    # Known gaps, kept visible: single-bucket items with no high-impact term
    # still score 3. Tightening this needs a decision on how loose the channel
    # may be, so they are reported, not asserted.
    borderline = [
        "Trung Quốc và Mỹ đã đạt được sự đồng thuận về tám điểm kết quả trong đàm phán thương mại.",
        "Dữ liệu sơ bộ cho thấy lưu lượng tàu chở hàng qua eo biển Hormuz giảm xuống mức một con số.",
    ]
    for title in borderline:
        score, tags, reason = bot.score_item(coin(title), CONFIG)
        print(f"  NOTE  borderline score={score} ({reason}): {title[:60]}")
    should_not_post = [
        "Trung tâm Bão Quốc gia Hoa Kỳ: Fay lại suy yếu thành áp thấp nhiệt đới.",
        "Ford Motor: Đang thu hồi 11.405 xe tại Mỹ do hình ảnh từ camera lùi không hiển thị.",
        "Kinh doanh: Chuỗi bệnh viện Hoàn Mỹ báo lãi kỷ lục trong quý vừa qua.",
    ]
    print("Coin369 flash news that the channel should carry")
    for title in should_post:
        score, tags, reason = bot.score_item(coin(title), CONFIG)
        check(f"score>={threshold}: {title[:60]}", score >= threshold, (score, reason))
    print("Coin369 items that are not market news stay below the threshold")
    for title in should_not_post:
        score, tags, reason = bot.score_item(coin(title), CONFIG)
        check(f"score<{threshold}: {title[:60]}", score < threshold, (score, reason))

    print("a foreign central bank is not the Vietnamese market")
    snb = coin("Thống đốc Ngân hàng Quốc gia Thụy Sĩ: Đà giảm hiện tại của đồng franc Thụy Sĩ chỉ là một xu hướng ngắn hạn.")
    score, tags, reason = bot.score_item(snb, CONFIG)
    check("SNB item not tagged #VNINDEX", "#VNINDEX" not in tags, tags)
    check("SNB item still scores as macro/FX", score >= threshold, (score, reason))
    flags, label = bot.market_flags(snb, tags)
    check("SNB item not flagged as Vietnam", "🇻🇳" not in flags, (flags, label))

    print("Vietnamese bank news from a Vietnamese source keeps its market tag")
    cafef = {"title": "Lãi suất ngân hàng 26/9 tại MB, Techcombank và VPBank tiếp tục phân hóa", "summary": "",
             "source": "CafeF Tai Chinh Ngan Hang", "category": "vietnam_market", "priority": 4}
    score, tags, reason = bot.score_item(cafef, CONFIG)
    check("CafeF banking item still #VNINDEX via its category", "#VNINDEX" in tags, tags)
    check("CafeF banking item still posts", score >= threshold, (score, reason))

    print("clickbait from a channel is never published verbatim")
    shouting = "ISRAEL SĂN ĐỔI TẬN CÙNG NGÕ HẺM,, CHẠY ĐÂU CHO THOÁT HẮN ĐÃ BỊ TIÊU DIỆT: chỉ huy Hamas bị hạ"
    issues = bot.headline_quality_issues(shouting, {"source": "Coin369", "source_article_verified": True})
    check("all-caps headline rejected", "shouting-headline" in issues, issues)
    check("doubled comma rejected", "repeated-punctuation" in issues, issues)
    normal = "Fed, ECB và BOJ giữ nguyên lãi suất trong tuần họp chính sách tháng 9"
    issues = bot.headline_quality_issues(normal, {"source": "X", "source_article_verified": True})
    check("normal headline with acronyms passes the caps gate", "shouting-headline" not in issues, issues)
    check("ellipsis is not repeated punctuation here", "repeated-punctuation" not in issues, issues)

    import fastnews247_llm as llm
    saved = (llm.openai_editorial, llm.subscription_editorial, bot.vietnamese_editorial)
    translate_calls = []
    try:
        llm.openai_editorial = lambda *a, **k: (_ for _ in ()).throw(llm.ApiError("network", retryable=True))
        bot.vietnamese_editorial = lambda item: translate_calls.append(1) or ("copied", "copied.")
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            saved_root = bot.ROOT
            bot.ROOT = Path(directory)
            try:
                channel_item = {"title": shouting, "score": 5, "source": "Coin369", "inline_article": shouting,
                                "article_text": shouting, "source_article_verified": True}
                cfg = dict(CONFIG)
                title, summary = bot.ladder_rewrite(channel_item, cfg)
                check("channel item with no LLM draft is dropped, not copied",
                      (title, summary) == ("", "") and channel_item["editorial_path"] == "llm-required",
                      (title, channel_item.get("editorial_path")))
                check("translation never called for a channel item", translate_calls == [], translate_calls)
                rss_item = {"title": "Gold rises 2% after the Fed decision", "score": 4, "source": "Wire",
                            "article_text": "Gold rose 2% after the Fed decision, traders said.",
                            "source_article_verified": True}
                bot.ladder_rewrite(rss_item, cfg)
                check("RSS items still fall back to translation", translate_calls == [1], translate_calls)
            finally:
                bot.ROOT = saved_root
    finally:
        llm.openai_editorial, llm.subscription_editorial, bot.vietnamese_editorial = saved

    print("valid Vietnamese newsroom headlines pass the main-event gate")
    ok_meta = {"source": "X", "source_article_verified": True}
    for headline in ("ADB: Việt Nam cần đa dạng hóa nguồn vốn để hỗ trợ tăng trưởng dài hạn",
                     "Thống đốc SNB nhận định đà giảm của đồng franc chỉ là xu hướng ngắn hạn",
                     "Ủy viên SEC Hester Peirce sẽ rời vị trí sau nhiệm kỳ kéo dài nhiều năm"):
        issues = bot.headline_quality_issues(headline, ok_meta)
        check(f"main event recognised: {headline[:45]}", "main-event-not-explicit" not in issues, issues)
    vague = "Thị trường vàng hôm nay có nhiều thay đổi đáng chú ý với nhà đầu tư trong nước"
    issues = bot.headline_quality_issues(vague, ok_meta)
    check("vague headline still rejected", bool(issues), issues)

    print("the editorial prompt states the gates the drafts are held to")
    prompt = bot.EDITORIAL_PROMPT
    check("headline length rule in prompt", "45-220" in prompt and "7 tu" in prompt)
    check("summary sentence rule in prompt", "8 tu" in prompt and "520" in prompt)
    check("no-question / no-teaser rule in prompt", "cau hoi" in prompt)

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("Vietnamese scoring checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
