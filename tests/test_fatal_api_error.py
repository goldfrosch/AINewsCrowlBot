"""복구 불가 API 오류(크레딧 소진·인증 실패) 서킷 브레이커

실측(2026-09-23): Anthropic 크레딧이 떨어지자 파이프라인이 완화 패스 3회 ×
톱업 2라운드 × 필라 3개 + 폴백 = 실행당 18회 이상을 같은 실패로 낭비하고,
사용자에게는 "게시 대상 0개"만 남겼다. 진짜 원인은 로그 깊숙이 묻혔다.
"""

from __future__ import annotations

import threading

import anthropic
import pytest

import claude_search
import pipeline
from agents import news_curation_agent as agent
from crawlers.base import Article
from tests.conftest import days_ago

_CREDIT_MESSAGE = (
    "400: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'Your credit balance is too low to access the Anthropic API.'}}"
)
_INACTIVE_INTENT = {"active": False, "recency_hours": None}


class TestFatalDetection:
    @pytest.mark.parametrize(
        "message",
        [
            _CREDIT_MESSAGE,
            "authentication_error: invalid api key",
            "permission_error",
            "invalid x-api-key",
        ],
    )
    def test_detects_non_retryable_errors(self, message: str) -> None:
        assert claude_search.is_fatal_error(message)

    @pytest.mark.parametrize("message", ["529: overloaded_error", "500: internal server error", ""])
    def test_transient_errors_are_not_fatal(self, message: str) -> None:
        assert not claude_search.is_fatal_error(message)

    def test_outcome_exposes_fatal_flag(self) -> None:
        assert claude_search.SearchOutcome(error=_CREDIT_MESSAGE).fatal
        assert not claude_search.SearchOutcome(error="529: overloaded").fatal
        assert not claude_search.SearchOutcome(articles=[{"url": "x"}]).fatal

    def test_status_200_with_error_body_is_still_fatal(self) -> None:
        """스트리밍 경로는 크레딧 소진을 status 200 + 에러 바디로 돌려준다."""
        assert claude_search.SearchOutcome(error=f"200: {_CREDIT_MESSAGE}").fatal

    def test_authentication_error_is_not_retried(self, mocker) -> None:
        invoke = mocker.patch.object(
            claude_search,
            "_invoke",
            side_effect=anthropic.AuthenticationError(
                "bad key", response=mocker.MagicMock(status_code=401, headers={}), body=None
            ),
        )

        outcome = claude_search.search_articles(mocker.MagicMock(), prompt="p", system_blocks=[], caller="t")

        assert outcome.fatal
        assert invoke.call_count == 1


class TestAgentCircuitBreaker:
    def test_agent_stops_after_fatal_error(self, mocker, tmp_db) -> None:
        mocker.patch.object(agent, "ANTHROPIC_API_KEY", "test-key")
        mocker.patch("anthropic.Anthropic", return_value=mocker.MagicMock())
        calls: list[str] = []
        lock = threading.Lock()

        def fail(*_args, **kwargs):
            with lock:
                calls.append(kwargs["caller"])
            return claude_search.SearchOutcome(error=_CREDIT_MESSAGE)

        mocker.patch.object(claude_search, "search_articles", side_effect=fail)

        with pytest.raises(claude_search.FatalSearchError):
            agent.run(target_count=3)

        # 첫 라운드(필라 수만큼)에서 멈춰야 한다 — 톱업 라운드는 돌지 않는다.
        assert len(calls) == len(agent._plan_pillars(None))


class TestPipelineCircuitBreaker:
    @pytest.fixture(autouse=True)
    def _isolate(self, mocker):
        mocker.patch("pipeline.load_preference_profile", return_value=None)
        mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)
        self.feeds = mocker.patch("pipeline.feed_pool.collect", return_value=[])

    def test_remaining_passes_are_skipped(self, mocker, tmp_db) -> None:
        research = mocker.patch(
            "pipeline.curator.research",
            side_effect=claude_search.FatalSearchError(_CREDIT_MESSAGE),
        )

        result = pipeline.run_curation_pipeline(count=3)

        assert research.call_count == 1
        assert result["fatal_api_error"] is True
        assert "복구 불가" in result["error"]
        assert result["articles"] == []

    def test_feed_topup_is_skipped_when_account_is_blocked(self, mocker, tmp_db) -> None:
        """feed 후보도 같은 키로 편집 심사를 받으므로 부를 이유가 없다."""
        mocker.patch("pipeline.curator.research", side_effect=claude_search.FatalSearchError(_CREDIT_MESSAGE))

        pipeline.run_curation_pipeline(count=3)

        self.feeds.assert_not_called()

    def test_reservoir_still_serves_during_outage(self, mocker, tmp_db) -> None:
        """계정이 막혀도 저수지에 남은 검수 완료 기사는 나가야 한다."""
        import database as db

        db.upsert_article(
            Article(
                url="https://a/leftover",
                title="Leftover",
                source="S",
                published_at=days_ago(1),
                platform_score=90.0,
                keywords=["ai_programming"],
            ).to_dict()
        )
        mocker.patch("pipeline.curator.research", side_effect=claude_search.FatalSearchError(_CREDIT_MESSAGE))

        result = pipeline.run_curation_pipeline(count=3)

        assert [a["url"] for a in result["articles"]] == ["https://a/leftover"]
        assert result["fatal_api_error"] is True

    def test_transient_error_still_tries_every_pass(self, mocker, tmp_db) -> None:
        """일시 오류까지 중단하면 복구 가능한 날에도 0건이 된다."""
        research = mocker.patch("pipeline.curator.research", side_effect=RuntimeError("529 overloaded"))

        result = pipeline.run_curation_pipeline(count=3)

        assert research.call_count == len(pipeline.RECENCY_RELAXATION_DAYS)
        assert result["fatal_api_error"] is False
