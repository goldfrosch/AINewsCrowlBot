"""
결정론적 신선 후보 풀 (HackerNews + RSS)

역할: Claude 웹 검색 경로가 목표 수량을 채우지 못했을 때 보충한다.

배경 — 기존에는 수집 경로가 Claude 웹 검색 단 하나였고, 그래서 그 호출이
빈손이면 그날 브리핑이 0건이 됐다(실측: 최근 45일 중 13일). 이 모듈이
두 번째 경로가 되어 단일 실패점을 없앤다. HN·RSS는 발행일이 API/피드에서
직접 오므로 신선도도 함께 보장된다.

선별 규칙:
  1. 신선도 컷오프 통과
  2. 관련도(AI/게임개발 키워드 매치) FEED_MIN_RELEVANCE 이상
  3. 소스 티어 → 관련도 → 최신순으로 정렬
  4. 소스별 상한 적용

3·4번이 필요한 이유(QA에서 실제로 확인):
  - 날짜만으로 정렬하니 하루 60편 쏟아지는 arXiv가 상위 3개를 독식했고
    그중 둘은 대상 독자와 무관했다(항만 크레인 스케줄링, AI 의식론).
  - 관련도를 키워드 개수로만 재니 초록이 긴 arXiv가 또 이겨서
    HN 후보 16개가 전부 밀렸다. 그래서 티어를 관련도보다 앞에 둔다.
  - 제목 매치를 가중하고 설명 매치는 상한을 둬 길이 편향을 없앤다.
"""

from config import (
    AI_KEYWORDS,
    FEED_DEFAULT_TIER,
    FEED_DESC_MATCH_CAP,
    FEED_GAME_DEV_WEIGHT,
    FEED_MAX_PER_SOURCE,
    FEED_MIN_RELEVANCE,
    FEED_POOL_ENABLED,
    FEED_SOURCE_TIERS,
    FEED_TITLE_WEIGHT,
    GAME_DEV_KEYWORDS,
    RECENCY_MAX_AGE_DAYS,
)
from crawlers.base import Article
from recency import age_days, is_stale


def _match_count(text: str) -> int:
    lowered = text.lower()
    ai_hits = sum(1 for kw in AI_KEYWORDS if kw in lowered)
    game_hits = sum(1 for kw in GAME_DEV_KEYWORDS if kw in lowered)
    return ai_hits + FEED_GAME_DEV_WEIGHT * game_hits


def relevance(article: Article) -> int:
    """대상 독자(AI 개발자 / AI 활용 게임 개발자) 관련도 점수.

    제목 매치를 가중하고 설명 매치는 상한을 둔다. 그렇지 않으면
    키워드가 밀집된 긴 학술 초록이 항상 이긴다.
    """
    title_score = FEED_TITLE_WEIGHT * _match_count(article.title)
    desc_score = min(_match_count(article.description), FEED_DESC_MATCH_CAP)
    return title_score + desc_score


def source_tier(source: str) -> int:
    return FEED_SOURCE_TIERS.get(source, FEED_DEFAULT_TIER)


def _sort_key(article: Article) -> tuple[int, int, int]:
    """소스 티어 → 관련도 높은 순 → 신선한 순 (오름차순 정렬용)."""
    age = age_days(article.published_at)
    return (source_tier(article.source), -relevance(article), age if age is not None else 999)


def collect(
    count: int,
    exclude_urls: set[str] | None = None,
    max_age_days: int = RECENCY_MAX_AGE_DAYS,
) -> list[Article]:
    """신선하고 관련도 높은 후보를 최대 count개 반환한다.

    Args:
        count:        필요한 기사 수 (0 이하면 빈 리스트)
        exclude_urls: 이미 DB에 있거나 이번 실행에서 채택된 URL
        max_age_days: 신선도 컷오프

    Returns:
        Article 리스트 (len ≤ count). 네트워크 실패 시 빈 리스트.
    """
    if count <= 0 or not FEED_POOL_ENABLED:
        return []

    excluded = exclude_urls or set()
    candidates: list[Article] = []

    for producer in _producers():
        try:
            candidates.extend(producer(max_age_days))
        except Exception as e:
            print(f"[FeedPool] 후보 수집 실패 — 건너뜀 ({e})")

    eligible = _eligible(candidates, excluded, max_age_days)
    eligible.sort(key=_sort_key)
    selected = _cap_per_source(eligible, count)

    print(f"[FeedPool] 적격 후보 {len(eligible)}개 중 {len(selected)}개 채택 (요청 {count}개)")
    return selected


def _eligible(candidates: list[Article], excluded: set[str], max_age_days: int) -> list[Article]:
    seen: set[str] = set()
    result: list[Article] = []
    for article in candidates:
        if not article.url or article.url in excluded or article.url in seen:
            continue
        if is_stale(article.published_at, max_age_days=max_age_days):
            continue
        if relevance(article) < FEED_MIN_RELEVANCE:
            continue
        seen.add(article.url)
        result.append(article)
    return result


def _cap_per_source(articles: list[Article], count: int) -> list[Article]:
    """소스별 상한을 지키며 앞에서부터 채운다. 부족하면 상한을 풀어 보충한다."""
    per_source: dict[str, int] = {}
    selected: list[Article] = []

    for article in articles:
        if len(selected) >= count:
            return selected
        used = per_source.get(article.source, 0)
        if used >= FEED_MAX_PER_SOURCE:
            continue
        per_source[article.source] = used + 1
        selected.append(article)

    # 상한 때문에 목표를 못 채웠다면 남은 후보로 채운다 (0건 방지가 최우선).
    if len(selected) < count:
        chosen = {a.url for a in selected}
        for article in articles:
            if len(selected) >= count:
                break
            if article.url not in chosen:
                selected.append(article)
    return selected


def _producers():
    """지연 임포트로 requests/feedparser 미설치 환경에서도 모듈 로드가 되게 한다."""
    from crawlers import hackernews, rss

    return (
        lambda days: hackernews.fetch_candidates(max_age_days=days),
        lambda days: rss.fetch_candidates(max_age_days=days),
    )
