"""Tin nhanh 247 MVP news runner.

Fetches RSS feeds, scores market relevance, drafts Telegram-ready posts,
deduplicates items, and optionally posts to Telegram when explicitly enabled.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import tempfile
from contextlib import contextmanager
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "fastnews247.sources.json"
VIETNAM_TZ = dt.timezone(dt.timedelta(hours=7), "Asia/Saigon")
URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b(?:[a-z0-9-]+\.)+[a-z]{2,63}\b(?:/\S*)?")
GENERIC_HEADLINE_PHRASES = (
    "có tin mới",
    "có diễn biến mới",
    "diễn biến mới trên thị trường",
    "thông tin mới",
    "tin thị trường mới",
    "cần chú ý",
    "cần theo dõi",
    "đây là những gì",
    "điều cần biết",
    "trao đổi tích cực",
    "đang thu hút chú ý",
    "nhà đầu tư chú ý",
    "nhiều thay đổi",
    "thị trường toàn cầu",
    "latest numbers",
    "top stories",
    "what to know",
    "tác động lớn nhất",
    "đánh giá tác động",
    "điều quan trọng hơn",
    "nói về những",
    "nói về việc",
    "sẽ thế nào",
    "sẽ ra sao",
    "vì sao",
    "những gì thay đổi",
    "thông tin chi tiết",
)


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", strip_html(value).lower()).strip()


def strip_urls(value: str) -> str:
    # Preserve domain-shaped brand entities without generating Telegram links.
    value = re.sub(r"(?i)(?<![/\w])crypto\.com\b", "Crypto chấm com", value or "")
    return re.sub(r"\s+", " ", URL_PATTERN.sub("", strip_html(value))).strip(" -|,")


def term_matches(text: str, term: str) -> bool:
    term = term.lower().strip()
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9\- ]{0,12}", term):
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return term in text


FETCH_AUDIT = {}


def fetch_url(url: str, timeout: int = 10) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TinNhanh247/1.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("response-too-large")
        FETCH_AUDIT[url] = {"httpDate": response.headers.get("Date"),
                            "age": response.headers.get("Age"),
                            "lastModified": response.headers.get("Last-Modified"),
                            "cache": response.headers.get("X-Cache"),
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "fetchedAt": dt.datetime.now(dt.timezone.utc).isoformat()}
        return raw


def child_text(node: ET.Element, names: list[str]) -> str:
    for name in names:
        found = node.find(name)
        if found is None and not name.startswith("{"):
            found = next((child for child in node if child.tag.split("}")[-1] == name), None)
        if found is not None and found.text:
            return found.text
    return ""


def parse_feed(feed: dict) -> list[dict]:
    raw = fetch_url(feed["url"], timeout=int(feed.get("timeoutSeconds", 10)))
    root = ET.fromstring(raw)
    items = [node for node in root.iter() if node.tag.split("}")[-1] == "item"]
    if not items:
        items = root.findall("{http://www.w3.org/2005/Atom}entry")

    parsed = []
    for item in items[:30]:
        title = strip_html(child_text(item, ["title", "{http://www.w3.org/2005/Atom}title"]))
        summary = strip_html(child_text(item, ["description", "summary", "{http://www.w3.org/2005/Atom}summary"]))
        link = child_text(item, ["link", "guid", "{http://www.w3.org/2005/Atom}id"])
        atom_link = item.find("{http://www.w3.org/2005/Atom}link")
        if atom_link is not None and atom_link.attrib.get("href"):
            link = atom_link.attrib["href"]
        published = child_text(item, ["pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated", "{http://purl.org/dc/elements/1.1/}date"])

        if title:
            parsed.append(
                {
                    "title": title,
                    "summary": summary,
                    "link": strip_html(link),
                    "published": published,
                    "source": feed["name"],
                    "category": feed.get("category", "general"),
                    "priority": int(feed.get("priority", 3)),
                    "region": feed.get("region", "global"),
                    "sourceTier": feed.get("sourceTier", "secondary"),
                    "articleHtmlAllowed": feed.get("articleHtmlAllowed", True),
                }
            )
    return parsed


def parse_time(value: str) -> float:
    if not value:
        return 0
    for parser in (email.utils.parsedate_to_datetime,
                   lambda v: dt.datetime.fromisoformat(v.replace("Z", "+00:00"))):
        try:
            parsed = parser(value)
            # Never silently interpret publisher dates using the host timezone.
            if parsed.tzinfo is not None:
                return parsed.timestamp()
        except (ValueError, TypeError, OverflowError):
            pass
    return 0


class ArticleHTMLParser(HTMLParser):
    """Extract publisher-provided article text without executing page scripts."""

    def __init__(self) -> None:
        super().__init__()
        self.paragraphs: list[str] = []
        self.descriptions: list[str] = []
        self.json_ld: list[str] = []
        self._paragraph: list[str] | None = None
        self._json: list[str] | None = None
        self._ignored_tags: list[str] = []

    _IGNORED_TAGS = {"aside", "button", "footer", "form", "header", "nav", "noscript", "style"}
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    _BOILERPLATE_MARKERS = (
        "advert", "breadcrumb", "cookie", "footer", "header", "menu", "nav", "newsletter",
        "promo", "related", "share", "sidebar", "social", "subscribe",
    )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {key.lower(): (value or "") for key, value in attrs}
        marker_text = f"{values.get('class', '')} {values.get('id', '')}".lower()
        if self._ignored_tags:
            if tag not in self._VOID_TAGS:
                self._ignored_tags.append(tag)
            return
        if tag in self._IGNORED_TAGS or any(marker in marker_text for marker in self._BOILERPLATE_MARKERS):
            if tag not in self._VOID_TAGS:
                self._ignored_tags.append(tag)
            return
        if tag == "p":
            self._paragraph = []
        elif tag == "meta":
            name = (values.get("name") or values.get("property") or "").lower()
            if name in {"description", "og:description", "twitter:description"}:
                content = strip_html(values.get("content", ""))
                if content:
                    self.descriptions.append(content)
        elif tag == "script" and "ld+json" in values.get("type", "").lower():
            self._json = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignored_tags:
            if tag in self._ignored_tags:
                while self._ignored_tags:
                    opened = self._ignored_tags.pop()
                    if opened == tag:
                        break
            return
        if tag == "p" and self._paragraph is not None:
            paragraph = strip_html(" ".join(self._paragraph))
            if len(paragraph) >= 80:
                self.paragraphs.append(paragraph)
            self._paragraph = None
        elif tag.lower() == "script" and self._json is not None:
            payload = "".join(self._json).strip()
            if payload:
                self.json_ld.append(payload)
            self._json = None

    def handle_data(self, data: str) -> None:
        if self._ignored_tags:
            return
        if self._paragraph is not None:
            self._paragraph.append(data)
        if self._json is not None:
            self._json.append(data)


def _json_article_text(value, keys: set[str] | None = None) -> list[str]:
    keys = keys or {"articleBody", "description"}
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and isinstance(child, str):
                cleaned = strip_html(child)
                if len(cleaned) >= 80:
                    found.append(cleaned)
            elif isinstance(child, (dict, list)):
                found.extend(_json_article_text(child, keys))
    elif isinstance(value, list):
        for child in value:
            found.extend(_json_article_text(child, keys))
    return found


def _deduplicate_passages(passages: list[str]) -> list[str]:
    unique = []
    seen = set()
    for passage in passages:
        cleaned = strip_urls(passage)
        key = normalize_text(cleaned)
        if len(cleaned) < 80 or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique


def source_article_text(
    item: dict,
    timeout: int = 12,
    max_bytes: int = 2_000_000,
    min_chars: int = 240,
    max_chars: int = 12_000,
) -> tuple[str, str]:
    """Return text fetched from the article URL, or a fail-closed reason."""
    link = strip_html(item.get("link", ""))
    if not re.match(r"^https?://", link, flags=re.IGNORECASE):
        return "", "missing-article-url"
    request = urllib.request.Request(
        link,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FastNews247/1.0; source-verification)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return "", "article-not-html"
            charset = response.headers.get_content_charset() or "utf-8"
            page = response.read(max_bytes).decode(charset, errors="replace")
    except Exception:
        return "", "article-fetch-failed"

    parser = ArticleHTMLParser()
    try:
        parser.feed(page)
    except Exception:
        return "", "article-parse-failed"

    json_bodies = []
    json_descriptions = []
    for payload in parser.json_ld[:20]:
        try:
            structured = json.loads(payload)
            json_bodies.extend(_json_article_text(structured, {"articleBody"}))
            json_descriptions.extend(_json_article_text(structured, {"description"}))
        except (json.JSONDecodeError, TypeError):
            continue
    # Prefer publisher-declared articleBody. Mixing it with all page paragraphs
    # duplicates facts and can pull unrelated modules into the summary.
    json_body_passages = _deduplicate_passages(json_bodies)
    json_description_passages = _deduplicate_passages(json_descriptions)
    paragraph_passages = _deduplicate_passages(parser.paragraphs)
    description_passages = _deduplicate_passages(parser.descriptions)
    if json_body_passages:
        candidates = json_body_passages
    elif paragraph_passages:
        candidates = paragraph_passages
    else:
        candidates = json_description_passages or description_passages
    article = strip_html(" ".join(candidates))[:max_chars]
    if len(article) < min_chars:
        return "", "article-text-too-thin"

    title_terms = {
        word for word in re.findall(r"[a-z0-9À-ỹ]{4,}", normalize_text(item.get("title", "")))
        if word not in {"that", "this", "with", "from", "what", "những", "trong", "được", "của", "cho"}
    }
    article_terms = set(re.findall(r"[a-z0-9À-ỹ]{4,}", normalize_text(article)))
    if len(title_terms & article_terms) < 2:
        return "", "article-title-mismatch"
    return article, ""


def fingerprint(item: dict) -> str:
    # The source name must not be part of the key: one article can appear in
    # several regional feeds. Include the publication day so recurring generic
    # official headlines can be posted again on a genuinely new day.
    title = normalize_text(item.get("title", ""))
    published_at = parse_time(item.get("published", ""))
    published_day = dt.datetime.fromtimestamp(published_at, dt.timezone.utc).date().isoformat() if published_at > 0 else ""
    if title:
        base = f"{title}|{published_day}"
    else:
        parsed = urllib.parse.urlsplit(item.get("link", ""))
        canonical_link = f"{parsed.netloc.lower().removeprefix('www.')}|{parsed.path.rstrip('/')}"
        base = f"{canonical_link}|{published_day}"
    return hashlib.sha256(normalize_text(base).encode("utf-8")).hexdigest()[:20]


def asset_matches(text: str, config: dict) -> dict[str, int]:
    matches = {}
    for asset, terms in config["assets"].items():
        count = sum(1 for term in terms if term_matches(text, term))
        if count:
            matches[asset] = count
    return matches


def clean_asset_matches(item: dict, matches: dict[str, int]) -> dict[str, int]:
    cleaned = dict(matches)
    primary_text = normalize_text(" ".join([item.get("title", ""), item.get("source", ""), item.get("category", "")]))

    if item.get("category") == "vietnam_market":
        cleaned["vietnamMarket"] = max(1, cleaned.get("vietnamMarket", 0))
        explicit_usd_terms = ["dxy", "fed", "fomc", "us treasury", "u.s. treasury", "dollar index", "chỉ số đô la", "chi so do la"]
        if not any(term_matches(primary_text, term) for term in explicit_usd_terms):
            cleaned.pop("usdRates", None)

    explicit_oil_terms = ["oil", "crude", "brent", "wti", "opec", "eia", "dầu", "dau"]
    if "oil" in cleaned and not any(term_matches(primary_text, term) for term in explicit_oil_terms):
        cleaned.pop("oil", None)

    if item.get("category") == "crypto" and "btc" in cleaned:
        # Crypto RSS summaries sometimes mention macro/energy terms from another story.
        # Keep the market tag focused on the actual crypto headline.
        if not any(term_matches(primary_text, term) for term in explicit_oil_terms):
            cleaned.pop("oil", None)

    return cleaned


def score_item(item: dict, config: dict) -> tuple[int, list[str], str]:
    primary_text = normalize_text(" ".join([item.get("title", ""), item.get("category", "")]))
    text = normalize_text(" ".join([primary_text, item.get("summary", "")]))
    if any(term_matches(text, term) for term in config.get("blockedTerms", [])):
        return 0, [], "blocked-term"

    matches = clean_asset_matches(item, asset_matches(primary_text, config))
    high_terms = [term for term in config.get("highImpactTerms", []) if term_matches(text, term)]

    score = 1
    score += min(2, len(matches))
    if high_terms:
        score += 1
    if item.get("priority", 3) >= 5:
        score += 1
    elif item.get("priority", 3) >= 4 and (matches or high_terms):
        score += 1

    weak_terms = [term for term in config.get("weakContextTerms", []) if term_matches(text, term)]
    if weak_terms:
        score -= 2

    score = max(1, min(5, score))
    if not matches and not high_terms:
        score = 1

    tags = tags_for(matches, primary_text)
    reason = ", ".join(list(matches.keys()) + high_terms[:3]) or "low-impact"
    return score, tags, reason


def tags_for(matches: dict[str, int], text: str) -> list[str]:
    mapping = {
        "gold": "#XAUUSD",
        "oil": "#OIL",
        "btc": "#BTC",
        "usdRates": "#DXY",
        "vietnamMarket": "#VNINDEX",
        "warGeo": "#WAR",
        "forexFx": "#FOREX",
    }
    tags = [mapping[key] for key in matches if key in mapping]
    if "fed" in text or "fomc" in text:
        tags.append("#FED")
    if "cpi" in text:
        tags.append("#CPI")
    if "forex" in text or "fx" in text:
        tags.append("#FOREX")
    currency_tags = {
        "eur": "#EUR",
        "euro": "#EUR",
        "gbp": "#GBP",
        "pound": "#GBP",
        "jpy": "#JPY",
        "yen": "#JPY",
        "cny": "#CNY",
        "yuan": "#CNY",
        "aud": "#AUD",
        "cad": "#CAD",
        "chf": "#CHF",
        "nzd": "#NZD",
    }
    for marker, tag in currency_tags.items():
        if term_matches(text, marker):
            tags.append(tag)
    return list(dict.fromkeys(tags))[:5]


def market_flags(item: dict, tags: list[str]) -> tuple[str, str]:
    # Geography must come from the event itself, never from the publisher name.
    text = normalize_text(" ".join([item.get("title", ""), item.get("summary", "")[:400]]))
    profiles = [
        ("🇻🇳", "Việt Nam", ["vietnam", "vn-index", "vnindex", "hose", "hnx", "usd/vnd", "vnd", "việt nam", "viet nam"]),
        ("🇺🇸", "Mỹ", ["united states", "u.s.", "hoa kỳ", "mỹ", "fed", "fomc", "powell", "treasury", "sec", "nfp", "nonfarm", "dxy", "bls", "federal reserve"]),
        ("🇪🇺", "Châu Âu", ["euro", "eur", "ecb", "lagarde", "eurozone", "europe"]),
        ("🇬🇧", "Anh", ["uk", "britain", "british", "boe", "pound", "gbp", "sterling", "bank of england"]),
        ("🇯🇵", "Nhật Bản", ["japan", "boj", "yen", "jpy", "nhật bản"]),
        ("🇨🇳", "Trung Quốc", ["china", "chinese", "pboc", "yuan", "cny", "trung quốc"]),
        ("🇭🇰", "Hồng Kông", ["hong kong", "hang seng", "hkd"]),
        ("🇰🇷", "Hàn Quốc", ["south korea", "korea", "bok", "won", "krw"]),
        ("🇮🇳", "Ấn Độ", ["india", "rbi", "rupee", "inr", "mumbai"]),
        ("🇸🇬", "Singapore", ["singapore", "mas", "sgd"]),
        ("🇮🇩", "Indonesia", ["indonesia", "bank indonesia", "rupiah", "idr"]),
        ("🇹🇭", "Thái Lan", ["thailand", "bank of thailand", "baht", "thb"]),
        ("🇲🇾", "Malaysia", ["malaysia", "bank negara", "ringgit", "myr"]),
        ("🇵🇭", "Philippines", ["philippines", "bangko sentral", "peso", "php"]),
        ("🇦🇺", "Úc", ["australia", "rba", "aussie", "aud"]),
        ("🇨🇦", "Canada", ["canada", "boc", "cad"]),
        ("🇨🇭", "Thụy Sĩ", ["swiss", "switzerland", "snb", "chf"]),
        ("🇳🇿", "New Zealand", ["new zealand", "rbnz", "kiwi", "nzd"]),
        ("🇩🇪", "Đức", ["germany", "german", "bundesbank", "berlin"]),
        ("🇫🇷", "Pháp", ["france", "french", "banque de france", "paris"]),
        ("🇮🇹", "Italy", ["italy", "italian", "rome"]),
        ("🇪🇸", "Tây Ban Nha", ["spain", "spanish", "madrid"]),
        ("🇸🇦", "Saudi / dầu", ["saudi", "riyadh"]),
        ("🇦🇪", "UAE", ["united arab emirates", "uae", "abu dhabi", "dubai"]),
        ("🇹🇷", "Thổ Nhĩ Kỳ", ["turkey", "turkiye", "lira", "try"]),
        ("🇿🇦", "Nam Phi", ["south africa", "sarb", "rand", "zar"]),
        ("🇳🇬", "Nigeria", ["nigeria", "naira", "cbn"]),
        ("🇧🇷", "Brazil", ["brazil", "brazilian", "brl"]),
        ("🇲🇽", "Mexico", ["mexico", "banxico", "mexican peso", "mxn"]),
        ("🇦🇷", "Argentina", ["argentina", "argentine", "buenos aires"]),
        ("🇷🇺", "Nga", ["russia"]),
        ("🇺🇦", "Ukraine", ["ukraine"]),
        ("🇮🇱", "Israel", ["israel"]),
        ("🇮🇷", "Iran", ["iran"]),
        ("🌊", "Biển Đỏ", ["red sea"]),
    ]
    found = []
    for flag, label, terms in profiles:
        if any(term_matches(text, term.strip()) for term in terms):
            found.append((flag, label))
    if not found and item.get("category") == "vietnam_market" and any(term in text for term in ("ngân hàng", "tiền gửi", "đồng", "chứng khoán việt nam")):
        found.append(("🇻🇳", "Việt Nam"))
    if not found:
        found.append(("🌐", "Quốc tế"))
    flags = " ".join(dict.fromkeys(flag for flag, _ in found[:2]))
    label = " / ".join(dict.fromkeys(label for _, label in found[:2]))
    return flags, label


def local_time_label(item: dict) -> str:
    local_dt = dt.datetime.now(VIETNAM_TZ)
    return local_dt.strftime("%H:%M")


def display_hashtags(tags: list[str]) -> str:
    base_tags = ["#news", "#Tinnhanh247"]
    market_tags = [tag for tag in tags if tag not in base_tags]
    return " ".join(list(dict.fromkeys(base_tags + market_tags))[:6])


def translate_text_to_vi(text: str) -> str:
    text = strip_html(text)
    if not text or looks_vietnamese(text):
        return text
    query = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "vi", "dt": "t", "q": text})
    url = f"https://translate.googleapis.com/translate_a/single?{query}"
    try:
        raw = fetch_url(url, timeout=6).decode("utf-8", errors="replace")
        payload = json.loads(raw)
        translated = "".join(part[0] for part in payload[0] if part and part[0]).strip()
        return translated or text
    except Exception:
        return ""


def split_complete_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", strip_urls(text)).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ỹ0-9])", text)
    return [part.strip() for part in parts if len(part.split()) >= 7 and re.search(r'[.!?][”"’]?$', part) and "..." not in part and "…" not in part]


def _fact_numbers(text: str) -> set[str]:
    values = {value.replace(",", ".") for value in re.findall(r"\d+(?:[.,]\d+)?", text)}
    lower = text.lower()
    month_names = {
        "january": "1", "february": "2", "march": "3", "april": "4", "may": "5", "june": "6",
        "july": "7", "august": "8", "september": "9", "october": "10", "november": "11", "december": "12",
    }
    values.update(number for name, number in month_names.items() if term_matches(lower, name))
    return values


def _event_is_explicit(text: str) -> bool:
    normalized = normalize_text(text)
    event_markers = (
        "announce", "approve", "ban", "buy", "cut", "decide", "drop", "fall", "hold",
        "increase", "invest", "launch", "raise", "release", "report", "rise", "said", "sell", "sign", "strike",
        "công bố", "cho biết", "quyết định", "tăng", "giảm", "giữ", "giữ nguyên", "vượt", "rơi",
        "đạt", "mất", "mua", "bán", "phê duyệt", "ký", "áp", "dỡ", "cắt", "nâng", "hạ",
        "tấn công", "đàm phán", "ra mắt", "thông qua", "cảnh báo", "phát hành", "đầu tư",
    )
    return any(term_matches(normalized, marker) for marker in event_markers)


def source_summary_sentences(item: dict, limit: int = 2) -> list[str]:
    """Select complete, source-grounded fact sentences before translation."""
    article = item.get("article_text", "")
    title_terms = {
        term for term in re.findall(r"[a-z0-9À-ỹ]{4,}", normalize_text(item.get("title", "")))
        if term not in {"that", "this", "with", "from", "what", "những", "trong", "được", "của", "cho"}
    }
    ranked = []
    for index, sentence in enumerate(split_complete_sentences(article)[:30]):
        normalized = normalize_text(sentence)
        overlap = len(title_terms & set(re.findall(r"[a-z0-9À-ỹ]{4,}", normalized)))
        has_number = bool(_fact_numbers(sentence))
        has_event = _event_is_explicit(sentence)
        is_context_sentence = index <= 2 and has_number and has_event
        if (overlap < 2 and not is_context_sentence) or not (has_number or has_event):
            continue
        ranked.append((overlap + int(has_number) + int(has_event), -index, sentence))
    ranked.sort(reverse=True)
    selected = sorted(ranked[:limit], key=lambda row: -row[1])
    sentences = [row[2] for row in selected]
    # Prefer the attributed, more specific sentence over a repeated lead fact.
    if len(sentences) == 2:
        a, b = sentences
        if _fact_numbers(a) and _fact_numbers(a).issubset(_fact_numbers(b)) and b.lower().startswith(("theo ", "according to ")):
            sentences = [b]
    return sentences


def vietnamese_editorial(item: dict) -> tuple[str, str]:
    """Build a Vietnamese event title and a 1-2 sentence factual summary."""
    source_title = strip_urls(item.get("title", ""))
    title = translate_text_to_vi(source_title)
    if not title or not looks_vietnamese(title):
        return "", ""
    if "..." in title or "…" in title:
        return "", ""
    if not _fact_numbers(title).issubset(_fact_numbers(source_title)):
        return "", ""
    title = strip_urls(title).rstrip(" .")
    item["source_sentences"] = source_summary_sentences(item)

    source_sentences = source_summary_sentences(item)
    if not source_sentences:
        return "", ""
    translated = []
    for source_sentence in source_sentences:
        sentence = translate_text_to_vi(source_sentence)
        if not sentence or not looks_vietnamese(sentence):
            return "", ""
        # Translation may change punctuation/formatting but must not introduce
        # numerical claims absent from the exact source sentence.
        if _fact_numbers(sentence) != _fact_numbers(source_sentence):
            return "", ""
        if any(term in sentence.lower() for term in ("giao dịch chị em", "trao đổi chị em")):
            return "", ""
        # Fail closed if translation drops named acronyms or source attribution.
        acronyms = set(re.findall(r"\b[A-Z]{2,8}\b", source_sentence)) - {"US", "UK", "EU", "THE"}
        if any(entity not in sentence for entity in acronyms):
            return "", ""
        if re.search(r"\b(said|says|according to)\b", source_sentence, re.I) and not any(term in sentence.lower() for term in ("cho biết", "nói", "theo ")):
            return "", ""
        sentence = strip_urls(sentence).strip()
        if sentence[-1:] not in ".!?":
            sentence += "."
        translated.append(sentence)
    if len(translated) == 2:
        title_terms = _meaningful_terms(title)
        first_new_terms = _meaningful_terms(translated[0]) - title_terms
        second_new_terms = _meaningful_terms(translated[1]) - title_terms
        first_new_numbers = _fact_numbers(translated[0]) - _fact_numbers(title)
        second_new_numbers = _fact_numbers(translated[1]) - _fact_numbers(title)
        if (len(first_new_terms) < 4 and not first_new_numbers) and (len(second_new_terms) >= 4 or second_new_numbers):
            translated = [translated[1]]
    return title, " ".join(translated)


def vietnamese_headline(item: dict, tags: list[str], category: str, market_label: str) -> str:
    # Compatibility helper used by existing checks/callers.
    return vietnamese_editorial(item)[0]


def freshness_issue(item: dict, config: dict) -> str:
    published_at = parse_time(item.get("published", ""))
    posting = config.get("posting", {})
    if published_at <= 0:
        return "missing-published-time" if posting.get("requirePublishedTime", True) else ""
    age_minutes = (time.time() - published_at) / 60
    if age_minutes > posting.get("maximumAgeMinutes", 240):
        return "stale-item"
    if age_minutes < -posting.get("futureToleranceMinutes", 15):
        return "invalid-future-time"
    return ""


def headline_quality_issues(headline: str, item: dict) -> list[str]:
    normalized = normalize_text(headline)
    issues = []
    if not strip_urls(item.get("source", "")):
        issues.append("missing-source")
    if not item.get("source_article_verified"):
        issues.append("source-article-unverified")
    if not headline:
        issues.append("translation-or-headline-missing")
        return issues
    if URL_PATTERN.search(headline):
        issues.append("url-in-public-copy")
    if not looks_vietnamese(headline):
        issues.append("not-vietnamese")
    if len(headline) < 45 or len(headline.split()) < 7:
        issues.append("headline-too-thin")
    if "..." in headline or "…" in headline or re.search(r"[,;:\-–—]\s*$", headline):
        issues.append("incomplete-headline")
    if "?" in headline:
        issues.append("question-or-teaser-headline")
    if any(phrase in normalized for phrase in GENERIC_HEADLINE_PHRASES):
        issues.append("generic-headline")
    advice_patterns = (
        r"\b(?:buy|sell)\s+now\b", r"\b(?:go\s+)?(?:long|short)\b",
        r"\b(?:stop[- ]?loss|take[- ]?profit)\b", r"\bnên\s+(?:mua|bán)\b",
        r"\b(?:mua|bán)\s+ngay\b", r"\bchốt\s+lời\b", r"\bcắt\s+lỗ\b",
    )
    if any(re.search(pattern, normalized) for pattern in advice_patterns):
        issues.append("trading-advice")
    event_markers = (
        "công bố", "cho biết", "quyết định", "tăng", "giảm", "giữ nguyên", "vượt", "rơi",
        "đạt", "mất", "mua", "bán", "phê duyệt", "bác bỏ", "ký", "áp", "dỡ", "cắt",
        "nâng", "hạ", "đình chỉ", "tấn công", "đàm phán", "ra mắt", "thông qua", "cảnh báo",
        "bổ nhiệm", "phát hành", "đầu tư", "rót", "thu hồi", "đóng cửa", "mở cửa", "ở mức",
    )
    quantitative_fact = re.search(
        r"\d+(?:[.,]\d+)?\s*(?:%|điểm cơ bản|bps|điểm|tỷ|triệu|nghìn|usd|vnd|đồng|"
        r"thùng|ounce|tấn|btc|eth)\b",
        normalized,
    )
    if not quantitative_fact and not any(marker in normalized for marker in event_markers):
        issues.append("main-event-not-explicit")
    return issues


def _meaningful_terms(text: str) -> set[str]:
    stop_words = {
        "the", "and", "that", "this", "with", "from", "after", "before",
        "của", "và", "trong", "với", "sau", "trước", "được", "cho", "biết",
        "tại", "một", "những", "các", "mức", "tháng", "năm",
    }
    return {
        term for term in re.findall(r"[a-z0-9À-ỹ]{3,}", normalize_text(text))
        if term not in stop_words
    }


def summary_quality_issues(summary: str, item: dict, title: str = "") -> list[str]:
    issues = []
    if "..." in summary or "…" in summary or not re.search(r'[.!?][”"’]?$', summary):
        issues.append("incomplete-summary")
    sentences = split_complete_sentences(summary)
    if not summary or not (1 <= len(sentences) <= 2):
        issues.append("summary-must-have-1-or-2-complete-sentences")
        return issues
    if URL_PATTERN.search(summary):
        issues.append("url-in-summary")
    if not looks_vietnamese(summary):
        issues.append("summary-not-vietnamese")
    if any(pattern in normalize_text(summary) for pattern in ("nên mua", "nên bán", "mua ngay", "bán ngay", "chốt lời", "cắt lỗ")):
        issues.append("trading-advice")
    source_numbers = _fact_numbers(item.get("article_text", ""))
    if not _fact_numbers(summary).issubset(source_numbers):
        issues.append("summary-number-not-in-source")
    for sentence in sentences:
        if len(sentence.split()) < 8 or not _event_is_explicit(sentence):
            issues.append("summary-lacks-subject-action-context")
            break
    if title:
        title_terms = _meaningful_terms(title)
        summary_terms = _meaningful_terms(summary)
        new_terms = summary_terms - title_terms
        new_numbers = _fact_numbers(summary) - _fact_numbers(title)
        if len(new_terms) < 4 and not new_numbers:
            issues.append("summary-adds-no-new-information")
    return issues


def topic_label(tags: list[str], category: str) -> str:
    topics = []
    mapping = {
        "#XAUUSD": "Vàng", "#OIL": "Dầu", "#BTC": "Crypto", "#FOREX": "Ngoại hối",
        "#DXY": "USD và lãi suất", "#FED": "Fed", "#CPI": "Lạm phát", "#VNINDEX": "Chứng khoán Việt Nam",
        "#WAR": "Địa chính trị", "#NGANHANG": "Ngân hàng / Lãi suất",
    }
    for tag in tags:
        if tag in mapping:
            topics.append(mapping[tag])
    if not topics:
        topics.append({"official_macro": "Vĩ mô", "world_macro": "Kinh tế thế giới", "war_geo": "Địa chính trị"}.get(category, "Thị trường"))
    return " / ".join(dict.fromkeys(topics))


def watch_assets(tags: list[str], category: str) -> str:
    assets = []
    if "#XAUUSD" in tags:
        assets.extend(["XAUUSD", "DXY"])
    if "#OIL" in tags:
        assets.extend(["Brent", "WTI"])
    if "#BTC" in tags:
        assets.extend(["BTC", "ETH"])
    if "#FOREX" in tags:
        assets.extend(["DXY", "EUR/USD", "USD/JPY"])
    currency_pairs = {
        "#EUR": ["EUR/USD", "EUR/JPY"],
        "#GBP": ["GBP/USD", "GBP/JPY"],
        "#JPY": ["USD/JPY", "EUR/JPY"],
        "#CNY": ["USD/CNH", "AUD/USD"],
        "#AUD": ["AUD/USD", "AUD/JPY"],
        "#CAD": ["USD/CAD", "WTI"],
        "#CHF": ["USD/CHF", "EUR/CHF"],
        "#NZD": ["NZD/USD"],
    }
    for tag, related_assets in currency_pairs.items():
        if tag in tags:
            assets.extend(related_assets)
    if "#DXY" in tags or "#FED" in tags or "#CPI" in tags:
        assets.extend(["USD", "DXY", "US yields"])
    if "#VNINDEX" in tags or category == "vietnam_market":
        assets.extend(["VN-Index", "USD/VND", "ngan hang", "BDS"])
    if not assets:
        assets = ["XAUUSD", "BTC", "USD"]
    return ", ".join(list(dict.fromkeys(assets))[:5])


def impact_line(tags: list[str], category: str) -> str:
    if "#XAUUSD" in tags and ("#DXY" in tags or "#FED" in tags or "#CPI" in tags):
        return "USD, DXY và vàng có thể biến động mạnh khi thị trường định giá lại lãi suất và rủi ro vĩ mô."
    if "#FOREX" in tags and ("#DXY" in tags or "#FED" in tags or "#CPI" in tags):
        return "Forex, USD và lợi suất trái phiếu có thể biến động mạnh; trader nên chú ý DXY và các cặp chính."
    if "#FOREX" in tags:
        return "Các cặp tiền chính có thể phản ứng theo kỳ vọng lãi suất, dòng tiền USD và tâm lý rủi ro."
    if any(tag in tags for tag in ["#EUR", "#GBP", "#JPY", "#CNY", "#AUD", "#CAD", "#CHF", "#NZD"]):
        return "Biến động tiền tệ có thể lan sang DXY, vàng và các cặp chéo liên quan trong phiên tới."
    if "#OIL" in tags or "#WAR" in tags:
        return "Dầu, vàng và tâm lý rủi ro có thể nhạy cảm nếu thông tin tiếp tục leo thang."
    if "#BTC" in tags:
        return "BTC và nhóm crypto có thể biến động theo dòng tiền và tâm lý rủi ro ngắn hạn."
    if "#VNINDEX" in tags or category == "vietnam_market":
        return "Dòng tiền trên thị trường Việt Nam có thể phản ứng với nhóm cổ phiếu liên quan và biến động tỷ giá/lãi suất."
    return "Thị trường cần theo dõi phản ứng của USD, vàng, dầu, BTC và các tài sản rủi ro."


def shorten_title(title: str, limit: int = 320) -> str:
    """Keep only complete sentences; never publish a headline cut with ellipsis."""
    title = re.sub(r"\s+", " ", strip_html(title)).strip()
    if len(title) <= limit:
        return title

    sentences = re.split(r"(?<=[.!?])\s+", title)
    kept = []
    length = 0
    for sentence in sentences:
        sentence = strip_urls(sentence).strip()
        if not sentence:
            continue
        next_length = length + (1 if kept else 0) + len(sentence)
        if next_length > limit:
            break
        kept.append(sentence)
        length = next_length
    return " ".join(kept).strip()


def draft_post(item: dict, score: int, tags: list[str]) -> tuple[str, list[str]]:
    category = item.get("category", "")
    banking = category == "vietnam_market" and any(term_matches(normalize_text(item.get("title", "")), marker) for marker in ("ngân hàng", "lãi suất", "tiền gửi"))
    if banking and not any(term_matches(normalize_text(item.get("title", "")), marker) for marker in ("cổ phiếu", "vn-index", "chứng khoán")):
        tags = [tag for tag in tags if tag != "#VNINDEX"] + ["#NGANHANG"]
    flags, market_label = market_flags(item, tags)
    title, summary = vietnamese_editorial(item)
    title = shorten_title(title, limit=220)
    summary = shorten_title(summary, limit=520)
    issues = headline_quality_issues(title, item)
    issues.extend(summary_quality_issues(summary, item, title))
    if issues:
        return "", issues
    label = f"🚨 Tin nhanh 247 | {flags} {market_label}"
    heat_icon = "🔥" if score >= 5 else "⚡️" if score == 4 else "👀"
    hot_stars = "⭐️" * max(1, min(5, int(score)))
    lines = [
        label,
        f"⏰ {local_time_label(item)} | 🔥 Độ HOT: {hot_stars}",
        "",
        f"🔹 {title}",
        f"📝 {summary}",
        "",
        f"🧾 Nguồn: {item.get('source', 'Nguồn tin')}",
        display_hashtags(tags),
    ]
    post = "\n".join(line for line in lines if line is not None).strip()
    if URL_PATTERN.search(post):
        return "", ["url-in-public-copy"]
    return post, []


def looks_vietnamese(text: str) -> bool:
    vietnamese_markers = "ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
    lower = text.lower()
    if any(char in lower for char in vietnamese_markers):
        return True
    common_words = [" và ", " của ", " trong ", " với ", " tại ", " tăng ", " giảm ", " lãi suất ", " thị trường ", " cho biết ", " công bố "]
    return any(word in f" {lower} " for word in common_words)


def telegram_post(config: dict, text: str) -> None:
    tg = config["posting"].get("telegram", {})
    if not tg.get("enabled"):
        return
    if config["posting"].get("requireVietnameseBeforePosting", True) and "Cần OpenClaw viết lại" in text:
        raise RuntimeError("Refusing to post: draft still needs Vietnamese editorial rewrite.")
    if URL_PATTERN.search(text):
        raise RuntimeError("Refusing to post: public copy contains a URL.")
    if re.search(r"\bICT\b", text, flags=re.IGNORECASE):
        raise RuntimeError("Refusing to post: public time must not include ICT.")
    mode = tg.get("mode", "bridge")
    if mode == "bridge":
        return telegram_bridge_post(tg, text)
    if mode == "direct":
        return telegram_direct_post(tg, text)
    raise RuntimeError(f"Unsupported Telegram transport {mode!r}; use 'bridge' or 'direct'.")


def telegram_bridge_post(tg: dict, text: str) -> dict:
    channel_id = tg.get("channelId", "")
    if not channel_id:
        raise RuntimeError("Missing Telegram channelId")
    cli = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/openclaw/openclaw.mjs"
    command = ["node", str(cli), "message", "send", "--channel=telegram",
               f"--target={channel_id}", f"--message={text}", "--json"]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60)
    except subprocess.TimeoutExpired:
        return {"status": "pending", "reason": "bridge-timeout"}
    # Logs, exit status and submission are NOT an API acknowledgement.
    decoder = json.JSONDecoder()
    for index, char in enumerate(result.stdout):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(result.stdout[index:])
        except ValueError:
            continue
        ack = extract_ack(payload)
        if ack:
            return {"status": "confirmed", "reason": "telegram-message-ack", **ack}
    return {"status": "pending", "reason": "bridge-no-message-ack",
            "exitCode": result.returncode}


def _redact_token(text: str, token: str) -> str:
    """Telegram puts the bot token in the URL, so it leaks into error strings."""
    return text.replace(token, "<token>") if token else text


def telegram_direct_post(tg: dict, text: str) -> dict:
    """Post straight to the Telegram Bot API.

    Used on hosts that do not run OpenClaw (a Linux VPS). The bridge remains the
    default; this path reads the token from the environment name declared by
    tokenEnv and never writes it to logs.
    """
    token_env = tg.get("tokenEnv", "")
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise RuntimeError(f"Missing Telegram bot token in ${token_env}.")
    channel_id = (os.environ.get(tg.get("channelEnv", ""), "") or tg.get("channelId", "")).strip()
    if not channel_id:
        raise RuntimeError("Missing Telegram channelId.")

    body = json.dumps({
        "chat_id": channel_id,
        "text": text,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        return {"status": "pending", "reason": "telegram-http-error",
                "httpStatus": exc.code, "detail": _redact_token(detail, token)}
    except Exception as exc:
        return {"status": "pending", "reason": "telegram-request-failed",
                "detail": _redact_token(str(exc), token)}

    ack = extract_ack(payload)
    if ack:
        return {"status": "confirmed", "reason": "telegram-message-ack", **ack}
    return {"status": "pending", "reason": "telegram-no-message-ack"}


def extract_ack(payload):
    if not isinstance(payload, dict) or payload.get("ok") is False:
        return None
    message_id = payload.get("messageId", payload.get("message_id"))
    chat_id = payload.get("chatId") or (payload.get("chat") or {}).get("id")
    if str(message_id or "").isdigit() and int(message_id) > 0 and chat_id:
        return {"messageId": int(message_id), "chatId": str(chat_id)}
    for key in ("result", "payload", "data"):
        ack = extract_ack(payload.get(key))
        if ack:
            return ack
    return None


def assert_posting_ready(config: dict) -> None:
    tg = config["posting"].get("telegram", {})
    mode = tg.get("mode", "bridge")
    channel_env = tg.get("channelEnv", "")
    has_channel = bool(os.environ.get(channel_env) or tg.get("channelId", ""))
    if mode == "bridge":
        if not has_channel:
            raise RuntimeError("Missing Telegram bridge channelId.")
        return
    if mode == "direct":
        token_env = tg.get("tokenEnv", "")
        if not token_env:
            raise RuntimeError("Direct transport needs posting.telegram.tokenEnv.")
        if not os.environ.get(token_env, "").strip():
            raise RuntimeError(f"Missing Telegram bot token in ${token_env}.")
        if not has_channel:
            raise RuntimeError("Missing Telegram channelId.")
        return
    raise RuntimeError(f"Unsupported credential transport {mode!r}; use 'bridge' or 'direct'.")


def test_telegram(config: dict, message: str) -> int:
    config = json.loads(json.dumps(config))
    config["posting"]["telegram"]["enabled"] = True
    assert_posting_ready(config)
    result = telegram_post(config, message)
    print(json.dumps(result))
    return 0 if result and result.get("status") == "confirmed" else 2


def prune_state(state: dict, duplicate_window_hours: int) -> dict:
    cutoff = time.time() - duplicate_window_hours * 3600
    seen = {key: {**value, "status": value.get("status", "legacy-unconfirmed")} for key, value in state.get("seen", {}).items() if value.get("time", 0) >= cutoff or value.get("status", "legacy-unconfirmed") != "confirmed"}
    return {**state, "schemaVersion": 2, "seen": seen}


def run_once(config: dict, post: bool = False) -> int:
    run_started = time.time()
    config = json.loads(json.dumps(config))
    config["posting"]["telegram"]["enabled"] = bool(post)
    if post:
        assert_posting_ready(config)

    state_path = ROOT / config["channel"]["stateDb"]
    output_path = ROOT / config["channel"]["draftOutput"]
    state = prune_state(load_json(state_path, {"seen": {}}), config["posting"]["duplicateWindowHours"])

    candidates = []
    errors = []
    rejected = []
    feed_audit = []
    seen_titles = {
        normalize_text(value.get("title", ""))
        for value in state.get("seen", {}).values()
        if value.get("title")
    }
    feed_workers = max(2, min(int(config.get("fetching", {}).get("feedWorkers", 8)), 12))
    with concurrent.futures.ThreadPoolExecutor(max_workers=feed_workers) as executor:
        future_to_feed = {executor.submit(parse_feed, feed): feed for feed in config["feeds"]}
        for future in concurrent.futures.as_completed(future_to_feed):
            feed = future_to_feed[future]
            try:
                feed_items = future.result()
            except Exception as exc:
                errors.append(f"{feed['name']}: {type(exc).__name__}")
                continue
            dates = [parse_time(i["published"]) for i in feed_items]
            feed_audit.append({"source": feed["name"], "items": len(feed_items),
                               "latestPublished": max(dates, default=0),
                               "fresh": sum(not freshness_issue(i, config) for i in feed_items),
                               "http": FETCH_AUDIT.get(feed["url"], {})})
            for item in feed_items:
                item["fingerprint"] = fingerprint(item)
                normalized_title = normalize_text(item.get("title", ""))
                if item["fingerprint"] in state["seen"] or normalized_title in seen_titles:
                    continue
                freshness = freshness_issue(item, config)
                if freshness:
                    rejected.append(f"{feed['name']}: {freshness} :: {item['title'][:120]}")
                    continue
                score, tags, reason = score_item(item, config)
                if score >= config["posting"]["minimumScoreToDraft"]:
                    item.update({"score": score, "tags": tags, "reason": reason})
                    candidates.append(item)

    candidates.sort(key=lambda item: (item["score"], parse_time(item.get("published", ""))), reverse=True)
    if post:
        candidates = [item for item in candidates if item["score"] >= config["posting"]["minimumScoreToPost"]]
    selected = []
    article_checks = 0
    max_article_checks = int(config["posting"].get("maxArticleChecksPerRun", 8))
    for item in candidates:
        if any(same_event(item, previous) for previous in list(state["seen"].values()) + selected):
            rejected.append(f"{item['source']}: duplicate-event :: {item['title']}")
            continue
        if config["posting"].get("requireSourceArticle", True):
            if article_checks >= max_article_checks:
                rejected.append(f"{item['source']}: article-check-budget-exhausted :: {item['title'][:120]}")
                continue
            article_checks += 1
            article_config = config.get("articleContent", {})
            if not article_config.get("preferHtml", True) or item.get("articleHtmlAllowed") is False:
                rejected.append(f"{item['source']}: article-html-not-allowed :: {item['title'][:120]}")
                continue
            article_text, article_issue = source_article_text(
                item,
                timeout=int(article_config.get("timeoutSeconds", 12)),
                max_bytes=int(article_config.get("maxBytes", 2_000_000)),
                min_chars=int(article_config.get("minimumTextChars", 240)),
                max_chars=int(article_config.get("maximumTextChars", 12_000)),
            )
            if article_issue:
                rejected.append(f"{item['source']}: {article_issue} :: {item['title'][:120]}")
                continue
            item["article_text"] = article_text
            item["source_article_verified"] = True
        else:
            item["article_text"] = item.get("summary", "")
            item["source_article_verified"] = True
        draft, quality_issues = draft_post(item, item["score"], item["tags"])
        if quality_issues:
            rejected.append(f"{item['source']}: {','.join(quality_issues)} :: {item['title'][:120]}")
            continue
        item["draft"] = draft
        selected.append(item)
        if len(selected) >= config["posting"]["maxPostsPerRun"]:
            break

    now = dt.datetime.now(VIETNAM_TZ).strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"# Tin nhanh 247 Drafts", "", f"Generated: {now}", ""]
    if errors:
        lines.extend(["## Feed Warnings", ""])
        lines.extend([f"- {error}" for error in errors])
        lines.append("")
    if rejected:
        lines.extend(["## Quality Gate Rejections", ""])
        lines.extend([f"- {reason}" for reason in rejected[:30]])
        lines.append("")

    if not selected:
        lines.extend(["No publish-worthy items found in this run.", ""])
    for index, item in enumerate(selected, start=1):
        draft = item["draft"]
        lines.extend([f"## Draft {index}", "", draft, "", f"Reason: {item['reason']}", ""])
        if post and item["score"] >= config["posting"]["minimumScoreToPost"]:
            if config["posting"].get("requireVietnameseBeforePosting", True) and "Cần OpenClaw viết lại" in draft:
                print(f"Skipped live post needing Vietnamese rewrite: {item['title']}")
            else:
                print(f"Posting live: {item['source']} - {item['title']}", flush=True)
                record = {"time": time.time(), "title": item["title"], "source": item["source"],
                          "score": item["score"], "link": item.get("link", ""),
                          "status": "pending", "reason": "write-ahead-send-intent"}
                state["seen"][item["fingerprint"]] = record
                save_json(state_path, state)
                try:
                    delivery = telegram_post(config, draft)
                    record.update(delivery or {"status": "pending", "reason": "missing-ack"})
                except Exception:
                    record.update(status="pending", reason="publisher-exception-uncertain")
                save_json(state_path, state)
                print("Delivery: " + json.dumps(record, ensure_ascii=False), flush=True)

    save_json(ROOT / "outputs/fastnews247/last_run.json",
              {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "post": post,
               "feeds": feed_audit, "errors": errors, "rejections": rejected,
               "selected": selected,
               "deliveries": [v for v in state["seen"].values() if v.get("time", 0) >= run_started]})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    save_json(state_path, state)
    print(f"Drafts: {len(selected)} -> {output_path}")
    if errors:
        print(f"Feed warnings: {len(errors)}")
    return 2 if post and any(v.get("status") == "pending" and v.get("time", 0) >= run_started for v in state["seen"].values()) else (1 if len(errors) == len(config["feeds"]) else 0)


def canonical_url(url):
    parsed = urllib.parse.urlsplit(url)
    return parsed.netloc.lower().removeprefix("www.") + parsed.path.rstrip("/")


def same_event(first, second):
    if first.get("link") and second.get("link") and canonical_url(first["link"]) == canonical_url(second["link"]):
        return True
    a, b = _meaningful_terms(first.get("title", "")), _meaningful_terms(second.get("title", ""))
    return bool(a and b and (a == b or
                (len(a & b) >= 4 and len(a & b) / min(len(a), len(b)) >= 0.8
                 and _fact_numbers(first.get("title", "")) == _fact_numbers(second.get("title", "")))))


@contextmanager
def owned_lock(path):
    # OS byte-range lock: atomic ownership, released by kernel on crash.
    # Never unlink this inode; age-based deletion allows concurrent owners.
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a+b")
    try:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BlockingIOError("busy") from exc
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        stream.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one fetch/draft cycle.")
    parser.add_argument("--post", action="store_true", help="Post selected items to Telegram for this run.")
    parser.add_argument("--test-telegram", metavar="TEXT", help="Send one test message to Telegram and exit.")
    args = parser.parse_args()
    config = load_json(CONFIG_PATH, None)
    if not config:
        print(f"Missing config: {CONFIG_PATH}", file=sys.stderr)
        return 1
    if args.test_telegram:
        return test_telegram(config, args.test_telegram)
    if args.once:
        try:
            with owned_lock(ROOT / "storage/fastnews247/worker.lock"):
                return run_once(config, post=args.post)
        except BlockingIOError:
            print("Skipped: another process owns the news lock.")
            return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
