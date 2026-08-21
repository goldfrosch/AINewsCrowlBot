"""
Discord 독립적 큐레이션 파이프라인

bot.py, dry_run.py, 테스트에서 공통으로 사용합니다.

수집 순서:
  1. Claude 웹 검색 (curator.research) — 오버페치 + 톱업 루프
  2. 신선도 컷오프 통과분만 DB 저장 (잉여는 pending 저수지로 남음)
  3. 랭킹 후 목표 수량 미달이면 HN/RSS 후보풀로 보충

기존 파이프라인은 1번이 실패하면 그날 브리핑이 0건이었다(실측 45일 중 13일).
3번이 두 번째 수집 경로 역할을 해 단일 실패점을 없앤다.
"""

import curator
import database as db
import recency
from agents.preference_analysis import load_preference_profile
from config import ARTICLES_PER_POST, GAME_DEV_KEYWORDS
from crawlers import feed_pool
from curation_intent import load_curation_intent
from ranker import rank_articles

# 저수지에서 끌어올 여유분. 신선도 필터로 탈락하는 분량을 감안해 넉넉히 조회한다.
_PENDING_FETCH_SLACK = 30


def _is_game_dev_article(article: dict) -> bool:
    """기사의 키워드, 제목, 설명에서 게임 개발 관련 키워드가 포함되어 있는지 확인."""
    keywords = article.get("keywords", [])
    if isinstance(keywords, str):
        try:
            import json

            keywords = json.loads(keywords)
        except Exception:
            keywords = []
    title = (article.get("title") or "").lower()
    description = (article.get("description") or "").lower()
    search_text = f"{title} {description}"
    for kw in keywords:
        kw_lower = kw.lower()
        for gdk in GAME_DEV_KEYWORDS:
            if gdk in kw_lower or kw_lower in gdk:
                return True
    return any(gdk in search_text for gdk in GAME_DEV_KEYWORDS)


def _ensure_game_dev_article(ranked: list[dict], count: int) -> list[dict]:
    """
    상위 count개 중 게임 개발+AI 기사가 최소 1개 포함되도록 보장.

    전략:
    1. 상위 count개에 이미 게임 개발 기사가 있으면 그대로 반환.
    2. 없으면, 전체 랭킹에서 가장 점수가 높은 게임 개발 기사 1개를
       상위 count개 중 가장 점수가 낮은 일반 기사와 교체.
    """
    if count <= 0:
        return ranked[:count]

    selected = ranked[:count]

    # 이미 게임 개발 기사가 포함되어 있으면 통과
    if any(_is_game_dev_article(a) for a in selected):
        return selected

    # 전체 랭킹에서 게임 개발 기사 찾기
    game_dev_articles = [a for a in ranked if _is_game_dev_article(a)]
    if not game_dev_articles:
        # 게임 개발 기사가 아예 없으면 그대로 반환 (강제 생성 불가)
        return selected

    # 가장 점수가 높은 게임 개발 기사 1개 선택
    game_article = game_dev_articles[0]

    # 이미 selected에 포함되어 있지 않은지 확인
    game_urls = {a.get("url") for a in selected if a.get("url")}
    if game_article.get("url") in game_urls:
        return selected

    # selected 중 가장 점수가 낮은 일반 기사와 교체
    # (동점이면 뒤에 있는 기사 = 인덱스가 큰 기사 우선 교체)
    worst_idx = max(
        range(len(selected)),
        key=lambda i: (-selected[i].get("final_score", 0), i),
    )
    result = list(selected)
    result[worst_idx] = game_article
    return result


def _store(articles) -> int:
    """Article 객체들을 DB에 저장하고 신규 저장 수를 반환한다."""
    new_count = 0
    for a in articles:
        is_new = db.upsert_article(
            {
                "url": a.url,
                "title": a.title,
                "source": a.source,
                "description": a.description,
                "author": a.author,
                "image_url": "",
                "published_at": a.published_at,
                "platform_score": a.platform_score,
                "keywords": a.keywords if isinstance(a.keywords, list) else [],
            }
        )
        if is_new:
            new_count += 1
    return new_count


