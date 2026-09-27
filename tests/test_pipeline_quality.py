from __future__ import annotations

import json

from article_quality import VerifiedArticle
from crawlers.base import Article
from tests.conftest import days_ago

_INACTIVE_INTENT = {
    "active": False,
    "summary": "",
    "focus_areas": [],
    "boost_topics": [],
    "avoid_topics": [],
    "focus_keywords": [],
    "avoid_keywords": [],
    "search_hints": "",
    "recency_hours": 48,
    "expires_at": None,
}


def _raw(url: str, title: str) -> Article:
    return Article(
        url=url,
        title=title,
        source="Test Source",
        description="Original English description",
        author="Engineer",
        published_at=days_ago(0),
        platform_score=100.0,
        keywords=["ai coding"],
    )


def _verified(article: Article) -> VerifiedArticle:
    return VerifiedArticle(
        article=article,
        canonical_url=article.url,
        language="en",
        published_at=article.published_at,
        excerpt="Detailed code examples and a reproducible game client workflow. " * 20,
        trusted_source=True,
    )


def _approved(article: Article, score: float = 92.0) -> Article:
    return Article(
        url=article.url,
        title="게임 클라이언트 코드에 적용하는 AI 리팩터링",
        source=article.source,
        description=(
            "AI를 사용해 게임 클라이언트 코드를 안전하게 리팩터링하고 테스트하는 절차입니다.\n\n"
            "**왜 유용한가**: 실제 코드와 검증 단계가 포함되어 바로 적용할 수 있습니다.\n\n"
            f"원문 제목: {article.title}"
        ),
        author=article.author,
        published_at=article.published_at,
        platform_score=score,
        keywords=["ai_programming", "game_client", "engine:unreal"],
    )


def _setup(mocker, raw_articles: list[Article], reviewed: list[Article]) -> None:
    mocker.patch("pipeline.curator.research", return_value=raw_articles)
    mocker.patch("pipeline.load_preference_profile", return_value=None)
    mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)
    mocker.patch("pipeline.article_quality.verify_articles", return_value=[_verified(a) for a in raw_articles])
    mocker.patch("pipeline.editorial_review.review_articles", return_value=reviewed)
    mocker.patch("pipeline.feed_pool.collect", return_value=[])


def test_pipeline_publishes_only_reviewed_articles_and_caps_at_two(mocker, tmp_db) -> None:
    raw_articles = [_raw(f"https://example.com/{index}", f"Original {index}") for index in range(3)]
    reviewed = [_approved(raw_articles[0]), _approved(raw_articles[1], 89.0)]
    _setup(mocker, raw_articles, reviewed)

    from pipeline import run_curation_pipeline

    result = run_curation_pipeline(count=5)

    assert len(result["articles"]) == 2
    assert result["quality_dropped"] == 1
    assert all("게임 클라이언트" in article["title"] for article in result["articles"])
    # 목 함수가 report를 채우지 않아도 통계 키는 항상 존재해야 한다 (0건 원인 규명용).
    assert result["stages"]["verify_attempted"] == 0
    assert result["stages"]["review_candidates"] == 0
    assert result["stages"]["reason_counts"] == {}

    import database as db

    assert db.get_all_article_urls() == {raw_articles[0].url, raw_articles[1].url}


def test_posted_korean_brief_still_blocks_its_english_near_duplicate(mocker, tmp_db) -> None:
    """저장 제목은 한국어(title_ko)라 영어 후보와 비교하면 근중복이 걸리지 않았다.

    심사가 description에 남긴 원문 제목까지 비교해야 같은 주제의 재게시와 재심사를 막는다.
    """
    import database as db
    import pipeline
    from editorial_review import apply_decisions, parse_review_decisions

    posted = _raw("https://example.com/posted", "Claude Code hooks, skills and subagents for production workflows")
    decision = {
        "url": posted.url,
        "verdict": "KEEP",
        "quality_score": 90,
        "title_ko": "프로덕션 워크플로를 위한 클로드 코드 훅·스킬·서브에이전트",
        "summary_ko": "훅과 스킬, 서브에이전트를 조합해 작업 흐름을 만드는 방법을 설명합니다.",
        "why_it_matters_ko": "바로 적용할 수 있는 구성 예시가 있습니다.",
        "content_type": "ai_programming",
    }
    brief = apply_decisions([_verified(posted)], parse_review_decisions(json.dumps([decision], ensure_ascii=False)))[0]
    db.upsert_article(brief.to_dict())
    db.mark_as_posted(db.get_pending_articles()[0]["id"], "msg-1", "chan-1")

    candidate = _raw("https://other.example/guide", "Production Claude Code workflow with hooks, subagents, and skills")
    _setup(mocker, [candidate], [])

    pipeline.run_curation_pipeline(count=1)

    review = pipeline.editorial_review.review_articles
    assert review.call_args_list
    assert all(call.args[0] == [] for call in review.call_args_list)


def test_unreviewed_pending_article_is_not_selected(mocker, tmp_db) -> None:
    import database as db

    unreviewed = _raw("https://example.com/unreviewed", "Unreviewed English article")
    db.upsert_article(unreviewed.to_dict())
    _setup(mocker, [], [])

    from pipeline import run_curation_pipeline

    result = run_curation_pipeline(count=2)

    assert result["articles"] == []


def test_stale_pending_rows_do_not_hide_fresh_reviewed_article(mocker, tmp_db) -> None:
    import database as db

    # 완화 루프가 창을 최대 90일까지 넓히므로, "되살아날 수 없는" 기한으로 둬야
    # 이 테스트가 원래 막으려던 회귀(LIMIT이 신선한 기사를 가리는 문제)만 검증한다.
    for index in range(40):
        stale = _approved(_raw(f"https://example.com/stale-{index}", f"Stale {index}"))
        stale.published_at = days_ago(200)
        db.upsert_article(stale.to_dict())
    fresh = _approved(_raw("https://example.com/fresh-reviewed", "Fresh reviewed"))
    db.upsert_article(fresh.to_dict())
    with db._db() as conn:
        conn.execute("UPDATE articles SET final_score = 1.0 WHERE url LIKE '%/stale-%'")
    _setup(mocker, [], [])

    from pipeline import run_curation_pipeline

    result = run_curation_pipeline(count=2)

    assert [article["url"] for article in result["articles"]] == [fresh.url]
