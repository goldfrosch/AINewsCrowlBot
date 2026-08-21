"""
RSS 후보 수집

config.RSS_FEEDS / RSS_NO_FILTER_SOURCES / AI_KEYWORDS 는 정의만 되어 있고
어떤 코드도 참조하지 않는 죽은 상수였다(크롤러 폴백은 실재하지 않았음).
이 모듈이 그 상수를 실제 수집 경로로 되살린다.

RSS는 `published_parsed`로 발행일이 확정되므로 Claude 웹 검색과 달리
신선도를 신뢰할 수 있다.
"""

import html
import re
from calendar import timegm
from datetime import UTC, datetime

from config import (
    AI_KEYWORDS,
    RECENCY_MAX_AGE_DAYS,
    RSS_FEEDS,
    RSS_MAX_PER_FEED,
    RSS_NO_FILTER_SOURCES,
)
from crawlers.base import Article

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_UNIFORM_SCORE = 100.0


def _clean(text: object, limit: int = 400) -> str:
    if not isinstance(text, str):
        return ""
    stripped = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", html.unescape(stripped)).strip()[:limit]


def _entry_date(entry) -> str | None:
    """entry의 발행일을 ISO 날짜 문자열로 반환. 없으면 None."""
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime.fromtimestamp(timegm(parsed), tz=UTC).date().isoformat()
            except (ValueError, OverflowError, TypeError):
                continue
    return None


def _matches_ai_keywords(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in AI_KEYWORDS)


def _feed_articles(source: str, entries, needs_filter: bool) -> list[Article]:
    articles: list[Article] = []
    for entry in entries:
        title = _clean(getattr(entry, "title", ""), limit=250)
        url = str(getattr(entry, "link", "") or "").strip()
        if not title or not url:
            continue

        published = _entry_date(entry)
        if published is None:
            # 발행일 없는 RSS 항목은 신선도를 보증할 수 없으므로 채택하지 않는다.
            continue

        description = _clean(getattr(entry, "summary", "") or getattr(entry, "description", ""))
        if needs_filter and not _matches_ai_keywords(f"{title} {description}"):
            continue

        articles.append(
            Article(
                url=url,
                title=title,
                source=source,
                description=description,
                author=_clean(getattr(entry, "author", ""), limit=100),
                published_at=published,
                platform_score=_UNIFORM_SCORE,
                keywords=[],
            )
        )
        if len(articles) >= RSS_MAX_PER_FEED:
            break
    return articles


def fetch_candidates(max_age_days: int = RECENCY_MAX_AGE_DAYS) -> list[Article]:
    """RSS_FEEDS 전체를 순회해 AI 관련 최신 기사를 수집한다. 실패한 피드는 건너뛴다."""
    import feedparser

    collected: dict[str, Article] = {}
    cutoff = (datetime.now(tz=UTC).date().toordinal()) - max_age_days

    for source, feed_url in RSS_FEEDS.items():
        needs_filter = source not in RSS_NO_FILTER_SOURCES
        try:
            parsed = feedparser.parse(feed_url)
            entries = parsed.entries or []
        except Exception as e:
            print(f"[RSS] '{source}' 파싱 실패 — 건너뜀 ({e})")
            continue

        for article in _feed_articles(source, entries, needs_filter):
            if datetime.fromisoformat(article.published_at).date().toordinal() < cutoff:
                continue
            collected.setdefault(article.url, article)

    articles = sorted(collected.values(), key=lambda a: a.published_at, reverse=True)
    print(f"[RSS] {len(articles)}개 후보 수집 (최근 {max_age_days}일, 피드 {len(RSS_FEEDS)}개)")
    return articles
