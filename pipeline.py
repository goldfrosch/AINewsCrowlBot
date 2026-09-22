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

import article_quality
import claude_search
import curator
import database as db
import editorial_review
import recency
from agents.agent_spec import pillar_max_age_days
from agents.preference_analysis import load_preference_profile
from config import (
    ARTICLES_PER_POST,
    CONTENT_TYPE_MAX_AGE_DAYS,
    MAX_PER_SOURCE_IN_POST,
    MAX_PER_TOPIC_IN_POST,
    MIN_ACCEPTABLE_ARTICLES,
    RECENCY_RELAXATION_DAYS,
)
from crawlers import feed_pool
from curation_intent import load_curation_intent
from ranker import rank_articles

# 저수지에서 끌어올 여유분. 신선도 필터로 탈락하는 분량을 감안해 넉넉히 조회한다.
_PENDING_FETCH_SLACK = 60
_REVIEWED_CONTENT_TYPES = set(CONTENT_TYPE_MAX_AGE_DAYS)
# 저수지·랭킹 단계에서 쓰는 최대 창. 가장 긴 필라(그래픽스)를 기준으로 조회한 뒤
# 기사별 분류에 맞는 창으로 다시 거른다.
_WIDEST_MAX_AGE = max(CONTENT_TYPE_MAX_AGE_DAYS.values())


def _article_window(article, base: int) -> int:
    """수집 단계 기사의 신선도 창. 필라가 있으면 필라 정책을 따른다."""
    pillar = getattr(article, "pillar", "") or ""
    return max(pillar_max_age_days(pillar, base), base) if pillar else base


def _stored_window(keywords, base: int) -> int:
    """저장된 기사의 신선도 창. content_type 키워드로 필라 정책을 복원한다.

    `base`보다 좁아지지 않는다. 완화 패스가 창을 30일로 넓혔는데 분류별 창(14일)이
    그걸 되돌리면, 어렵게 찾아온 기사가 저수지에서 다시 탈락해 완화가 무력화된다.
    """
    windows = [CONTENT_TYPE_MAX_AGE_DAYS[kw] for kw in keywords or [] if kw in CONTENT_TYPE_MAX_AGE_DAYS]
    return max([*windows, base])


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
                "image_url": a.image_url,
                "published_at": a.published_at,
                "platform_score": a.platform_score,
                "keywords": a.keywords if isinstance(a.keywords, list) else [],
            }
        )
        if is_new:
            new_count += 1
    return new_count


def _diversify(ranked: list[dict], count: int) -> list[dict]:
    """같은 소스·같은 주제가 브리핑을 독식하지 않도록 상한을 걸고 뽑는다.

    상한 때문에 목표를 못 채우면 밀어둔 후보로 뒤를 채운다 — 다양성 때문에
    0건이 되는 것은 본말전도다.
    """
    picked: list[dict] = []
    per_source: dict[str, int] = {}
    deferred: list[dict] = []

    for article in ranked:
        source = (article.get("source") or "").strip().lower()
        title = article.get("title") or ""
        same_topic = sum(1 for chosen in picked if article_quality.shares_topic(title, chosen.get("title") or ""))
        if per_source.get(source, 0) >= MAX_PER_SOURCE_IN_POST or same_topic >= MAX_PER_TOPIC_IN_POST:
            deferred.append(article)
            continue
        per_source[source] = per_source.get(source, 0) + 1
        picked.append(article)
        if len(picked) == count:
            return picked

    return (picked + deferred)[:count]


def _select(count: int, max_age_days: int) -> list[dict]:
    """pending 저수지에서 검수 완료된 신선 기사만 품질순으로 반환한다.

    기사마다 분류(content_type)에 맞는 창을 적용한다. 단일 창을 쓰면 에버그린
    그래픽스 자료가 실무 기준(2주)에 걸려 저수지에서 영영 안 나오거나,
    반대로 실무 아티클이 4개월짜리 창으로 새어 나온다.
    """
    lookup_age = max(max_age_days, _WIDEST_MAX_AGE)
    pending = db.get_pending_articles(limit=count + _PENDING_FETCH_SLACK, max_age_days=lookup_age)
    fresh = [
        article
        for article in pending
        if _REVIEWED_CONTENT_TYPES.intersection(article.get("keywords", []))
        and not recency.is_stale(
            article.get("published_at"),
            _stored_window(article.get("keywords", []), max_age_days),
        )
    ]
    return _diversify(rank_articles(fresh), count)


