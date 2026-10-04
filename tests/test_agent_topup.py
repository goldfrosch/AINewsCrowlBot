"""
agents/news_curation_agent.run() — 필라 병렬 검색 + 수량 보장 루프

기존 구현은 검색을 1회만 하고 결과를 그대로 반환했다. 중복이나 응답 절단으로
후보가 사라지면 그날 브리핑이 1건 또는 0건이 됐다(실측 45일 중 13일 0건).
지금은 주제군(필라)마다 호출을 분리해 동시에 보내고, 오버페치 목표에 못 미치면
토픽을 회전시켜 한 라운드 더 검색한다.
"""

import threading

import pytest

import claude_search
import database as db
from agents import news_curation_agent as agent
from config import OVERFETCH_MAX, OVERFETCH_MIN, SEARCH_HARVEST_TARGET, TOPUP_MAX_ROUNDS
from tests.conftest import days_ago

PILLARS = len(agent._plan_pillars(None))


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


def _round(*urls, age_days: int = 1):
    """한 라운드 분량 — 첫 필라가 전부 반환하고 나머지 필라는 빈손."""
    return [_outcome(*urls, age_days=age_days), *[_outcome() for _ in range(PILLARS - 1)]]


def _run(mocker, outcomes, target_count=3, **kwargs):
    """outcomes를 순서대로 돌려주고, 소진되면 빈 결과를 계속 반환한다."""
    mocker.patch.object(agent, "ANTHROPIC_API_KEY", "test-key")
    mocker.patch("anthropic.Anthropic", return_value=mocker.MagicMock())

    queue = list(outcomes)
    lock = threading.Lock()

    def next_outcome(*_args, **_kwargs):
        with lock:
            return queue.pop(0) if queue else _outcome()

    search = mocker.patch.object(claude_search, "search_articles", side_effect=next_outcome)
    articles = agent.run(target_count=target_count, **kwargs)
    return articles, search


def _prompts(search) -> list[str]:
    return [call.kwargs["prompt"] for call in search.call_args_list]


class TestPillarFanout:
    def test_one_call_per_pillar_each_round(self, mocker, tmp_db):
        """필라를 한 프롬프트에 합치면 모델이 쉬운 주제로 쏠린다 → 호출을 쪼갠다."""
        _, search = _run(mocker, _round(*[f"https://x/{i}" for i in range(40)]))
        assert search.call_count == PILLARS

    def test_each_pillar_prompt_is_scoped(self, mocker, tmp_db):
        _, search = _run(mocker, _round(*[f"https://x/{i}" for i in range(40)]))
        callers = {call.kwargs["caller"] for call in search.call_args_list}
        assert callers == {f"agent_find_{key}" for key in agent._plan_pillars(None)}

    def test_quota_is_split_across_pillars(self, mocker, tmp_db):
        _, search = _run(mocker, _round(*[f"https://x/{i}" for i in range(40)]))
        requested = [
            int(line.split()[1]) for p in _prompts(search) for line in p.splitlines() if line.startswith("Find ")
        ]
        assert len(requested) == PILLARS
        assert sum(requested) >= agent._overfetch_target(3)


class TestOverfetch:
    def test_requests_more_than_target(self, mocker, tmp_db):
        """3개가 필요하면 12개를 요청해 중복·본문검증·심사 손실을 흡수한다."""
        assert agent._overfetch_target(3) == 12

    def test_overfetch_target_is_clamped(self):
        assert agent._overfetch_target(1) == OVERFETCH_MIN
        assert agent._overfetch_target(3) == 12
        assert agent._overfetch_target(30) == OVERFETCH_MAX

    def test_every_pillar_gets_a_floor(self):
        quota = agent._split_by_weight(3, ["ai_practice", "ai_game", "graphics_3d"])
        assert min(quota.values()) >= 2

    def test_weight_order_is_respected(self):
        quota = agent._split_by_weight(40, ["ai_practice", "ai_game", "graphics_3d"])
        assert quota["ai_practice"] > quota["ai_game"] > quota["graphics_3d"]

    def test_request_size_does_not_shrink_with_shortfall(self, mocker, tmp_db):
        """검색 비용은 검색 횟수로 정해진다. 부족분이 1건이라고 요청을 줄이면 같은 돈으로 덜 받아 온다."""
        _, search = _run(mocker, _round(*[f"https://x/{i}" for i in range(40)]), target_count=1)
        requested = [
            int(line.split()[1]) for p in _prompts(search) for line in p.splitlines() if line.startswith("Find ")
        ]
        assert sum(requested) >= SEARCH_HARVEST_TARGET


