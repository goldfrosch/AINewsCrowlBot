"""
공유 pytest fixture
"""

from pathlib import Path

import pytest

import database as db
import token_tracker


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """임시 SQLite로 DB_PATH를 override하고 init_db()까지 실행합니다."""
    db_path = tmp_path / "test_bot.db"
    db.set_db_path(db_path)
    db.init_db()
    yield db_path
    # cleanup: 기본 경로로 복원
    db.set_db_path(Path("data/bot.db"))


@pytest.fixture
def tmp_token_db(tmp_path, monkeypatch):
    """임시 토큰 DB."""
    db_path = tmp_path / "test_token.db"
    token_tracker.set_token_db_path(db_path)
    token_tracker.init_token_db()
    yield db_path
    token_tracker.set_token_db_path(Path("data/token_usage.db"))


@pytest.fixture
def sample_articles():
    """테스트용 기사 dict 리스트."""
    return [
        {
            "url": "https://example.com/article-1",
            "title": "GPT-5 새로운 기능 발표",
            "source": "VentureBeat AI",
            "description": "OpenAI가 GPT-5의 새로운 기능을 발표했습니다.",
            "author": "John Doe",
            "image_url": "",
            "published_at": "2026-04-01",
            "platform_score": 500.0,
            "keywords": ["gpt-4", "openai", "llm"],
        },
        {
            "url": "https://example.com/article-2",
            "title": "Claude Code로 생산성 10배 올리기",
            "source": "HackerNews",
            "description": "Claude Code를 활용한 개발 워크플로우 최적화 가이드",
            "author": "Jane Smith",
            "image_url": "",
            "published_at": "2026-04-02",
            "platform_score": 1200.0,
            "keywords": ["claude", "ai coding", "developer tools"],
        },
        {
            "url": "https://example.com/article-3",
            "title": "RAG 파이프라인 구축 베스트 프랙티스",
            "source": "Medium AI",
            "description": "프로덕션 RAG 시스템 구축을 위한 실전 가이드",
            "author": "Bob Lee",
            "image_url": "",
            "published_at": "2026-04-03",
            "platform_score": 200.0,
            "keywords": ["rag", "vector database", "embedding"],
        },
    ]
