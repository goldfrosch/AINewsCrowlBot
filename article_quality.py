"""Deterministic article-page verification before editorial review."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Final
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

import recency
from article_fetch import canonicalize_url, create_http_client, fetch_html, supported_article_url
from crawlers.base import Article

_MIN_ARTICLE_CHARS: Final = 600
_TRUSTED_DOMAINS: Final = {
    "80.lv",
    "anthropic.com",
    "arstechnica.com",
    "blog.jetbrains.com",
    "developer.nvidia.com",
    "gamedeveloper.com",
    "gdconf.com",
    "github.blog",
    "godotengine.org",
    "infoq.com",
    "martinfowler.com",
    "openai.com",
    "simonwillison.net",
    "unity.com",
    "unrealengine.com",
}
_DATE_META_KEYS: Final = {
    "article:published_time",
    "citation_date",
    "citation_publication_date",
    "date",
    "datepublished",
    "publish_date",
}
_TITLE_STOPWORDS: Final = {
    "a",
    "ai",
    "and",
    "for",
    "how",
    "in",
    "of",
    "the",
    "to",
    "using",
    "with",
}


@dataclass(frozen=True, slots=True)
class VerifiedArticle:
    """Article metadata backed by a fetched, readable page."""

    article: Article
    canonical_url: str
    language: str
    published_at: str
    excerpt: str
    trusted_source: bool


def _domain_matches(host: str, domains: set[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _visible_text(soup: BeautifulSoup) -> str:
    for node in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        node.decompose()
    container = soup.find("article") or soup.find("main") or soup.body
    return " ".join(container.stripped_strings) if container else ""


def _language(soup: BeautifulSoup, text: str) -> str | None:
    html_tag = soup.find("html")
    declared = str(html_tag.get("lang") or "").lower() if html_tag else ""
    hangul = len(re.findall(r"[가-힣]", text))
    han = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
    kana = len(re.findall(r"[\u3040-\u30ff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if declared.startswith("zh") or (han >= 20 and hangul < 10 and kana < 10):
        return None
    if declared.startswith("ko") or hangul >= 20:
        return "ko"
    if declared.startswith("en") or (latin >= 100 and han < 20 and kana < 10):
        return "en"
    return None


def _metadata_date(soup: BeautifulSoup) -> date | None:
    for meta in soup.find_all("meta"):
        key = str(meta.get("property") or meta.get("name") or "").lower()
        if key in _DATE_META_KEYS:
            parsed = recency.parse_published_date(meta.get("content"))
            if parsed:
                return parsed
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        return recency.parse_published_date(time_tag.get("datetime"))
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        queue = data if isinstance(data, list) else [data]
        for item in queue:
            if isinstance(item, dict):
                parsed = recency.parse_published_date(item.get("datePublished"))
                if parsed:
                    return parsed
    return None


def _url_date(url: str) -> date | None:
    path = urlsplit(url).path
    numeric = re.search(r"/(20\d{2})/(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])(?:/|$)", path)
    if numeric:
        return recency.parse_published_date("-".join(numeric.groups()))
    named = re.search(
        r"/(20\d{2})/(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)/(\d{1,2})(?:/|$)",
        path,
        re.IGNORECASE,
    )
    return recency.parse_published_date(" ".join(reversed(named.groups()))) if named else None


def _trusted_source(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return _domain_matches(host, _TRUSTED_DOMAINS)


def verify_html(article: Article, html: str, max_age_days: int) -> VerifiedArticle | None:
    """Verify one fetched page and return trusted metadata when it passes hard gates."""
    canonical_url = canonicalize_url(article.url)
    if not supported_article_url(canonical_url):
        return None
    soup = BeautifulSoup(html, "html.parser")
    text = _visible_text(soup)
    if len(text) < _MIN_ARTICLE_CHARS:
        return None
    language = _language(soup, text)
    if language is None:
        return None
    page_date = _metadata_date(soup)
    path_date = _url_date(canonical_url)
    verified_dates = [value for value in (page_date, path_date) if value]
    if any(recency.is_stale(value, max_age_days=max_age_days) for value in verified_dates):
        return None
    # 발행일 미상은 폐기하지 않고 빈 문자열로 통과시킨다 — recency 모듈의 정책과 동일하게
    # 랭킹에서 감점(0.85)만 받는다. 이전 구현은 meta 날짜가 없고 신뢰 도메인(15개)도
    # 아니면 즉시 버렸는데, 실측 URL 30개 중 10%가 여기서 탈락했고 그중에는 과거에
    # 정상 게시된 실무자 블로그도 있었다.
    published = page_date or path_date or recency.parse_published_date(article.published_at)
    if published is not None and recency.is_stale(published, max_age_days=max_age_days):
        return None
    return VerifiedArticle(
        article=article,
        canonical_url=canonical_url,
        language=language,
        published_at=published.isoformat() if published else "",
        excerpt=text[:5000],
        trusted_source=_trusted_source(canonical_url),
    )


def verify_articles(articles: list[Article], max_age_days: int, report: dict | None = None) -> list[VerifiedArticle]:
    """Fetch and verify article pages with bounded response sizes and safe redirects.

    `report`를 넘기면 시도 수와 통과 수가 기록된다.
    """
    verified: list[VerifiedArticle] = []
    with create_http_client() as client:
        for article in articles:
            html = fetch_html(client, article.url)
            if html and (candidate := verify_html(article, html, max_age_days)):
                verified.append(candidate)
    if report is not None:
        report["attempted"] = len(articles)
        report["passed"] = len(verified)
    return verified


def _title_tokens(title: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9가-힣]+", title.lower())
        if len(token) > 1 and token not in _TITLE_STOPWORDS
    }


def is_near_duplicate(title: str, previous_titles: list[str], threshold: float = 0.55) -> bool:
    """Return whether a title substantially overlaps a recent title."""
    current = _title_tokens(title)
    if len(current) < 3:
        return False
    for previous_title in previous_titles:
        previous = _title_tokens(previous_title)
        union = current | previous
        if union and len(current & previous) / len(union) >= threshold:
            return True
    return False


def remove_near_duplicates(candidates: list[VerifiedArticle], recent_titles: list[str]) -> list[VerifiedArticle]:
    """Remove topic duplicates against recent posts and within this batch."""
    accepted: list[VerifiedArticle] = []
    compared = list(recent_titles)
    for candidate in candidates:
        if is_near_duplicate(candidate.article.title, compared):
            continue
        accepted.append(candidate)
        compared.append(candidate.article.title)
    return accepted