class TestTopupLoop:
    def test_single_round_when_overfetch_target_met(self, mocker, tmp_db):
        urls = [f"https://a/{i}" for i in range(agent._overfetch_target(3))]
        articles, search = _run(mocker, _round(*urls))
        assert len(articles) == len(urls)
        assert search.call_count == PILLARS

    def test_retries_when_short(self, mocker, tmp_db):
        articles, search = _run(mocker, [*_round("https://a/1"), *_round("https://a/2")])
        assert {a["url"] for a in articles} == {"https://a/1", "https://a/2"}
        assert search.call_count == PILLARS * (1 + TOPUP_MAX_ROUNDS)

    def test_empty_first_round_still_retries(self, mocker, tmp_db):
        """예전에는 첫 검색이 빈손이면 그대로 0건이었다."""
        articles, search = _run(mocker, [*_round(), *_round("https://a/1", "https://a/2", "https://a/3")])
        assert len(articles) == 3
        assert search.call_count == PILLARS * (1 + TOPUP_MAX_ROUNDS)

    def test_all_rounds_empty_returns_empty(self, mocker, tmp_db):
        articles, search = _run(mocker, [*_round(), *_round()])
        assert articles == []
        assert search.call_count == PILLARS * (1 + TOPUP_MAX_ROUNDS)

    def test_should_stop_skips_topup_round(self, mocker, tmp_db):
        """실행당 비용 상한·배치 마감에 걸리면 후보가 모자라도 톱업 라운드를 시작하지 않는다."""
        session = agent.SearchSession(should_stop=lambda: "실행당 비용 상한 도달")
        articles, search = _run(mocker, [*_round("https://a/1"), *_round("https://a/2")], session=session)
        assert [a["url"] for a in articles] == ["https://a/1"]
        assert search.call_count == PILLARS

    def test_retry_round_prompt_is_marked(self, mocker, tmp_db):
        _, search = _run(mocker, [*_round("https://a/1"), *_round("https://a/2")])
        prompts = _prompts(search)
        assert all("RETRY ROUND" not in p for p in prompts[:PILLARS])
        assert all("RETRY ROUND 1" in p for p in prompts[PILLARS:])

    def test_topics_rotate_between_rounds(self, mocker, tmp_db):
        """같은 프롬프트로 재검색하면 같은 결과가 온다 → 토픽 순서를 바꾼다."""
        _, search = _run(mocker, [*_round("https://a/1"), *_round("https://a/2")])
        prompts = _prompts(search)
        first = prompts[0].split("Rules:")[0]
        second = prompts[PILLARS].split("Rules:")[0]
        assert first != second


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
        articles, _ = _run(mocker, _round("https://a/dup", "https://a/new"))
        urls = [a["url"] for a in articles]
        assert "https://a/dup" not in urls
        assert "https://a/new" in urls

    def test_url_variants_of_stored_article_are_dropped(self, mocker, tmp_db):
        """저장 키는 정규형이다. 후행 슬래시·추적 파라미터만 다른 같은 글을 새 글로 보면 안 된다."""
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
        articles, _ = _run(mocker, _round("https://a/dup/?utm_source=feed", "https://a/new"))
        assert [a["url"] for a in articles] == ["https://a/new"]

    def test_stale_articles_dropped(self, mocker, tmp_db):
        outcome = claude_search.SearchOutcome(
            articles=[
                *_outcome("https://a/fresh", age_days=1).articles,
                *_outcome("https://a/ancient", age_days=400).articles,
            ]
        )
        articles, _ = _run(mocker, [outcome, *[_outcome() for _ in range(PILLARS - 1)]])
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
        articles, _ = _run(mocker, [outcome, *[_outcome() for _ in range(PILLARS - 1)]])
        assert [a["url"] for a in articles if a["url"] == "https://a/ok"]
        assert all(a["url"] and a["title"] for a in articles)

    def test_duplicates_within_run_deduplicated(self, mocker, tmp_db):
        articles, _ = _run(mocker, _round("https://a/1", "https://a/1", "https://a/1"), target_count=1)
        assert len(articles) == 1

    def test_returns_newest_first(self, mocker, tmp_db):
        outcome = claude_search.SearchOutcome(
            articles=[
                *_outcome("https://a/older", age_days=5).articles,
                *_outcome("https://a/newer", age_days=0).articles,
            ]
        )
        articles, _ = _run(mocker, [outcome, *[_outcome() for _ in range(PILLARS - 1)]])
        assert articles[0]["url"] == "https://a/newer"

    def test_keywords_are_preserved(self, mocker, tmp_db):
        """에이전트 스키마에 keywords가 없어 기사 109건 중 101건이 키워드 0개였다."""
        articles, _ = _run(mocker, _round("https://a/1", "https://a/2", "https://a/3"))
        assert all(a["keywords"] == ["llm"] for a in articles)

    def test_pillar_is_tagged_on_each_article(self, mocker, tmp_db):
        """필라를 잃으면 하류에서 기사별 신선도 창을 복원할 수 없다."""
        articles, _ = _run(mocker, _round("https://a/1"))
        assert articles[0]["pillar"] in agent._plan_pillars(None)


