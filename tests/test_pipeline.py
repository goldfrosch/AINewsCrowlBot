"""pipeline.py 통합 테스트"""

import pytest

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
    "search_hints": [],
    "recency_hours": None,
    "expires_at": None,
}


@pytest.fixture(autouse=True)
def no_network_feeds(mocker):
    """HN/RSS 후보풀은 실제 네트워크를 타므로 기본적으로 비활성화한다."""
    return mocker.patch("pipeline.feed_pool.collect", return_value=[])


@pytest.fixture(autouse=True)
def pass_quality_gate(mocker):
    """Legacy pipeline scenarios isolate storage/ranking from network and LLM review."""

    def verify(articles, max_age_days, report=None):
        if report is not None:
            report["attempted"] = len(articles)
            report["passed"] = len(articles)
        return [
            VerifiedArticle(
                article=article,
                canonical_url=article.url,
                language="en",
                published_at=article.published_at,
                excerpt="Reproducible implementation details. " * 20,
                trusted_source=True,
            )
            for article in articles
        ]

    def review(candidates, report=None):
        if report is not None:
            report["candidates"] = len(candidates)
            report["kept"] = len(candidates)
        return [
            Article(
                url=candidate.canonical_url,
                title=candidate.article.title,
                source=candidate.article.source,
                description=candidate.article.description,
                author=candidate.article.author,
                image_url=candidate.article.image_url,
                published_at=candidate.published_at,
                platform_score=candidate.article.platform_score,
                keywords=[*candidate.article.keywords, "ai_programming"],
            )
            for candidate in candidates
        ]

    mocker.patch("pipeline.article_quality.verify_articles", side_effect=verify)
    mocker.patch("pipeline.editorial_review.review_articles", side_effect=review)


def _article(url: str, title: str, *, age_days: int = 1, score: float = 100.0, keywords=None) -> Article:
    return Article(
        url=url,
        title=title,
        source="TestSource",
        description="Test description",
        author="Author",
        published_at=days_ago(age_days),
        platform_score=score,
        keywords=keywords or [],
    )


