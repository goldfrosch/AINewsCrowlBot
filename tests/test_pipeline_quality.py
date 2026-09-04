from __future__ import annotations

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

    import database as db

    assert db.get_all_article_urls() == {raw_articles[0].url, raw_articles[1].url}


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

    for index in range(40):
        stale = _approved(_raw(f"https://example.com/stale-{index}", f"Stale {index}"))
        stale.published_at = days_ago(30)
        db.upsert_article(stale.to_dict())
    fresh = _approved(_raw("https://example.com/fresh-reviewed", "Fresh reviewed"))
    db.upsert_article(fresh.to_dict())
    with db._db() as conn:
        conn.execute("UPDATE articles SET final_score = 1.0 WHERE url LIKE '%/stale-%'")
    _setup(mocker, [], [])

    from pipeline import run_curation_pipeline

    result = run_curation_pipeline(count=2)

    assert [article["url"] for article in result["articles"]] == [fresh.url]
