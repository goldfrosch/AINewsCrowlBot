"""
HackerNews 후보 수집 (Algolia search_by_date)

Claude 웹 검색은 발행일을 지시대로 지키지 않는다(실측: 최근 40건 중 87.5%가 30일 초과).
반면 HN Algolia는 `created_at_i` 유닉스 타임스탬프를 직접 주므로 신선도가 보장된다.
이 모듈은 "발행일이 확실한 후보"를 만드는 용도이며, Claude 경로가 목표 수량을
채우지 못할 때만 보충으로 사용된다.
"""

import time
from datetime import UTC, datetime

import requests

from config import (
    FEED_HTTP_TIMEOUT,
    HN_MAX_RESULTS,
    HN_MIN_POINTS,
    HN_SEARCH_QUERIES,
    RECENCY_MAX_AGE_DAYS,
)
from crawlers.base import Article

_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"
_ITEM_URL = "https://news.ycombinator.com/item?id={}"

# 모든 프로듀서가 동일한 platform_score 밴드를 쓰도록 통일한다.
# 실제 HN 점수는 description에 노출해 정보 손실을 막는다.
_UNIFORM_SCORE = 100.0


def _cutoff_timestamp(max_age_days: int) -> int:
    return int(time.time()) - max_age_days * 86400


def _hit_to_article(hit: dict) -> Article | None:
    title = str(hit.get("title") or "").strip()
    if not title:
        return None

    object_id = str(hit.get("objectID") or "").strip()
    url = str(hit.get("url") or "").strip() or (_ITEM_URL.format(object_id) if object_id else "")
    if not url:
        return None

    created_at_i = hit.get("created_at_i")
    if not isinstance(created_at_i, (int, float)):
        return None
    published = datetime.fromtimestamp(float(created_at_i), tz=UTC).date().isoformat()

    points = int(hit.get("points") or 0)
    comments = int(hit.get("num_comments") or 0)
    discussion = _ITEM_URL.format(object_id) if object_id else ""
    description = f"🔥 HN {points} points · 댓글 {comments}개"
    if discussion and discussion != url:
        description += f"\n토론: {discussion}"

    return Article(
        url=url,
        title=title,
        source="HackerNews",
        description=description,
        author=str(hit.get("author") or ""),
        published_at=published,
        platform_score=_UNIFORM_SCORE,
        keywords=[],
    )


def fetch_candidates(
    max_age_days: int = RECENCY_MAX_AGE_DAYS,
    min_points: int = HN_MIN_POINTS,
    queries: list[str] | None = None,
) -> list[Article]:
    """최근 max_age_days 이내 HN 스토리를 수집한다. 실패한 쿼리는 건너뛴다."""
    cutoff = _cutoff_timestamp(max_age_days)
    per_query = max(HN_MAX_RESULTS // max(len(queries or HN_SEARCH_QUERIES), 1), 5)
    collected: dict[str, Article] = {}

    for query in queries or HN_SEARCH_QUERIES:
        params = {
            "query": query,
            "tags": "story",
            "numericFilters": f"created_at_i>{cutoff},points>={min_points}",
            "hitsPerPage": per_query,
        }
        try:
            response = requests.get(_ALGOLIA_URL, params=params, timeout=FEED_HTTP_TIMEOUT)
            response.raise_for_status()
            hits = response.json().get("hits") or []
        except Exception as e:
            print(f"[HackerNews] '{query}' 조회 실패 — 건너뜀 ({e})")
            continue

        for hit in hits:
            if not isinstance(hit, dict):
                continue
            article = _hit_to_article(hit)
            if article and article.url not in collected:
                collected[article.url] = article

    articles = sorted(collected.values(), key=lambda a: a.published_at, reverse=True)
    print(f"[HackerNews] {len(articles)}개 후보 수집 (최근 {max_age_days}일, {min_points}점 이상)")
    return articles
