from __future__ import annotations

from bot import _make_embed
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