def _new_stage_report() -> dict:
    """단계별 손실 통계. 0건 원인 규명을 위해 모든 게이트의 통과율을 추적한다."""
    return {
        "verify_attempted": 0,
        "verify_passed": 0,
        "dup_removed": 0,
        "review_candidates": 0,
        "review_kept": 0,
        "review_rejected": 0,
        "reason_counts": {},
    }


def _new_stage_report_defaults() -> dict:
    return {"verify_reasons": {}, "passes": []}


def _merge_stage_report(stages: dict, verify: dict, review: dict, dup_removed: int) -> None:
    stages["verify_attempted"] += verify.get("attempted", 0)
    stages["verify_passed"] += verify.get("passed", 0)
    stages["dup_removed"] += dup_removed
    candidates = review.get("candidates", 0)
    kept = review.get("kept", 0)
    stages["review_candidates"] += candidates
    stages["review_kept"] += kept
    stages["review_rejected"] += candidates - kept
    for reason, count in (review.get("reasons") or {}).items():
        stages["reason_counts"][reason] = stages["reason_counts"].get(reason, 0) + count
    verify_reasons = stages.setdefault("verify_reasons", {})
    for reason, count in (verify.get("reasons") or {}).items():
        verify_reasons[reason] = verify_reasons.get(reason, 0) + count


def _review_candidates(articles, max_age_days: int, recent_titles: list[str], stages: dict, relax_level: int = 0):
    verify_report: dict = {}
    verified = article_quality.verify_articles(
        articles,
        lambda article: _article_window(article, max_age_days),
        report=verify_report,
    )
    unique = article_quality.remove_near_duplicates(verified, recent_titles)
    review_report: dict = {}
    reviewed = editorial_review.review_articles(unique, report=review_report, relax_level=relax_level)
    _merge_stage_report(stages, verify_report, review_report, len(verified) - len(unique))
    return reviewed