class TestRunCurationPipeline:
    def test_full_pipeline_mocked(self, mocker, tmp_db):
        mock_articles = [
            _article("https://example.com/pipeline-1", "Pipeline Test 1", age_days=1, keywords=["llm"]),
            _article("https://example.com/pipeline-2", "Pipeline Test 2", age_days=2, keywords=["rag"]),
        ]
        mocker.patch("pipeline.curator.research", return_value=mock_articles)
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)

        from pipeline import run_curation_pipeline

        result = run_curation_pipeline(count=5)

        assert result["error"] is None
        assert result["raw_count"] == 2
        assert result["new_count"] == 2
        assert len(result["articles"]) == 2
        assert all("final_score" in a for a in result["articles"])

    def test_empty_curator_result(self, mocker, tmp_db):
        mocker.patch("pipeline.curator.research", return_value=[])
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)

        from pipeline import run_curation_pipeline

        result = run_curation_pipeline(count=5)

        assert result["error"] is None
        assert result["raw_count"] == 0
        assert result["new_count"] == 0
        assert result["articles"] == []

    def test_curator_exception(self, mocker, tmp_db):
        mocker.patch("pipeline.curator.research", side_effect=Exception("API Error"))
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)

        from pipeline import run_curation_pipeline

        result = run_curation_pipeline(count=5)

        assert result["error"] is not None
        assert "API Error" in result["error"]
        assert result["articles"] == []

    def test_duplicate_articles_counted(self, mocker, tmp_db, sample_articles):
        """이미 DB에 있는 기사는 new_count에서 제외."""
        for a in sample_articles:
            from database import upsert_article

            upsert_article(a)

        mock_articles = [
            _article(sample_articles[0]["url"], sample_articles[0]["title"]),  # already in DB
            _article("https://example.com/new-one", "New Article"),
        ]
        mocker.patch("pipeline.curator.research", return_value=mock_articles)
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)

        from pipeline import run_curation_pipeline

        result = run_curation_pipeline(count=5)

        assert result["raw_count"] == 2
        assert result["new_count"] == 1  # only the new one

    def test_articles_are_ranked(self, mocker, tmp_db):
        """결과 기사가 final_score 기준 내림차순이어야 함."""
        mock_articles = [
            _article(f"https://example.com/rank-{i}", f"Article {i}", age_days=i, score=float(20 * (i + 1)))
            for i in range(5)
        ]
        mocker.patch("pipeline.curator.research", return_value=mock_articles)
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)

        from pipeline import run_curation_pipeline

        result = run_curation_pipeline(count=3)

        assert len(result["articles"]) == 2
        scores = [a["final_score"] for a in result["articles"]]
        assert scores == sorted(scores, reverse=True)

    def test_count_limits_output(self, mocker, tmp_db):
        """count보다 많은 기사가 와도 count개만 반환."""
        mock_articles = [_article(f"https://example.com/limit-{i}", f"Article {i}") for i in range(10)]
        mocker.patch("pipeline.curator.research", return_value=mock_articles)
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)

        from pipeline import run_curation_pipeline

        result = run_curation_pipeline(count=2)

        assert len(result["articles"]) == 2

    def test_pipeline_forwards_intent_to_curator(self, mocker, tmp_db):
        intent = {
            "active": True,
            "summary": "test-intent",
            "focus_areas": ["agentic systems"],
            "boost_topics": ["multi_agent_orchestration"],
            "avoid_topics": [],
            "focus_keywords": ["eval harness"],
            "avoid_keywords": [],
            "search_hints": ["prioritize practical guides"],
            "recency_hours": 48,
            "expires_at": "2026-05-18T00:00:00Z",
        }
        mock_articles = [_article("https://example.com/intended", "Intent Article")]
        research_mock = mocker.patch("pipeline.curator.research", return_value=mock_articles)
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=intent)

        from pipeline import run_curation_pipeline

        run_curation_pipeline(count=1)

        research_mock.assert_called_once()
        call_kwargs = research_mock.call_args.kwargs
        assert call_kwargs["intent"] == intent

    def test_pipeline_forwards_both_profile_and_intent(self, mocker, tmp_db):
        """Active intent AND preference profile should both reach curator.research()."""
        profile = {
            "curation_hints": {
                "boost_sources": ["ArXiv"],
                "avoid_sources": [],
                "focus_keywords": ["agent"],
                "skip_keywords": [],
            }
        }
        intent = {
            "active": True,
            "summary": "integration-test-intent",
            "focus_areas": ["agentic systems"],
            "boost_topics": ["multi_agent_orchestration"],
            "avoid_topics": [],
            "focus_keywords": ["eval"],
            "avoid_keywords": [],
            "search_hints": "practical guides",
            "recency_hours": 48,
            "expires_at": None,
        }
        mock_articles = [_article("https://example.com/both", "Both Profile And Intent")]
        research_mock = mocker.patch("pipeline.curator.research", return_value=mock_articles)
        mocker.patch("pipeline.load_preference_profile", return_value=profile)
        mocker.patch("pipeline.load_curation_intent", return_value=intent)

        from pipeline import run_curation_pipeline

        run_curation_pipeline(count=1)

        research_mock.assert_called_once()
        call_args = research_mock.call_args
        assert call_args.kwargs["intent"] == intent
        # preferences is 3rd positional arg in pipeline.py
        assert call_args.args[2] == profile


