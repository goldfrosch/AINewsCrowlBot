"""news_curation_agent.py prompt tests"""

from datetime import timedelta

import recency
from agents.news_curation_agent import build_search_prompt
from config import RECENCY_MAX_AGE_DAYS


def _base_prompt(monkeypatch, preferences=None, intent=None, **kwargs):
    return build_search_prompt(
        ["ai_game_ui_sound"],
        3,
        set(),
        preferences=preferences,
        intent=intent,
        **kwargs,
    )


def test_intent_loaded_into_search_prompt(monkeypatch):
    intent = {
        "active": True,
        "summary": "Game UI focus",
        "focus_areas": ["sentinel-game-ui"],
        "boost_topics": [],
        "avoid_topics": [],
        "focus_keywords": [],
        "avoid_keywords": [],
        "search_hints": [],
        "recency_hours": 24,
        "expires_at": None,
    }

    prompt = _base_prompt(monkeypatch, intent=intent)

    assert "Runtime Editorial Intent" in prompt
    assert "sentinel-game-ui" in prompt


def test_intent_boost_topics_with_descriptions(monkeypatch):
    intent = {
        "active": True,
        "summary": "Game UI focus",
        "focus_areas": [],
        "boost_topics": ["ai_game_ui_sound"],
        "avoid_topics": [],
        "focus_keywords": [],
        "avoid_keywords": [],
        "search_hints": [],
        "recency_hours": 24,
        "expires_at": None,
    }

    prompt = _base_prompt(monkeypatch, intent=intent)

    assert "ai_game_ui_sound" in prompt
    assert "AI for game UI/UX design" in prompt


def test_profile_hints_loaded_into_search_prompt(monkeypatch):
    preferences = {
        "liked_sources": ["ArXiv"],
        "disliked_sources": [],
        "liked_keywords": ["agent"],
    }

    prompt = _base_prompt(monkeypatch, preferences=preferences)

    assert "Learned Preference Hints" in prompt
    assert "User prefers these sources: ArXiv" in prompt
    assert "User enjoys these topics: agent" in prompt


def test_inactive_intent_omitted(monkeypatch):
    intent = {
        "active": False,
        "summary": "ignored",
        "focus_areas": ["x"],
        "boost_topics": ["ai_game_ui_sound"],
        "avoid_topics": [],
        "focus_keywords": [],
        "avoid_keywords": [],
        "search_hints": [],
        "recency_hours": 24,
        "expires_at": None,
    }

    prompt = _base_prompt(monkeypatch, intent=intent)

    assert "Runtime Editorial Intent" not in prompt


def test_both_intent_and_preferences_in_prompt(monkeypatch):
    """Active intent and preference hints should both appear in search prompt."""
    preferences = {
        "liked_sources": ["ArXiv"],
        "disliked_sources": [],
        "liked_keywords": ["agent"],
    }
    intent = {
        "active": True,
        "summary": "Game UI focus",
        "focus_areas": ["sentinel-game-ui"],
        "boost_topics": [],
        "avoid_topics": [],
        "focus_keywords": ["Unity UI"],
        "avoid_keywords": [],
        "search_hints": "practical tutorials",
        "recency_hours": 24,
        "expires_at": None,
    }

    prompt = _base_prompt(monkeypatch, preferences=preferences, intent=intent)

    assert "Runtime Editorial Intent" in prompt
    assert "sentinel-game-ui" in prompt
    assert "Unity UI" in prompt
    assert "Learned Preference Hints" in prompt
    assert "ArXiv" in prompt
    assert "agent" in prompt


def test_no_preferences_no_section(monkeypatch):
    prompt = _base_prompt(monkeypatch, preferences=None)

    assert "Learned Preference Hints" not in prompt


# ── 신선도 주입 ──────────────────────────────────────────────────────────────


def test_prompt_states_todays_date(monkeypatch):
    """모델은 오늘 날짜를 모른다. 명시하지 않으면 'within 48 hours'가 무의미해진다."""
    prompt = _base_prompt(monkeypatch)

    assert recency.today().isoformat() in prompt
    assert "Today is" in prompt


def test_prompt_states_cutoff_date(monkeypatch):
    prompt = _base_prompt(monkeypatch)

    cutoff = (recency.today() - timedelta(days=RECENCY_MAX_AGE_DAYS)).isoformat()
    assert cutoff in prompt
    assert "HARD REQUIREMENT" in prompt


def test_prompt_cutoff_follows_intent_recency_hours(monkeypatch):
    intent = {"active": True, "summary": "s", "recency_hours": 24}
    prompt = _base_prompt(monkeypatch, intent=intent)

    assert (recency.today() - timedelta(days=1)).isoformat() in prompt
    assert "within the last 1 days" in prompt


def test_prompt_requests_keywords_field(monkeypatch):
    """에이전트 스키마에 keywords가 없어 기사 109건 중 101건이 키워드 0개였다."""
    prompt = _base_prompt(monkeypatch)

    assert '"keywords"' in prompt
    assert "Assign 3-5 relevant keywords" in prompt


def test_prompt_lists_recent_posted_urls(monkeypatch, tmp_db):
    """중복 회피 목록이 매일 비어 있던 회귀를 막는다."""
    import database as db

    db.upsert_article(
        {
            "url": "https://example.com/posted-yesterday",
            "title": "Yesterday",
            "source": "S",
            "description": "",
            "author": "",
            "image_url": "",
            "published_at": "",
            "platform_score": 100.0,
            "keywords": [],
        }
    )
    article_id = db.get_pending_articles()[0]["id"]
    db.mark_as_posted(article_id, "msg-1", "chan-1")

    prompt = _base_prompt(monkeypatch)

    assert "https://example.com/posted-yesterday" in prompt


def test_retry_round_asks_for_different_queries(monkeypatch):
    prompt = _base_prompt(monkeypatch, round_index=1)

    assert "RETRY ROUND 1" in prompt
    assert "DIFFERENT queries" in prompt
