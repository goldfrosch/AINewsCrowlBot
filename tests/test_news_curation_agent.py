"""news_curation_agent.py prompt tests"""

from agents.news_curation_agent import build_search_prompt


def _base_prompt(monkeypatch, preferences=None, intent=None):
    monkeypatch.setattr("agents.news_curation_agent.db.get_todays_posted_urls", lambda: [])
    return build_search_prompt(
        ["ai_game_ui_sound"],
        3,
        set(),
        preferences=preferences,
        intent=intent,
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
