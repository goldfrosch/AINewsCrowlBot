"""
공유 pytest fixture
"""

from datetime import timedelta
from pathlib import Path

import pytest

import database as db
import recency
import token_tracker


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    """모든 테스트를 임시 SQLite로 격리한다.

    autouse인 이유: 격리를 명시적으로 요청하지 않은 테스트가
    프로덕션 `data/bot.db`와 `data/token_usage.db`에 실제로 기록을 남겼다.
    (pytest 1회 실행으로 token_usage.db에 더미 행 6개가 추가되는 것을 확인)
    """
    db_path = tmp_path / "test_bot.db"
    db.set_db_path(db_path)
    db.init_db()
    token_tracker.set_token_db_path(tmp_path / "test_token.db")
    token_tracker.init_token_db()
    yield db_path
    db.set_db_path(Path("data/bot.db"))
    token_tracker.set_token_db_path(Path("data/token_usage.db"))


@pytest.fixture
def tmp_token_db(tmp_db, tmp_path):
    """토큰 DB 경로를 명시적으로 쓰고 싶은 테스트용."""
    return tmp_path / "test_token.db"


def days_ago(days: int) -> str:
    """신선도 컷오프에 종속되지 않도록 상대 날짜를 만든다."""
    return (recency.today() - timedelta(days=days)).isoformat()


@pytest.fixture
def sample_articles():
    """테스트용 기사 dict 리스트 (발행일은 항상 최근으로 유지)."""
    return [
        {
            "url": "https://example.com/article-1",
            "title": "GPT-5 새로운 기능 발표",
            "source": "VentureBeat AI",
            "description": "OpenAI가 GPT-5의 새로운 기능을 발표했습니다.",
            "author": "John Doe",
            "image_url": "",
            "published_at": days_ago(1),
            "platform_score": 100.0,
            "keywords": ["gpt-4", "openai", "llm"],
        },
        {
            "url": "https://example.com/article-2",
            "title": "Claude Code로 생산성 10배 올리기",
            "source": "HackerNews",
            "description": "Claude Code를 활용한 개발 워크플로우 최적화 가이드",
            "author": "Jane Smith",
            "image_url": "",
            "published_at": days_ago(2),
            "platform_score": 100.0,
            "keywords": ["claude", "ai coding", "developer tools"],
        },
        {
            "url": "https://example.com/article-3",
            "title": "RAG 파이프라인 구축 베스트 프랙티스",
            "source": "Medium AI",
            "description": "프로덕션 RAG 시스템 구축을 위한 실전 가이드",
            "author": "Bob Lee",
            "image_url": "",
            "published_at": days_ago(3),
            "platform_score": 100.0,
            "keywords": ["rag", "vector database", "embedding"],
        },
    ]