def _topup_from_feeds(shortfall: int, max_age_days: int, stages: dict, relax_level: int = 0) -> tuple[int, int]:
    """HN/RSS 후보도 본문 검증과 편집 심사를 거쳐 부족분을 채운다.

    부족분의 4배를 받는 이유: feed 후보도 본문검증·편집심사 수율이 웹 검색 후보와
    같아서(실측 약 25%) 2배만 받으면 보충이 거의 항상 실패한다.
    """
    if shortfall <= 0:
        return 0, 0

    known = db.get_all_article_urls()
    candidates = feed_pool.collect(shortfall * 4, exclude_urls=known, max_age_days=max_age_days)
    reviewed = _review_candidates(candidates, max_age_days, db.get_recent_posted_titles(), stages, relax_level)
    reviewed.sort(key=lambda article: article.platform_score, reverse=True)
    inserted = _store(reviewed[:shortfall])
    quality_dropped = len(candidates) - len(reviewed)

    if inserted:
        print(f"[Pipeline] feed 후보풀로 {inserted}개 보충 (부족분 {shortfall}개)")
    else:
        print(f"[Pipeline] feed 후보풀에서도 보충 실패 (부족분 {shortfall}개)")
    return inserted, quality_dropped


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
            "stages":        dict,        # 단계별 통과율·탈락 사유 (본문검증/심사)
        }
    """
    target_count = min(max(count, 0), ARTICLES_PER_POST)
    pref_profile = load_preference_profile()
    if pref_profile:
        print(f"[Pipeline] 선호도 프로파일 로드 — {pref_profile.get('summary', '')}")

    from agents.news_curation_agent import get_topic_keys

    intent = load_curation_intent(valid_topics=get_topic_keys())
    if intent.get("active"):
        print(f"[Pipeline] 큐레이션 의도 로드 — {intent.get('summary', '')}")

    base_max_age = recency.max_age_from_intent(intent)
    # 오늘 게시분만 보던 기존 로직은 06:00 시점에 항상 빈 배열이라
    # 큐레이터가 어제·그제 기사를 재추천했고 UNIQUE 제약으로 조용히 폐기됐다.
    exclude_urls = db.get_recent_posted_urls()

    stages = _new_stage_report()
    stages.update(_new_stage_report_defaults())

    totals = {"raw": 0, "fresh": 0, "stale_dropped": 0, "quality_dropped": 0, "new": 0}
    error = None
    fatal_api_error = False
    max_age_days = base_max_age

    # 이전 실행의 잉여(저수지)를 먼저 본다. 검색이 부진한 날의 1차 방어선이다.
    final = _select(target_count, base_max_age)
    if final:
        print(f"[Pipeline] 저수지에서 {len(final)}개 확보 (검색 전)")

    # 목표를 못 채우면 신선도 창과 품질 컷을 한 단계씩 넓혀 다시 검색한다.
    # 단일 패스 구조에서는 한 게이트만 어긋나도 그날이 통째로 0건이 됐다.
    #
    # 활성 큐레이션 의도가 창을 좁혔다면 그 값은 사용자의 명시적 지시이므로 넓히지 않는다.
    # "최근 24시간" 요청에 90일 전 기사를 끼워 넣는 것은 0건보다 나쁜 배신이다.
    # 이때는 품질 컷만 단계적으로 내린다.
    intent_locked = bool(intent and intent.get("active") and intent.get("recency_hours"))
    windows = [base_max_age] * len(RECENCY_RELAXATION_DAYS) if intent_locked else list(RECENCY_RELAXATION_DAYS)

    for level, window in enumerate(windows):
        if len(final) >= target_count:
            break
        max_age_days = max(base_max_age, window)
        trusted_cut, unknown_cut = editorial_review.thresholds(level)
        print(
            f"[Pipeline] 패스 {level + 1}/{len(windows)} — "
            f"창 {max_age_days}일 · 품질컷 {trusted_cut:.0f}/{unknown_cut:.0f} · "
            f"현재 {len(final)}/{target_count}개"
        )

        shortfall = target_count - len(final)
        try:
            raw_articles = curator.research(
                shortfall,
                exclude_urls,
                pref_profile or {},
                intent=intent,
                max_age_days=max_age_days,
            )
        except claude_search.FatalSearchError as e:
            # 크레딧 소진·인증 실패는 완화해도 결과가 같다. 남은 패스를 포기하고
            # 사용자에게 진짜 원인을 올려 보낸다 — 조용한 0건이 100일 블랙아웃을 만들었다.
            print(f"[Pipeline] 복구 불가 API 오류 — 남은 패스를 중단합니다: {e}")
            error = f"복구 불가 API 오류: {e}"
            fatal_api_error = True
            break
        except Exception as e:
            print(f"[Pipeline] curator.research() 실패 — 다음 단계로 진행: {e}")
            error = str(e)
            raw_articles = []

        fresh_articles = [
            a for a in raw_articles if not recency.is_stale(a.published_at, _article_window(a, max_age_days))
        ]
        stale_dropped = len(raw_articles) - len(fresh_articles)
        reviewed = _review_candidates(
            fresh_articles, max_age_days, db.get_recent_posted_titles(), stages, relax_level=level
        )
        new_count = _store(reviewed)

        totals["raw"] += len(raw_articles)
        totals["fresh"] += len(fresh_articles)
        totals["stale_dropped"] += stale_dropped
        totals["quality_dropped"] += len(fresh_articles) - len(reviewed)
        totals["new"] += new_count
        stages["passes"].append(
            {
                "level": level,
                "max_age_days": max_age_days,
                "raw": len(raw_articles),
                "reviewed": len(reviewed),
                "new": new_count,
            }
        )
        print(
            f"[Pipeline] 패스 {level + 1} 결과 — 수집 {len(raw_articles)}개 / 신선 {len(fresh_articles)}개 / "
            f"심사통과 {len(reviewed)}개 / 신규 {new_count}개"
        )

        final = _select(target_count, max_age_days)

    feed_topup = 0
    # feed 후보도 같은 Anthropic 키로 편집 심사를 받으므로, 계정이 막혔으면 의미가 없다.
    if len(final) < target_count and not fatal_api_error:
        relax_level = len(RECENCY_RELAXATION_DAYS) - 1
        feed_topup, feed_quality_dropped = _topup_from_feeds(
            target_count - len(final), max_age_days, stages, relax_level
        )
        totals["quality_dropped"] += feed_quality_dropped
        totals["new"] += feed_topup
        if feed_topup:
            final = _select(target_count, max_age_days)

    if len(final) < MIN_ACCEPTABLE_ARTICLES:
        print(f"[Pipeline] 경고 — 최소 기준 {MIN_ACCEPTABLE_ARTICLES}개 미달 ({len(final)}개)")
    print(f"[Pipeline] 게시 대상 {len(final)}개 / 목표 {target_count}개")

    return {
        "articles": final,
        "raw_count": totals["raw"],
        "fresh_count": totals["fresh"],
        "stale_dropped": totals["stale_dropped"],
        "quality_dropped": totals["quality_dropped"],
        "new_count": totals["new"],
        "feed_topup": feed_topup,
        "max_age_days": max_age_days,
        "error": error,
        "fatal_api_error": fatal_api_error,
        "stages": stages,
    }
