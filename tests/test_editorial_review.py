from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic

from article_quality import VerifiedArticle
from config import CLAUDE_EFFORT, REVIEW_MAX_TOKENS
from crawlers.base import Article
from editorial_review import (
    _reject_reason,
    apply_decisions,
    parse_review_decisions,
    review_articles,
)
from tests.conftest import days_ago


def _verified(*, trusted: bool = False, url: str = "https://unknown.example/game-assets") -> VerifiedArticle:
    article = Article(
        url=url,
        title="AI asset workflow for game client programmers",
        source="Unknown Practitioner",
        description="Original description",
        author="Engineer",
        published_at=days_ago(0),
        platform_score=100.0,
        keywords=["ai game art"],
    )
    return VerifiedArticle(
        article=article,
        canonical_url=article.url,
        language="en",
        published_at=article.published_at,
        excerpt="A detailed workflow with prompts, texture settings, Unreal import steps, and licensing notes. " * 20,
        trusted_source=trusted,
    )


def _response(*, score: int, title_ko: str = "게임 클라이언트 개발자를 위한 AI 에셋 워크플로") -> str:
    return json.dumps(
        [
            {
                "url": "https://unknown.example/game-assets",
                "verdict": "KEEP",
                "quality_score": score,
                "title_ko": title_ko,
                "summary_ko": "프로그래머가 AI로 텍스처를 만들고 품질을 보정한 뒤 게임 엔진에 적용하는 과정을 단계별로 설명합니다.",
                "why_it_matters_ko": "아트 경험이 부족한 클라이언트 개발자도 재현할 수 있는 실전 절차를 제공합니다.",
                "content_type": "game_asset_workflow",
                "engines": ["Unreal", "Unity"],
                "game_client_relevance": 95,
                "keywords": ["ai game art", "texture"],
                "rejection_reason": "",
            }
        ],
        ensure_ascii=False,
    )


def test_apply_decisions_builds_korean_game_asset_brief() -> None:
    candidates = [_verified()]
    decisions = parse_review_decisions(_response(score=88))

    result = apply_decisions(candidates, decisions)

    assert len(result) == 1
    assert result[0].title == "게임 클라이언트 개발자를 위한 AI 에셋 워크플로"
    assert "왜 유용한가" in result[0].description
    assert "원문 제목" in result[0].description
    assert result[0].platform_score == 88.0
    assert "game_asset_workflow" in result[0].keywords
    assert "engine:unreal" in result[0].keywords
    assert "game_client" in result[0].keywords


def test_unknown_source_requires_higher_quality_score() -> None:
    decisions = parse_review_decisions(_response(score=65))

    assert apply_decisions([_verified(trusted=False)], decisions) == []


def test_trusted_source_uses_standard_quality_threshold() -> None:
    decisions = parse_review_decisions(_response(score=65))

    assert len(apply_decisions([_verified(trusted=True)], decisions)) == 1


def test_unknown_source_accepts_solid_practical_article() -> None:
    """루브릭 70-84 구간(실무자가 바로 따라할 수 있는 글)은 미신뢰 소스라도 통과해야 한다.

    임계값 82 시절에는 모델이 KEEP으로 판정한 78점·70점 아티클이 전부 폐기돼
    브리핑이 0건이 됐다.
    """
    decisions = parse_review_decisions(_response(score=70))

    assert len(apply_decisions([_verified(trusted=False)], decisions)) == 1


def test_string_quality_score_is_parsed_as_number() -> None:
    """모델이 점수를 문자열로 내면 이전 구현은 0.0으로 떨어뜨려 후보를 전량 탈락시켰다."""
    payload = json.loads(_response(score=0))
    payload[0]["quality_score"] = "88"

    decisions = parse_review_decisions(json.dumps(payload, ensure_ascii=False))

    assert decisions[0].quality_score == 88.0
    assert len(apply_decisions([_verified(trusted=False)], decisions)) == 1


def test_review_prompt_declares_scoring_scale_and_thresholds() -> None:
    """루브릭 없이 0-100만 요구하면 모델의 임의 스케일과 코드 임계값이 어긋난다."""
    from editorial_review import _QUALITY_THRESHOLD, _UNKNOWN_SOURCE_THRESHOLD, _review_prompt

    prompt = _review_prompt([_verified(trusted=False)])

    assert "SCORING" in prompt
    assert f">= {_UNKNOWN_SOURCE_THRESHOLD:.0f}" in prompt
    assert f">= {_QUALITY_THRESHOLD:.0f}" in prompt
    # 근중복 제거는 remove_near_duplicates가 이미 했으므로 심사에서 또 깎으면 이중 페널티다.
    assert "INDEPENDENTLY" in prompt


