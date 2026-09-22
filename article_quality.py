"""Deterministic article-page verification before editorial review."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Final
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

import recency
from article_fetch import canonicalize_url, create_http_client, fetch_page, supported_article_url
from config import VERIFY_FETCH_WORKERS
from crawlers.base import Article

_MIN_ARTICLE_CHARS: Final = 600
# 신뢰 도메인은 편집 심사에서 낮은 임계값을 적용받는다. 15개만 있던 기존 목록은
# 실측 후보의 90% 이상이 "미지의 소스"로 분류돼 70점 컷을 받았고, 그 결과
# 모델이 KEEP으로 판정한 62~69점 실무 가이드가 전량 폐기됐다.
_TRUSTED_DOMAINS: Final = {
    # AI 엔지니어링 1차 소스·실무자 블로그
    "anthropic.com",
    "openai.com",
    "deepmind.google",
    "huggingface.co",
    "simonwillison.net",
    "eugeneyan.com",
    "hamel.dev",
    "jxnl.co",
    "lilianweng.github.io",
    "martinfowler.com",
    "blog.langchain.com",
    "langchain.com",
    "llamaindex.ai",
    "wandb.ai",
    "modal.com",
    "fly.io",
    "vercel.com",
    "github.blog",
    "blog.jetbrains.com",
    "stackoverflow.blog",
    "netflixtechblog.com",
    "engineering.fb.com",
    "developers.googleblog.com",
    "aws.amazon.com",
    "microsoft.com",
    "pragmaticengineer.com",
    "newsletter.pragmaticengineer.com",
    "thoughtworks.com",
    "infoq.com",
    "arstechnica.com",
    "news.ycombinator.com",
    # 그래픽스·3D·게임 개발
    "80.lv",
    "gamedeveloper.com",
    "gdconf.com",
    "godotengine.org",
    "unity.com",
    "unrealengine.com",
    "docs.unrealengine.com",
    "blender.org",
    "developer.nvidia.com",
    "developer.blender.org",
    "learnopengl.com",
    "thebookofshaders.com",
    "iquilezles.org",
    "polyhaven.com",
    "sketchfab.com",
    "itch.io",
    # 한국어 실무 플랫폼
    "velog.io",
    "brunch.co.kr",
    "tech.kakao.com",
    "techblog.woowahan.com",
    "engineering.linecorp.com",
    "medium.com",
    "toss.tech",
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


def verify_html_detailed(article: Article, html: str, max_age_days: int) -> tuple[VerifiedArticle | None, str]:
    """Verify one fetched page and report the gate that rejected it."""
    canonical_url = canonicalize_url(article.url)
    if not supported_article_url(canonical_url):
        return None, "unsupported_url"
    soup = BeautifulSoup(html, "html.parser")
    text = _visible_text(soup)
    if len(text) < _MIN_ARTICLE_CHARS:
        return None, "body_too_short"
    language = _language(soup, text)
    if language is None:
        return None, "language_rejected"
    page_date = _metadata_date(soup)
    path_date = _url_date(canonical_url)
    verified_dates = [value for value in (page_date, path_date) if value]
    if any(recency.is_stale(value, max_age_days=max_age_days) for value in verified_dates):
        return None, "stale"
    # 발행일 미상은 폐기하지 않고 빈 문자열로 통과시킨다 — recency 모듈의 정책과 동일하게
    # 랭킹에서 감점(0.85)만 받는다. 이전 구현은 meta 날짜가 없고 신뢰 도메인(15개)도
    # 아니면 즉시 버렸는데, 실측 URL 30개 중 10%가 여기서 탈락했고 그중에는 과거에
    # 정상 게시된 실무자 블로그도 있었다.
    published = page_date or path_date or recency.parse_published_date(article.published_at)
    if published is not None and recency.is_stale(published, max_age_days=max_age_days):
        return None, "stale"
    return (
        VerifiedArticle(
            article=article,
            canonical_url=canonical_url,
            language=language,
            published_at=published.isoformat() if published else "",
            excerpt=text[:5000],
            trusted_source=_trusted_source(canonical_url),
        ),
        "ok",
    )


def verify_html(article: Article, html: str, max_age_days: int) -> VerifiedArticle | None:
    """Verify one fetched page and return trusted metadata when it passes hard gates."""
    return verify_html_detailed(article, html, max_age_days)[0]


def verify_articles(
    articles: list[Article],
    max_age_days: int | Callable[[Article], int],
    report: dict | None = None,
) -> list[VerifiedArticle]:
    """Fetch and verify article pages in parallel, recording per-gate rejection counts.

    `max_age_days`에 함수를 주면 기사마다 다른 신선도 창을 적용한다 — 실무 아티클은
    2주, 그래픽스 학습자료는 4개월처럼 필라별 정책이 다르기 때문이다.
    `report`를 넘기면 시도 수·통과 수와 함께 `reasons`(사유별 건수)가 기록된다.
    순차 fetch는 후보 30개 × 느린 페이지에서 수 분이 걸려 오버페치를 막는 병목이었다.
    """
    if not articles:
        if report is not None:
            report.update({"attempted": 0, "passed": 0, "reasons": {}})
        return []

    window = max_age_days if callable(max_age_days) else (lambda _article: max_age_days)
    reasons: Counter[str] = Counter()
    results: list[tuple[int, VerifiedArticle]] = []

    with create_http_client() as client:

        def work(indexed: tuple[int, Article]) -> tuple[int, VerifiedArticle | None, str]:
            index, article = indexed
            html, reason = fetch_page(client, article.url)
            if html is None:
                return index, None, reason
            candidate, gate = verify_html_detailed(article, html, window(article))
            return index, candidate, gate

        workers = max(1, min(VERIFY_FETCH_WORKERS, len(articles)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for index, candidate, reason in pool.map(work, enumerate(articles)):
                if candidate is not None:
                    results.append((index, candidate))
                else:
                    reasons[reason] += 1

    # 입력 순서를 유지해야 랭킹 이전 단계가 결정론적으로 남는다.
    verified = [candidate for _, candidate in sorted(results, key=lambda item: item[0])]
    if report is not None:
        report["attempted"] = len(articles)
        report["passed"] = len(verified)
        report["reasons"] = dict(reasons.most_common())
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


def _title_bigrams(title: str) -> set[tuple[str, str]]:
    tokens = [token for token in re.findall(r"[a-z0-9가-힣]+", title.lower()) if len(token) > 1]
    return set(pairwise(tokens))


def shares_topic(title: str, other: str, min_overlap: int = 3) -> bool:
    """Return whether two titles cover the same specific subject.

    Jaccard 하나로는 주제 군집을 못 잡는다. 실측(2026-09-23): "GPT-6 Astra"를 다루는
    서로 다른 블로그 글 3건이 하루 브리핑 6건 중 절반을 차지했는데, 문장 구성이
    달라 Jaccard가 0.19에 그쳤다. 고유명사는 붙어 다니므로 **공유 바이그램**이
    있고 토큰 겹침도 충분하면 같은 주제로 본다.
    """
    current, previous = _title_tokens(title), _title_tokens(other)
    if len(current & previous) < min_overlap:
        return False
    return bool(_title_bigrams(title) & _title_bigrams(other))


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