def _select(count: int, max_age_days: int) -> list[dict]:
    """pending 저수지에서 신선한 기사만 골라 랭킹 후 상위 count개를 반환한다."""
    pending = db.get_pending_articles(limit=count + _PENDING_FETCH_SLACK)
    fresh = [a for a in pending if not recency.is_stale(a.get("published_at"), max_age_days)]
    ranked = rank_articles(fresh)
    return _ensure_game_dev_article(ranked, count)


def _topup_from_feeds(shortfall: int, max_age_days: int) -> int:
    """HN/RSS 후보풀에서 부족분을 채운다. 저장한 기사 수를 반환한다.

    남는 기사가 저수지에 쌓여 다음 날 랭킹을 왜곡하지 않도록
    부족분만큼만 저장한다.
    """
    if shortfall <= 0:
        return 0

    known = db.get_all_article_urls()
    candidates = feed_pool.collect(shortfall * 2, exclude_urls=known, max_age_days=max_age_days)

    inserted = 0
    for article in candidates:
        if _store([article]):
            inserted += 1
        if inserted >= shortfall:
            break

    if inserted:
        print(f"[Pipeline] feed 후보풀로 {inserted}개 보충 (부족분 {shortfall}개)")
    else:
        print(f"[Pipeline] feed 후보풀에서도 보충 실패 (부족분 {shortfall}개)")
    return inserted


def run_curation_pipeline(count: int = ARTICLES_PER_POST) -> dict:
    """
    큐레이션 파이프라인을 실행합니다 (Discord 의존 없음).

    Returns:
        {
            "articles":      list[dict],  # 랭킹 완료 기사
            "raw_count":     int,         # curator가 반환한 기사 수
            "fresh_count":   int,         # 신선도 컷오프를 통과한 수
            "stale_dropped": int,         # 기한초과로 버려진 수
            "new_count":     int,         # DB에 새로 저장된 기사 수
            "feed_topup":    int,         # HN/RSS로 보충한 수
            "max_age_days":  int,         # 적용된 신선도 컷오프
            "error":         str | None,  # curator 에러 (보충 성공 시에도 유지)
        }
    """
    pref_profile = load_preference_profile()
    if pref_profile:
        print(f"[Pipeline] 선호도 프로파일 로드 — {pref_profile.get('summary', '')}")

    from agents.news_curation_agent import get_topic_keys

    intent = load_curation_intent(valid_topics=get_topic_keys())
    if intent.get("active"):
        print(f"[Pipeline] 큐레이션 의도 로드 — {intent.get('summary', '')}")

    max_age_days = recency.max_age_from_intent(intent)
    # 오늘 게시분만 보던 기존 로직은 06:00 시점에 항상 빈 배열이라
    # 큐레이터가 어제·그제 기사를 재추천했고 UNIQUE 제약으로 조용히 폐기됐다.
    exclude_urls = db.get_recent_posted_urls()

    raw_articles = []
    error = None
    try:
        raw_articles = curator.research(count, exclude_urls, pref_profile or {}, intent=intent)
    except Exception as e:
        print(f"[Pipeline] curator.research() 실패 — feed 후보풀로 보충 시도: {e}")
        error = str(e)

    fresh_articles = [a for a in raw_articles if not recency.is_stale(a.published_at, max_age_days)]
    stale_dropped = len(raw_articles) - len(fresh_articles)
    if stale_dropped:
        print(f"[Pipeline] 기한초과 {stale_dropped}개 제외 (최근 {max_age_days}일 기준)")

    new_count = _store(fresh_articles)
    print(f"[Pipeline] 큐레이션 — 수집 {len(raw_articles)}개 / 신선 {len(fresh_articles)}개 / 신규 {new_count}개")

    final = _select(count, max_age_days)

    feed_topup = 0
    if len(final) < count:
        feed_topup = _topup_from_feeds(count - len(final), max_age_days)
        if feed_topup:
            final = _select(count, max_age_days)

    print(f"[Pipeline] 게시 대상 {len(final)}개 / 목표 {count}개")

    return {
        "articles": final,
        "raw_count": len(raw_articles),
        "fresh_count": len(fresh_articles),
        "stale_dropped": stale_dropped,
        "new_count": new_count,
        "feed_topup": feed_topup,
        "max_age_days": max_age_days,
        "error": error,
    }