class TestSearchSession:
    """완화 패스는 같은 세션으로 run()을 다시 부른다. 앞 패스를 그대로 반복하면 안 된다."""

    @staticmethod
    def _first_pass(mocker, session):
        _run(mocker, _round(*[f"https://a/{i}" for i in range(40)]), session=session, max_age_days=14)

    def test_only_pillars_with_widened_window_are_searched_again(self, mocker, tmp_db):
        session = agent.SearchSession()
        self._first_pass(mocker, session)

        _, search = _run(mocker, [], session=session, max_age_days=30)

        callers = {call.kwargs["caller"] for call in search.call_args_list}
        assert callers
        assert all("ai_practice" in caller for caller in callers)

    def test_round_numbers_continue_across_passes(self, mocker, tmp_db):
        """라운드가 0으로 돌아가면 토픽 회전과 재시도 문구가 사라져 1패스 쿼리가 반복된다."""
        session = agent.SearchSession()
        self._first_pass(mocker, session)

        _, search = _run(mocker, [], session=session, max_age_days=30)

        assert all("RETRY ROUND" in prompt for prompt in _prompts(search))

    def test_no_search_when_no_window_widened(self, mocker, tmp_db):
        session = agent.SearchSession()
        self._first_pass(mocker, session)

        articles, search = _run(mocker, [], session=session, max_age_days=14)

        assert articles == []
        search.assert_not_called()

    def test_urls_already_sent_to_review_are_not_collected_again(self, mocker, tmp_db):
        session = agent.SearchSession(seen_urls={"https://a/seen"})

        articles, _ = _run(mocker, _round("https://a/seen/", "https://a/new"), session=session)

        assert [a["url"] for a in articles] == ["https://a/new"]

    def test_all_failed_searches_raise_so_caller_can_fall_back(self, mocker, tmp_db):
        """전부 오류인데 빈 목록을 돌려주면 '새 글 없음'과 구분되지 않아 폴백 기회가 사라진다."""
        failures = [claude_search.SearchOutcome(error="529: overloaded") for _ in range(PILLARS * 2)]

        with pytest.raises(RuntimeError):
            _run(mocker, failures)
