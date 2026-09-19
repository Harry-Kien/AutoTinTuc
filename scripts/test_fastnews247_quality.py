"""Regression checks for Fast News 247 public-copy quality gates."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as news


def issues(headline: str) -> list[str]:
    return news.headline_quality_issues(
        headline,
        {"source": "Nguồn kiểm thử", "source_article_verified": True},
    )


def main() -> None:
    rejected = [
        "Đây là những gì thay đổi trong tuyên bố mới của Fed",
        "Tác động lớn nhất sau nâng hạng thị trường chứng khoán Việt Nam",
        "Lãi suất ngân hàng 19/9 tại MB, Techcombank và VPBank tiếp tục phân hóa đáng kể",
        "Vì sao giá vàng tăng mạnh trong phiên hôm nay?",
    ]
    accepted = [
        "Fed tăng lãi suất 25 điểm cơ bản lên 5,5% và phát tín hiệu còn một đợt tăng trong năm nay",
        "CPI Mỹ tháng 8 ở mức 3,2%, cao hơn dự báo 3,0% và gây áp lực lên kỳ vọng giảm lãi suất",
        "VN-Index tăng 20 điểm, khối ngoại mua ròng 2.600 tỷ đồng trong phiên giao dịch ngày 19/9",
    ]

    for headline in rejected:
        assert issues(headline), f"Expected rejection: {headline}"
    for headline in accepted:
        assert not issues(headline), f"Unexpected rejection: {headline} -> {issues(headline)}"

    assert news.strip_urls("Tin mới https://example.com/a") == "Tin mới"
    assert "..." not in news.shorten_title("Fed tăng lãi suất 25 điểm cơ bản. " + "A" * 400)
    assert "trading-advice" in issues("Fed tăng lãi suất 25 điểm cơ bản; nhà đầu tư nên mua USD ngay")
    assert "source-article-unverified" in news.headline_quality_issues(
        accepted[0], {"source": "Nguồn kiểm thử"}
    )
    iso_item = {"published": "2026-09-19T08:30:00Z"}
    assert news.parse_time(iso_item["published"]) > 0

    parser = news.ArticleHTMLParser()
    parser.feed('<meta property="og:description" content="Fed tăng lãi suất 25 điểm cơ bản sau cuộc họp và công bố dự báo kinh tế mới cho thị trường toàn cầu."><p>Fed tăng lãi suất 25 điểm cơ bản sau cuộc họp chính sách tiền tệ, đồng thời công bố dự báo kinh tế mới cho thị trường toàn cầu.</p>')
    assert parser.paragraphs and parser.descriptions
    print("Fast News 247 quality checks passed.")


class FastNewsQualityTests(unittest.TestCase):
    def test_existing_regressions(self):
        main()

    def test_article_parser_ignores_navigation_and_prefers_json_article_body(self):
        page = '''<html><nav><p>Subscribe to our newsletter and follow every market link today.</p></nav>
        <script type="application/ld+json">{"@type":"NewsArticle","description":"Newsletter teaser that must not replace the complete source article body even though this description is long enough for extraction.","articleBody":"The Federal Reserve held its policy rate at 5.50% after the September meeting. Chair Powell said inflation remained above the 2% target and officials needed more evidence before cutting rates."}</script>
        <p>The Federal Reserve held its policy rate at 5.50% after the September meeting and published a statement explaining its latest decision to financial markets.</p></html>'''
        response = MagicMock()
        response.headers.get.return_value = "text/html; charset=utf-8"
        response.headers.get_content_charset.return_value = "utf-8"
        response.read.return_value = page.encode()
        response.__enter__.return_value = response
        item = {"title": "Federal Reserve holds rate at 5.50% after September meeting", "link": "https://example.test/article"}
        with patch("urllib.request.urlopen", return_value=response):
            article, issue = news.source_article_text(item, min_chars=100)
        self.assertEqual(issue, "")
        self.assertIn("5.50%", article)
        self.assertNotIn("newsletter", article)
        self.assertEqual(article.count("Federal Reserve held"), 1)

    def test_editorial_output_has_separate_grounded_summary(self):
        item = {
            "title": "Federal Reserve holds rate at 5.50% after September meeting",
            "article_text": "The Federal Reserve held its policy rate at 5.50% after the September meeting. Chair Powell said inflation remained above the 2% target and officials needed more evidence before cutting rates.",
            "source": "Official source", "source_article_verified": True, "category": "official_macro",
        }
        translations = {
            item["title"]: "Fed giữ lãi suất ở mức 5,50% sau cuộc họp tháng 9",
            "The Federal Reserve held its policy rate at 5.50% after the September meeting.": "Fed giữ lãi suất chính sách ở mức 5,50% sau cuộc họp tháng 9.",
            "Chair Powell said inflation remained above the 2% target and officials needed more evidence before cutting rates.": "Chủ tịch Powell cho biết lạm phát vẫn cao hơn mục tiêu 2% và các quan chức cần thêm dữ liệu trước khi giảm lãi suất.",
        }
        with patch.object(news, "translate_text_to_vi", side_effect=lambda text: translations[text]):
            title, summary = news.vietnamese_editorial(item)
        self.assertIn("5,50%", title)
        self.assertEqual(len(news.split_complete_sentences(summary)), 1)
        self.assertNotIn("Fed giữ lãi suất chính sách ở mức 5,50%", summary)
        self.assertIn("Chủ tịch Powell", summary)
        self.assertFalse(news.summary_quality_issues(summary, item))

    def test_editorial_fails_closed_without_fact_sentence(self):
        item = {
            "title": "Market outlook attracts attention",
            "article_text": "Investors discussed the market in a broad interview without announcing any decision.",
        }
        with patch.object(news, "translate_text_to_vi", return_value="Triển vọng thị trường thu hút sự chú ý"):
            self.assertEqual(news.vietnamese_editorial(item), ("", ""))

    def test_summary_rejects_new_number_and_advice(self):
        item = {"article_text": "Fed tăng lãi suất 25 điểm cơ bản."}
        self.assertIn("summary-number-not-in-source", news.summary_quality_issues("Fed tăng lãi suất 50 điểm cơ bản.", item))
        self.assertIn("trading-advice", news.summary_quality_issues("Fed tăng lãi suất 25 điểm cơ bản và nhà đầu tư nên mua USD ngay.", item))

    def test_summary_must_add_information_beyond_title(self):
        title = "Fed giữ lãi suất ở mức 5,50% sau cuộc họp tháng 9"
        item = {"article_text": "Fed giữ lãi suất ở mức 5,50% sau cuộc họp tháng 9."}
        repeated = "Fed giữ lãi suất ở mức 5,50% sau cuộc họp tháng 9."
        self.assertIn(
            "summary-adds-no-new-information",
            news.summary_quality_issues(repeated, item, title),
        )

        item["article_text"] += " Chủ tịch Powell cho biết lạm phát vẫn cao hơn mục tiêu 2%."
        informative = "Chủ tịch Powell cho biết lạm phát vẫn cao hơn mục tiêu 2%."
        self.assertNotIn(
            "summary-adds-no-new-information",
            news.summary_quality_issues(informative, item, title),
        )

    def test_public_draft_has_required_sections_and_no_url(self):
        item = {
            "title": "Fed giữ lãi suất ở mức 5,5% sau cuộc họp tháng 9",
            "article_text": "Fed giữ lãi suất ở mức 5,5% sau cuộc họp tháng 9 và công bố dự báo kinh tế mới.",
            "source": "Cục Dự trữ Liên bang", "source_article_verified": True, "category": "official_macro",
        }
        with patch.object(news, "translate_text_to_vi", side_effect=lambda text: text):
            draft, issues_found = news.draft_post(item, 5, ["#DXY", "#FED"])
        self.assertFalse(issues_found)
        self.assertIn("🚨 Tin nhanh 247 | 🇺🇸 Mỹ", draft)
        self.assertIn("#news #Tinnhanh247 #DXY #FED", draft)
        self.assertNotIn("#Fastnews", draft)
        self.assertNotIn("₿", draft)
        self.assertNotIn("ICT", draft)
        self.assertIn("🔹 Fed", draft)
        self.assertNotIn("🔹 Tin tức:", draft)
        self.assertIn("\n📝 Fed", draft)
        self.assertNotIn("Tóm tắt:", draft)
        self.assertIn("🔥 Độ HOT: ⭐️⭐️⭐️⭐️⭐️", draft)
        self.assertNotIn("🏷 Chủ đề:", draft)
        self.assertIn("🧾 Nguồn: Cục Dự trữ Liên bang", draft)
        self.assertNotRegex(draft, r"https?://|www\.")

        draft_four, issues_found = news.draft_post(item, 4, ["#DXY", "#FED"])
        self.assertFalse(issues_found)
        self.assertIn("🚨 Tin nhanh 247 |", draft_four)
        self.assertNotIn("⚡️", draft_four.splitlines()[0])
        self.assertIn("🔥 Độ HOT: ⭐️⭐️⭐️⭐️", draft_four)

        draft_four, issues_found = news.draft_post(item, 4, ["#DXY", "#FED"])
        self.assertFalse(issues_found)
        self.assertIn("🚨 Tin nhanh 247 |", draft_four)
        self.assertNotIn("⚡️", draft_four.splitlines()[0])
        self.assertIn("🔥 Độ HOT: ⭐️⭐️⭐️⭐️", draft_four)

    def test_fingerprint_deduplicates_same_story_across_sources(self):
        first = {
            "title": "ECB giữ nguyên lãi suất sau cuộc họp tháng 9",
            "published": "Sat, 19 Sep 2026 08:00:00 GMT",
            "source": "BBC Europe",
            "link": "https://example.test/one",
        }
        syndicated = {
            **first,
            "source": "BBC Business",
            "link": "https://example.test/two?utm_source=rss",
        }
        next_day = {**first, "published": "Sun, 20 Sep 2026 08:00:00 GMT"}
        self.assertEqual(news.fingerprint(first), news.fingerprint(syndicated))
        self.assertNotEqual(news.fingerprint(first), news.fingerprint(next_day))


if __name__ == "__main__":
    unittest.main(verbosity=2)
