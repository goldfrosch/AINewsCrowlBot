"""crawlers/feed_pool.py — 두 번째 수집 경로 (단일 실패점 제거)"""

from config import FEED_MAX_PER_SOURCE
from crawlers import feed_pool
from crawlers.base import Article
from tests.conftest import days_ago


def _article(url: str, age_days: int = 1, source: str = "HackerNews", title: str | None = None) -> Article:
    return Article(
        url=url,
        title=title if title is not None else f"Building an llm agent at {url}",
        source=source,
        description="",
        published_at=days_ago(age_days),
        platform_score=100.0,
    )


def _patch_producers(mocker, hn=None, rss=None):
    mocker.patch.object(
        feed_pool,
        "_producers",
        return_value=(lambda days: list(hn or []), lambda days: list(rss or [])),
    )


class TestRelevance:
    def test_ai_keyword_scores(self):
        assert feed_pool.relevance(_article("https://x", title="Using an llm agent with rag")) >= 2

    def test_game_dev_keywords_weighted_higher(self):
        ai_only = feed_pool.relevance(_article("https://x", title="llm agent"))
        game = feed_pool.relevance(_article("https://y", title="llm agent for ai game development"))
        assert game > ai_only

    def test_unrelated_scores_zero(self):
        """실측 회귀: '항만 크레인 스케줄링' 논문이 게시 후보로 올라왔다."""
        unrelated = _article(
            "https://arxiv/1",
            title="Robust Metaheuristics under Uncertainty for Berth Allocation and Quay Crane Assignment",
        )
        assert feed_pool.relevance(unrelated) == 0


