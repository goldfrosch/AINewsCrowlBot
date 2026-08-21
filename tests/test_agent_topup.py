"""
agents/news_curation_agent.run() — 수량 보장 루프

기존 구현은 검색을 1회만 하고 결과를 그대로 반환했다. 중복이나 응답 절단으로
후보가 사라지면 그날 브리핑이 1건 또는 0건이 됐다(실측 45일 중 13일 0건).
"""

import claude_search
import database as db
from agents import news_curation_agent as agent
from config import TOPUP_MAX_ROUNDS
from tests.conftest import days_ago


def _outcome(*urls, age_days: int = 1):
    return claude_search.SearchOutcome(
        articles=[
            {
                "url": url,
                "title": f"Title {url}",
                "source": "TestSource",
                "description": "d",
                "published_at": days_ago(age_days),
                "curator_reason": "r",
                "keywords": ["llm"],
            }
            for url in urls
        ],
        stop_reason="end_turn",
    )


def _run(mocker, outcomes, target_count=3):
    mocker.patch.object(agent, "ANTHROPIC_API_KEY", "test-key")
    mocker.patch("anthropic.Anthropic", return_value=mocker.MagicMock())
    search = mocker.patch.object(claude_search, "search_articles", side_effect=list(outcomes))
    articles = agent.run(target_count=target_count)
    return articles, search


def _prompt_of(search, index: int) -> str:
    return search.call_args_list[index].kwargs["prompt"]


class TestOverfetch:
    def test_requests_more_than_target(self, mocker, tmp_db):
        """3개가 필요하면 12개를 요청해 중복·기한초과 손실을 흡수한다."""
        _, search = _run(mocker, [_outcome(*[f"https://x/{i}" for i in range(12)])])
        assert "Find 12 high-quality" in _prompt_of(search, 0)

    def test_overfetch_target_is_clamped(self):
        assert agent._overfetch_target(1) == 8  # OVERFETCH_MIN
        assert agent._overfetch_target(3) == 12
        assert agent._overfetch_target(10) == 24  # OVERFETCH_MAX


class TestTopupLoop:
    def test_single_round_when_target_met(self, mocker, tmp_db):
        articles, search = _run(mocker, [_outcome("https://a/1", "https://a/2", "https://a/3")])
        assert len(articles) == 3
        assert search.call_count == 1

    def test_retries_when_short(self, mocker, tmp_db):
        articles, search = _run(mocker, [_outcome("https://a/1"), _outcome("https://a/2"), _outcome("https://a/3")])
        assert len(articles) == 3
        assert search.call_count == 1 + TOPUP_MAX_ROUNDS

    def test_stops_as_soon_as_target_met(self, mocker, tmp_db):
        articles, search = _run(mocker, [_outcome("https://a/1"), _outcome("https://a/2", "https://a/3")])
        assert len(articles) == 3
        assert search.call_count == 2

    def test_empty_first_round_still_retries(self, mocker, tmp_db):
        """예전에는 첫 검색이 빈손이면 그대로 0건이었다."""
        articles, search = _run(mocker, [_outcome(), _outcome("https://a/1", "https://a/2", "https://a/3")])
        assert len(articles) == 3
        assert search.call_count == 2

    def test_all_rounds_empty_returns_empty(self, mocker, tmp_db):
        articles, search = _run(mocker, [_outcome(), _outcome(), _outcome()])
        assert articles == []
        assert search.call_count == 1 + TOPUP_MAX_ROUNDS

    def test_retry_round_prompt_is_marked(self, mocker, tmp_db):
        _, search = _run(mocker, [_outcome("https://a/1"), _outcome("https://a/2", "https://a/3")])
        assert "RETRY ROUND" not in _prompt_of(search, 0)
        assert "RETRY ROUND 1" in _prompt_of(search, 1)

    def test_topics_rotate_between_rounds(self, mocker, tmp_db):
        """같은 프롬프트로 재검색하면 같은 결과가 온다 → 토픽 순서를 바꾼다."""
        _, search = _run(mocker, [_outcome("https://a/1"), _outcome("https://a/2", "https://a/3")])
        first_topics = _prompt_of(search, 0).split("Rules:")[0]
        second_topics = _prompt_of(search, 1).split("Rules:")[0]
        assert first_topics != second_topics


class TestFiltering:
    def test_urls_already_in_db_are_dropped(self, mocker, tmp_db):
        """upsert가 조용히 False를 반환해 사라지던 기사를 미리 걸러낸다."""
        db.upsert_article(
            {
                "url": "https://a/dup",
                "title": "Existing",
                "source": "S",
                "description": "",
                "author": "",
                "image_url": "",
                "published_at": days_ago(1),
                "platform_score": 100.0,
                "keywords": [],
            }
        )
        articles, _ = _run(
            mocker,
            [_outcome("https://a/dup", "https://a/new"), _outcome("https://a/new2"), _outcome("https://a/new3")],
        )
        urls = [a["url"] for a in articles]
        assert "https://a/dup" not in urls
        assert "https://a/new" in urls

    def test_stale_articles_dropped(self, mocker, tmp_db):
        articles, _ = _run(
            mocker,
            [
                claude_search.SearchOutcome(
                    articles=[
                        *_outcome("https://a/fresh", age_days=1).articles,
                        *_outcome("https://a/ancient", age_days=400).articles,
                    ]
                ),
                _outcome("https://a/x"),
                _outcome("https://a/y"),
            ],
        )
        urls = [a["url"] for a in articles]
        assert "https://a/fresh" in urls
        assert "https://a/ancient" not in urls

    def test_articles_without_url_or_title_dropped(self, mocker, tmp_db):
        outcome = claude_search.SearchOutcome(
            articles=[
                {"url": "", "title": "No URL"},
                {"url": "https://a/no-title", "title": ""},
                {"url": "https://a/ok", "title": "OK", "published_at": days_ago(1)},
            ]
        )
        articles, _ = _run(mocker, [outcome, _outcome("https://a/2"), _outcome("https://a/3")])
        assert [a["url"] for a in articles if a["url"] == "https://a/ok"]
        assert all(a["url"] and a["title"] for a in articles)

    def test_duplicates_within_run_deduplicated(self, mocker, tmp_db):
        articles, _ = _run(mocker, [_outcome("https://a/1", "https://a/1", "https://a/1")], target_count=1)
        assert len(articles) == 1

    def test_returns_newest_first(self, mocker, tmp_db):
        outcome = claude_search.SearchOutcome(
            articles=[
                *_outcome("https://a/older", age_days=5).articles,
                *_outcome("https://a/newer", age_days=0).articles,
            ]
        )
        articles, _ = _run(mocker, [outcome, _outcome("https://a/x"), _outcome("https://a/y")])
        assert articles[0]["url"] == "https://a/newer"

    def test_keywords_are_preserved(self, mocker, tmp_db):
        """에이전트 스키마에 keywords가 없어 기사 109건 중 101건이 키워드 0개였다."""
        articles, _ = _run(mocker, [_outcome("https://a/1", "https://a/2", "https://a/3")])
        assert all(a["keywords"] == ["llm"] for a in articles)
