from __future__ import annotations

import json
from pathlib import Path

CURATION_INTENT_PATH = Path("data/curation_intent.json")

_DEFAULT_INTENT = {
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


def _default_intent() -> dict:
    return {
        "active": _DEFAULT_INTENT["active"],
        "summary": _DEFAULT_INTENT["summary"],
        "focus_areas": list(_DEFAULT_INTENT["focus_areas"]),
        "boost_topics": list(_DEFAULT_INTENT["boost_topics"]),
        "avoid_topics": list(_DEFAULT_INTENT["avoid_topics"]),
        "focus_keywords": list(_DEFAULT_INTENT["focus_keywords"]),
        "avoid_keywords": list(_DEFAULT_INTENT["avoid_keywords"]),
        "search_hints": _DEFAULT_INTENT["search_hints"],
        "recency_hours": _DEFAULT_INTENT["recency_hours"],
        "expires_at": _DEFAULT_INTENT["expires_at"],
    }


def _string_list(value) -> list[str]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return []


def _normalized_topics(value, field_name: str, valid_topics: set[str] | None) -> list[str]:
    topics = _string_list(value)
    if not topics or valid_topics is None:
        return topics

    filtered: list[str] = []
    unknown: list[str] = []
    seen_unknown: set[str] = set()

    for topic in topics:
        if topic in valid_topics:
            filtered.append(topic)
        elif topic not in seen_unknown:
            seen_unknown.add(topic)
            unknown.append(topic)

    if unknown:
        print(f"[CurationIntent] Unknown {field_name} skipped: {', '.join(unknown)}")

    return filtered


def load_curation_intent(path: Path | str | None = None, valid_topics: set[str] | None = None) -> dict:
    """Load and normalize curation intent from JSON file."""
    intent_path = Path(path) if path is not None else CURATION_INTENT_PATH
    if not intent_path.exists():
        return _default_intent()

    try:
        raw = json.loads(intent_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("curation intent JSON must be an object")
    except Exception as e:
        print(f"[CurationIntent] Failed to load intent from {intent_path}: {e}")
        return _default_intent()

    if not raw.get("active", False):
        return _default_intent()

    intent = _default_intent()
    intent["active"] = True
    intent["summary"] = raw.get("summary", "") if isinstance(raw.get("summary", ""), str) else ""
    intent["focus_areas"] = _string_list(raw.get("focus_areas"))
    intent["boost_topics"] = _normalized_topics(raw.get("boost_topics"), "boost_topics", valid_topics)
    intent["avoid_topics"] = _normalized_topics(raw.get("avoid_topics"), "avoid_topics", valid_topics)
    intent["focus_keywords"] = _string_list(raw.get("focus_keywords"))
    intent["avoid_keywords"] = _string_list(raw.get("avoid_keywords"))
    intent["search_hints"] = raw.get("search_hints", "") if isinstance(raw.get("search_hints", ""), str) else ""
    recency_hours = raw.get("recency_hours", 48)
    intent["recency_hours"] = recency_hours if isinstance(recency_hours, int) and recency_hours > 0 else 48
    intent["expires_at"] = raw.get("expires_at")

    return intent