class TestCollect:
    def test_merges_both_producers(self, mocker):
        _patch_producers(
            mocker,
            hn=[_article("https://hn/1")],
            rss=[_article("https://rss/1", source="VentureBeat AI")],
        )
        result = feed_pool.collect(5)
        assert {a.url for a in result} == {"https://hn/1", "https://rss/1"}

    def test_respects_count(self, mocker):
        _patch_producers(mocker, hn=[_article(f"https://hn/{i}", source=f"Source{i}") for i in range(10)])
        assert len(feed_pool.collect(3)) == 3

    def test_zero_count_returns_empty(self, mocker):
        _patch_producers(mocker, hn=[_article("https://hn/1")])
        assert feed_pool.collect(0) == []

    def test_excludes_known_urls(self, mocker):
        _patch_producers(mocker, hn=[_article("https://hn/dup"), _article("https://hn/new")])
        result = feed_pool.collect(5, exclude_urls={"https://hn/dup"})
        assert [a.url for a in result] == ["https://hn/new"]

    def test_drops_stale_candidates(self, mocker):
        _patch_producers(mocker, hn=[_article("https://hn/old", age_days=40), _article("https://hn/fresh")])
        result = feed_pool.collect(5, max_age_days=7)
        assert [a.url for a in result] == ["https://hn/fresh"]

    def test_drops_irrelevant_candidates(self, mocker):
        _patch_producers(
            mocker,
            rss=[
                _article("https://arxiv/noise", source="ArXiv cs.AI", title="Berth Allocation and Quay Crane Review"),
                _article("https://arxiv/good", source="ArXiv cs.AI", title="Multi-agent llm agent orchestration"),
            ],
        )
        result = feed_pool.collect(5)
        assert [a.url for a in result] == ["https://arxiv/good"]

    def test_deduplicates_across_producers(self, mocker):
        _patch_producers(mocker, hn=[_article("https://same/1")], rss=[_article("https://same/1")])
        assert len(feed_pool.collect(5)) == 1

    def test_higher_relevance_wins_over_recency(self, mocker):
        """날짜만으로 정렬해 arXiv가 상위를 독식하던 회귀를 막는다."""
        _patch_producers(
            mocker,
            rss=[
                _article("https://rss/today-weak", age_days=0, source="Medium AI", title="an llm paper"),
                _article(
                    "https://rss/older-strong",
                    age_days=4,
                    source="Game Developer",
                    title="ai game development with ai game art and llm agent tooling",
                ),
            ],
        )
        result = feed_pool.collect(2)
        assert result[0].url == "https://rss/older-strong"

    def test_community_tier_beats_academic(self, mocker):
        """HN 후보 16개가 키워드 밀집 arXiv 초록에 전부 밀리던 회귀를 막는다."""
        dense_abstract = "llm agent rag embedding fine-tuning mcp agentic langchain prompt engineering " * 3
        _patch_producers(
            mocker,
            hn=[_article("https://hn/story", source="HackerNews", title="A short llm agent post")],
            rss=[
                Article(
                    url="https://arxiv/dense",
                    title="Keyword dense llm agent rag mcp agentic paper",
                    source="ArXiv cs.AI",
                    description=dense_abstract,
                    published_at=days_ago(0),
                    platform_score=100.0,
                )
            ],
        )
        result = feed_pool.collect(2)
        assert result[0].source == "HackerNews"

    def test_editorial_tier_beats_academic(self, mocker):
        _patch_producers(
            mocker,
            rss=[
                _article("https://arxiv/1", source="ArXiv cs.AI", title="llm agent rag mcp agentic paper"),
                _article("https://blog/1", source="80 Level", title="ai game art with llm"),
            ],
        )
        result = feed_pool.collect(2)
        assert result[0].source == "80 Level"

    def test_long_description_does_not_outrank_title_match(self, mocker):
        """설명 매치 상한이 없으면 초록 스터핑으로 순위를 뒤집을 수 있다."""
        stuffed = Article(
            url="https://x/stuffed",
            title="Unrelated title about logistics",
            source="Medium AI",
            description="llm agent rag mcp agentic langchain embedding " * 10,
            published_at=days_ago(0),
            platform_score=100.0,
        )
        titled = _article("https://x/titled", source="Medium AI", title="llm agent rag mcp guide")
        _patch_producers(mocker, rss=[stuffed, titled])
        result = feed_pool.collect(2)
        assert result[0].url == "https://x/titled"

    def test_fresher_wins_within_same_relevance_and_source(self, mocker):
        _patch_producers(
            mocker, hn=[_article("https://hn/older", age_days=5), _article("https://hn/newer", age_days=0)]
        )
        result = feed_pool.collect(2)
        assert [a.url for a in result] == ["https://hn/newer", "https://hn/older"]

    def test_per_source_cap_applied(self, mocker):
        """한 소스가 후보를 독식하지 못하게 한다."""
        _patch_producers(
            mocker,
            rss=[_article(f"https://arxiv/{i}", source="ArXiv cs.AI") for i in range(6)],
            hn=[_article("https://hn/1", source="HackerNews")],
        )
        result = feed_pool.collect(3)
        assert sum(1 for a in result if a.source == "ArXiv cs.AI") == FEED_MAX_PER_SOURCE
        assert any(a.source == "HackerNews" for a in result)

    def test_cap_relaxed_when_target_unreachable(self, mocker):
        """0건 방지가 상한보다 우선한다."""
        _patch_producers(mocker, rss=[_article(f"https://arxiv/{i}", source="ArXiv cs.AI") for i in range(5)])
        assert len(feed_pool.collect(4)) == 4

    def test_one_producer_failure_does_not_kill_pool(self, mocker):
        def boom(days):
            raise RuntimeError("network down")

        mocker.patch.object(
            feed_pool,
            "_producers",
            return_value=(boom, lambda days: [_article("https://rss/survivor", source="ArXiv cs.AI")]),
        )
        result = feed_pool.collect(5)
        assert [a.url for a in result] == ["https://rss/survivor"]

    def test_disabled_returns_empty(self, mocker):
        mocker.patch("crawlers.feed_pool.FEED_POOL_ENABLED", False)
        _patch_producers(mocker, hn=[_article("https://hn/1")])
        assert feed_pool.collect(5) == []
