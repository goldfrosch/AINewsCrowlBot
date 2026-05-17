from pathlib import Path

from curation_intent import load_curation_intent


def test_missing_file_returns_inactive_default(tmp_path):
    intent = load_curation_intent(tmp_path / "missing.json", valid_topics={"ai_game_ui_sound"})

    assert intent == {
        "active": False,
        "summary": "",
        "focus_areas": [],
        "boost_topics": [],
        "avoid_topics": [],
        "focus_keywords": [],
        "avoid_keywords": [],
        "search_hints": "",
        "recency_hours": 48,
        "expires_at": None,
    }


def test_malformed_json_returns_inactive_default(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("{bad", encoding="utf-8")

    intent = load_curation_intent(path)
    captured = capsys.readouterr()

    assert not intent["active"]
    assert "Failed to load intent" in captured.out


def test_active_false_returns_inactive(tmp_path):
    path = tmp_path / "inactive.json"
    path.write_text('{"active": false, "summary": "x"}', encoding="utf-8")

    intent = load_curation_intent(path)

    assert not intent["active"]
    assert intent["summary"] == ""
    assert intent["focus_areas"] == []


def test_active_true_loads_fields(tmp_path):
    path = tmp_path / "active.json"
    path.write_text(
        """
        {
          "active": true,
          "summary": "game ui focus",
          "focus_areas": ["HUD", "menus"],
          "boost_topics": ["ai_game_ui_sound"],
          "avoid_topics": ["ai_game_workflow"],
          "focus_keywords": ["Unity UI"],
          "avoid_keywords": ["press release"],
          "search_hints": "prefer practical case studies",
          "recency_hours": 24,
          "expires_at": null
        }
        """,
        encoding="utf-8",
    )

    intent = load_curation_intent(path, valid_topics={"ai_game_ui_sound", "ai_game_workflow"})

    assert intent == {
        "active": True,
        "summary": "game ui focus",
        "focus_areas": ["HUD", "menus"],
        "boost_topics": ["ai_game_ui_sound"],
        "avoid_topics": ["ai_game_workflow"],
        "focus_keywords": ["Unity UI"],
        "avoid_keywords": ["press release"],
        "search_hints": "prefer practical case studies",
        "recency_hours": 24,
        "expires_at": None,
    }


def test_unknown_boost_topics_filtered(tmp_path, capsys):
    path = tmp_path / "topics.json"
    path.write_text('{"active": true, "boost_topics": ["ai_game_ui_sound", "nonexistent_topic"]}', encoding="utf-8")

    intent = load_curation_intent(path, valid_topics={"ai_game_ui_sound"})
    captured = capsys.readouterr()

    assert intent["boost_topics"] == ["ai_game_ui_sound"]
    assert "nonexistent_topic" in captured.out


def test_non_list_fields_default_to_empty(tmp_path):
    path = tmp_path / "bad_lists.json"
    path.write_text('{"active": true, "focus_areas": "not a list"}', encoding="utf-8")

    intent = load_curation_intent(path)

    assert intent["focus_areas"] == []


def test_invalid_recency_hours_defaults(tmp_path):
    path = tmp_path / "bad_recency.json"
    path.write_text('{"active": true, "recency_hours": "bad"}', encoding="utf-8")

    intent = load_curation_intent(path)

    assert intent["recency_hours"] == 48


def test_default_path_used_when_none(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: False)

    intent = load_curation_intent(None)

    assert not intent["active"]