class TestRecencyFiltering:
    def _setup(self, mocker, articles):
        mocker.patch("pipeline.curator.research", return_value=articles)
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)
        from pipeline import run_curation_pipeline

        return run_curation_pipeline

    def test_stale_articles_dropped(self, mocker, tmp_db):
        """실측: 게시된 기사의 87.5%가 30일 초과였다."""
        run = self._setup(
            mocker,
            [
                _article("https://example.com/fresh", "Fresh", age_days=1),
                _article("https://example.com/stale", "Stale", age_days=400),
            ],
        )
        result = run(count=3)

        assert result["raw_count"] == 2
        assert result["fresh_count"] == 1
        assert result["stale_dropped"] == 1
        assert [a["url"] for a in result["articles"]] == ["https://example.com/fresh"]

    def test_all_stale_reports_cause(self, mocker, tmp_db):
        run = self._setup(mocker, [_article("https://example.com/old", "Old", age_days=90)])
        result = run(count=3)

        assert result["articles"] == []
        assert result["stale_dropped"] == 1
        assert result["error"] is None

    def test_undated_article_survives(self, mocker, tmp_db):
        """발행일 미상까지 버리면 0건 문제가 재발한다."""
        undated = _article("https://example.com/undated", "Undated")
        undated.published_at = ""
        run = self._setup(mocker, [undated])
        result = run(count=3)

        assert [a["url"] for a in result["articles"]] == ["https://example.com/undated"]

    def test_stale_pending_reservoir_not_posted(self, mocker, tmp_db):
        """저수지에 남은 기사도 기한이 지나면 게시하지 않는다."""
        from database import upsert_article

        upsert_article(
            {
                "url": "https://example.com/aged-reservoir",
                "title": "Aged",
                "source": "Test",
                "description": "",
                "author": "",
                "image_url": "",
                "published_at": days_ago(60),
                "platform_score": 100.0,
                "keywords": [],
            }
        )
        run = self._setup(mocker, [])
        result = run(count=3)

        assert result["articles"] == []


class TestFeedTopup:
    def _setup(self, mocker, articles, intent=None):
        mocker.patch("pipeline.curator.research", return_value=articles)
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=intent or _INACTIVE_INTENT)
        from pipeline import run_curation_pipeline

        return run_curation_pipeline

    def test_topup_fills_shortfall(self, mocker, tmp_db, no_network_feeds):
        no_network_feeds.return_value = [
            _article("https://hn/1", "HN One"),
            _article("https://hn/2", "HN Two"),
        ]
        run = self._setup(mocker, [_article("https://example.com/only", "Only One")])
        result = run(count=3)

        assert result["feed_topup"] == 1
        assert len(result["articles"]) == 2

    def test_no_topup_when_target_met(self, mocker, tmp_db, no_network_feeds):
        run = self._setup(
            mocker,
            [_article(f"https://example.com/a{i}", f"A{i}") for i in range(3)],
        )
        result = run(count=3)

        assert result["feed_topup"] == 0
        no_network_feeds.assert_not_called()

    def test_topup_recovers_from_curator_exception(self, mocker, tmp_db, no_network_feeds):
        """수집 경로가 하나뿐이라 curator가 죽으면 그날 0건이던 문제를 막는다."""
        no_network_feeds.return_value = [_article("https://hn/rescue", "Rescue")]
        mocker.patch("pipeline.curator.research", side_effect=Exception("API Error"))
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)
        from pipeline import run_curation_pipeline

        result = run_curation_pipeline(count=1)

        assert result["error"] is not None
        assert [a["url"] for a in result["articles"]] == ["https://hn/rescue"]

    def test_topup_inserts_only_shortfall(self, mocker, tmp_db, no_network_feeds):
        """잉여 feed 기사가 저수지에 쌓여 다음 날 랭킹을 왜곡하지 않도록."""
        no_network_feeds.return_value = [_article(f"https://hn/{i}", f"HN {i}") for i in range(6)]
        run = self._setup(mocker, [_article("https://example.com/one", "One")])
        run(count=2)

        from database import get_all_article_urls

        feed_urls = {u for u in get_all_article_urls() if u.startswith("https://hn/")}
        assert len(feed_urls) == 1

    def test_uses_intent_recency_window(self, mocker, tmp_db, no_network_feeds):
        intent = dict(_INACTIVE_INTENT, active=True, summary="tight", recency_hours=24)
        run = self._setup(mocker, [], intent=intent)
        result = run(count=2)

        assert result["max_age_days"] == 1
        assert no_network_feeds.call_args.kwargs["max_age_days"] == 1
