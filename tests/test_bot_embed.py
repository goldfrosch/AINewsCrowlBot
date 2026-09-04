from __future__ import annotations

from bot import _failure_message, _make_embed, _stages_line, _summary_message
from config import CLAUDE_MODEL
from tests.conftest import days_ago


def test_make_embed_renders_korean_brief_metadata() -> None:
    article = {
        "title": "언리얼 프로젝트에 적용하는 AI 에셋 워크플로",
        "url": "https://example.com/original",
        "source": "Engineering Blog",
        "description": (
            "AI로 텍스처를 만든 뒤 품질을 보정하고 엔진에 적용하는 과정을 설명합니다.\n\n"
            "**왜 유용한가**: 프로그래머가 재현할 수 있는 설정과 예제가 있습니다.\n\n"
            "원문 제목: Practical AI asset workflow"
        ),
        "author": "Engineer",
        "published_at": days_ago(0),
        "platform_score": 91.0,
        "keywords": ["game_asset_workflow", "engine:unreal", "engine:unity"],
    }

    embed = _make_embed(article, is_ai_curated=True)

    assert "언리얼 프로젝트" in embed.title
    assert "원문 제목" not in embed.description
    fields = {field.name: field.value for field in embed.fields}
    assert fields["원문 제목"] == "Practical AI asset workflow"
    assert fields["분류"] == "게임 에셋 워크플로"
    assert fields["엔진"] == "Unreal · Unity"
    assert fields["품질 점수"] == "91"


def test_make_embed_bounds_source_field() -> None:
    article = {
        "title": "검수된 AI 프로그래밍 가이드",
        "url": "https://example.com/guide",
        "source": "S" * 2_000,
        "description": "재현 가능한 실전 가이드입니다.",
        "platform_score": 90.0,
        "keywords": ["ai_programming"],
    }

    embed = _make_embed(article, is_ai_curated=True)

    source = next(field.value for field in embed.fields if field.name == "출처")
    assert len(source) <= 200


def _result(stages: dict) -> dict:
    return {
        "articles": [],
        "raw_count": 5,
        "fresh_count": 5,
        "stale_dropped": 0,
        "quality_dropped": 5,
        "new_count": 0,
        "feed_topup": 0,
        "max_age_days": 7,
        "error": None,
        "stages": stages,
    }


def test_failure_message_names_body_verification_stage() -> None:
    """수집은 됐지만 본문 검증 0통과면 fetch·언어 문제로 심사 탈락과 구분해야 한다."""
    result = _result({"verify_attempted": 5, "verify_passed": 0})

    message = _failure_message(result, 2)

    assert "본문 검증을 통과한 기사가 없습니다" in message
    assert "5개" in message


def test_failure_message_lists_top_rejection_reasons() -> None:
    result = _result(
        {
            "verify_attempted": 5,
            "verify_passed": 3,
            "dup_removed": 0,
            "review_candidates": 3,
            "review_kept": 0,
            "review_rejected": 3,
            "reason_counts": {"논문 형식입니다": 2, "사유 미제공": 1},
        }
    )

    message = _failure_message(result, 2)

    assert "편집 심사에서 모두 탈락했습니다" in message
    assert "논문 형식입니다 (2건)" in message
    assert "사유 미제공 (1건)" in message


def test_summary_message_includes_stage_pass_rates_and_model() -> None:
    result = {
        "articles": [{"url": "u"}],
        "raw_count": 8,
        "stale_dropped": 1,
        "quality_dropped": 3,
        "new_count": 2,
        "feed_topup": 0,
        "max_age_days": 7,
        "error": None,
        "stages": {
            "verify_attempted": 8,
            "verify_passed": 5,
            "review_candidates": 5,
            "review_kept": 2,
        },
    }

    message = _summary_message(result, 2)

    assert "본문검증 5/8" in message
    assert "심사 2/5" in message
    assert CLAUDE_MODEL in message


def test_stages_line_tolerates_missing_stages() -> None:
    assert "본문검증 0/0" in _stages_line({})