def test_korean_editorial_fields_are_required() -> None:
    decisions = parse_review_decisions(_response(score=90, title_ko="AI asset workflow"))

    assert apply_decisions([_verified(trusted=True)], decisions) == []


def test_apply_decisions_collects_rejection_reasons() -> None:
    """0건일 때 원인 규명용. 사유는 모델 응답과 임계값에서 온다."""
    approved_candidate = _verified(trusted=False, url="https://a.example/post")
    low_score_candidate = _verified(trusted=False, url="https://b.example/post")
    paper_candidate = _verified(trusted=True, url="https://c.example/post")
    decisions = parse_review_decisions(
        json.dumps(
            [
                {
                    "url": "https://a.example/post",
                    "verdict": "KEEP",
                    "quality_score": 88,
                    "title_ko": "한글 제목",
                    "summary_ko": "한글 요약입니다.",
                    "why_it_matters_ko": "한글 이유입니다.",
                    "content_type": "ai_programming",
                    "engines": [],
                    "game_client_relevance": 50,
                    "keywords": [],
                    "rejection_reason": "",
                },
                {
                    "url": "https://b.example/post",
                    "verdict": "KEEP",
                    "quality_score": 65,
                    "title_ko": "한글 제목",
                    "summary_ko": "한글 요약입니다.",
                    "why_it_matters_ko": "한글 이유입니다.",
                    "content_type": "ai_programming",
                    "engines": [],
                    "game_client_relevance": 50,
                    "keywords": [],
                    "rejection_reason": "",
                },
                {
                    "url": "https://c.example/post",
                    "verdict": "REJECT",
                    "quality_score": 60,
                    "title_ko": "",
                    "summary_ko": "",
                    "why_it_matters_ko": "",
                    "content_type": "",
                    "engines": [],
                    "game_client_relevance": 0,
                    "keywords": [],
                    "rejection_reason": "논문 형식이라 재현 가능한 절차가 없습니다",
                },
            ],
            ensure_ascii=False,
        )
    )
    reasons: list[str] = []

    approved = apply_decisions([approved_candidate, low_score_candidate, paper_candidate], decisions, reasons)

    assert len(approved) == 1
    assert reasons == ["품질 점수 미달(65<70)", "논문 형식이라 재현 가능한 절차가 없습니다"]


def test_reject_reason_covers_each_gate() -> None:
    from editorial_review import EditorialDecision

    keep_no_korean = EditorialDecision(
        url="u",
        verdict="KEEP",
        quality_score=90,
        title_ko="",
        summary_ko="",
        why_it_matters_ko="",
        content_type="ai_programming",
        engines=(),
        game_client_relevance=0,
        keywords=(),
        rejection_reason="",
    )
    keep_low_score = EditorialDecision(
        url="u",
        verdict="KEEP",
        quality_score=70,
        title_ko="한글",
        summary_ko="한글 요약",
        why_it_matters_ko="한글 이유",
        content_type="ai_programming",
        engines=(),
        game_client_relevance=0,
        keywords=(),
        rejection_reason="",
    )
    keep_bad_type = EditorialDecision(
        url="u",
        verdict="KEEP",
        quality_score=90,
        title_ko="한글",
        summary_ko="한글 요약",
        why_it_matters_ko="한글 이유",
        content_type="model_release",
        engines=(),
        game_client_relevance=0,
        keywords=(),
        rejection_reason="",
    )
    paper = EditorialDecision(
        url="u",
        verdict="REJECT",
        quality_score=50,
        title_ko="",
        summary_ko="",
        why_it_matters_ko="",
        content_type="",
        engines=(),
        game_client_relevance=0,
        keywords=(),
        rejection_reason="",
    )

    assert _reject_reason(None, 82.0) == "심사 결과 없음"
    assert _reject_reason(paper, 75.0) == "사유 미제공"
    assert _reject_reason(keep_low_score, 82.0) == "품질 점수 미달(70<82)"
    assert _reject_reason(keep_bad_type, 75.0) == "분류 부적합(model_release)"
    assert _reject_reason(keep_no_korean, 75.0) == "한국어 필드 누락"


