"""pipeline.py 통합 테스트"""

from crawlers.base import Article

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


class TestRunCurationPipeline:
    def test_full_pipeline_mocked(self, mocker, tmp_db):
        mock_articles = [
            Article(
                url="https://example.com/pipeline-1",
                title="Pipeline Test 1",
                source="TestSource",
                description="Test description",
                author="Author",
                published_at="2026-04-01",
                platform_score=100.0,
                keywords=["llm"],
            ),
            Article(
                url="https://example.com/pipeline-2",
                title="Pipeline Test 2",
                source="TestSource",
                description="Another test",
                author="Author",
                published_at="2026-04-02",
                platform_score=200.0,
                keywords=["rag"],
            ),
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
            Article(
                url=sample_articles[0]["url"],  # already in DB
                title=sample_articles[0]["title"],
                source=sample_articles[0]["source"],
                description="dup",
                author="",
                published_at="",
                platform_score=100.0,
                keywords=[],
            ),
            Article(
                url="https://example.com/new-one",
                title="New Article",
                source="Test",
                description="new",
                author="",
                published_at="",
                platform_score=100.0,
                keywords=[],
            ),
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
            Article(
                url=f"https://example.com/rank-{i}",
                title=f"Article {i}",
                source="Test",
                description="",
                author="",
                published_at="",
                platform_score=float(i * 100),
                keywords=[],
            )
            for i in range(5)
        ]
        mocker.patch("pipeline.curator.research", return_value=mock_articles)
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)

        from pipeline import run_curation_pipeline

        result = run_curation_pipeline(count=3)

        assert len(result["articles"]) == 3
        scores = [a["final_score"] for a in result["articles"]]
        assert scores == sorted(scores, reverse=True)

    def test_count_limits_output(self, mocker, tmp_db):
        """count보다 많은 기사가 와도 count개만 반환."""
        mock_articles = [
            Article(
                url=f"https://example.com/limit-{i}",
                title=f"Article {i}",
                source="Test",
                description="",
                author="",
                published_at="",
                platform_score=100.0,
                keywords=[],
            )
            for i in range(10)
        ]
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
        mock_articles = [
            Article(
                url="https://example.com/intended",
                title="Intent Article",
                source="Test",
                description="",
                author="",
                published_at="",
                platform_score=100.0,
                keywords=[],
            )
        ]
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
        mock_articles = [
            Article(
                url="https://example.com/both",
                title="Both Profile And Intent",
                source="Test",
                description="",
                author="",
                published_at="",
                platform_score=100.0,
                keywords=[],
            )
        ]
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