def _message(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=100, output_tokens=100),
    )


def _stream_client(mocker, *messages):
    stream = mocker.MagicMock()
    stream.__enter__ = mocker.MagicMock(return_value=stream)
    stream.get_final_message.side_effect = list(messages)
    client = mocker.MagicMock()
    client.messages.stream.return_value = stream
    mocker.patch("editorial_review.ANTHROPIC_API_KEY", "test-key")
    mocker.patch("editorial_review.anthropic.Anthropic", return_value=client)
    return client


def test_review_request_declares_thinking_budget_and_effort(mocker) -> None:
    """Opus 5는 thinking이 기본 활성이라 예산과 effort를 명시하지 않으면 조용히 잘린다."""
    client = _stream_client(mocker, _message(_response(score=92)))
    mocker.patch("editorial_review.token_tracker.log_token_usage")

    review_articles([_verified(trusted=True)])

    kwargs = client.messages.stream.call_args_list[0].kwargs
    assert kwargs["max_tokens"] == REVIEW_MAX_TOKENS
    assert kwargs["output_config"] == {"effort": CLAUDE_EFFORT}


def test_review_articles_retries_wider_when_truncated(mocker) -> None:
    """검수에는 폴백 경로가 없다. 잘렸다고 폐기하면 그날 브리핑이 0건이 된다."""
    client = _stream_client(
        mocker,
        _message('[{"url":"https://unknown.exam', stop_reason="max_tokens"),
        _message(_response(score=92)),
    )
    usage_log = mocker.patch("editorial_review.token_tracker.log_token_usage")

    result = review_articles([_verified(trusted=True)])

    assert len(result) == 1
    assert client.messages.stream.call_count == 2
    assert client.messages.stream.call_args_list[1].kwargs["max_tokens"] == REVIEW_MAX_TOKENS * 2
    # 잘린 호출도 실제로 과금되므로 사용량은 두 번 다 기록되어야 한다
    assert usage_log.call_count == 2


def test_review_articles_discards_when_retry_is_still_truncated(mocker) -> None:
    client = _stream_client(
        mocker,
        _message("[{broken", stop_reason="max_tokens"),
        _message("[{broken", stop_reason="max_tokens"),
    )
    mocker.patch("editorial_review.token_tracker.log_token_usage")

    assert review_articles([_verified(trusted=True)]) == []
    assert client.messages.stream.call_count == 2


def test_review_articles_returns_empty_when_api_fails(mocker) -> None:
    client = _stream_client(mocker)
    client.messages.stream.side_effect = anthropic.APIConnectionError(request=mocker.MagicMock())
    mocker.patch("editorial_review.token_tracker.log_token_usage")

    assert review_articles([_verified(trusted=True)]) == []


def test_review_articles_fills_report_with_rejection_reasons(mocker) -> None:
    """0건 원인 규명: 후보 수·통과 수·탈락 사유별 건수를 report로 돌려준다."""
    keep_json = json.dumps(
        [
            {
                "url": "https://keep.example/post",
                "verdict": "KEEP",
                "quality_score": 88,
                "title_ko": "한글 제목",
                "summary_ko": "한글 요약입니다.",
                "why_it_matters_ko": "한글 이유입니다.",
                "content_type": "ai_programming",
                "engines": [],
                "game_client_relevance": 50,
                "keywords": [],
                "rejection_reason": "",
            },
            {
                "url": "https://ad.example/post",
                "verdict": "REJECT",
                "quality_score": 40,
                "title_ko": "",
                "summary_ko": "",
                "why_it_matters_ko": "",
                "content_type": "",
                "engines": [],
                "game_client_relevance": 0,
                "keywords": [],
                "rejection_reason": "광고성 홍보 페이지입니다",
            },
        ],
        ensure_ascii=False,
    )
    _stream_client(mocker, _message(keep_json))
    mocker.patch("editorial_review.token_tracker.log_token_usage")
    report: dict = {}

    approved = review_articles(
        [
            _verified(trusted=True, url="https://keep.example/post"),
            _verified(trusted=True, url="https://ad.example/post"),
        ],
        report=report,
    )

    assert len(approved) == 1
    assert report == {
        "candidates": 2,
        "kept": 1,
        "reasons": {"광고성 홍보 페이지입니다": 1},
    }
