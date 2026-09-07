"""
기사 탐색용 user prompt 구성

핵심 변경: 프롬프트에 **오늘 날짜와 컷오프 날짜를 명시**한다.
기존 프롬프트는 `within 48 hours`라고만 적었는데, 모델은 오늘이 며칠인지
알 수 없어 학습 컷오프 기준으로 "최신"을 판단했다. 그 결과 실측상
최근 게시 40건 중 30일 초과가 87.5%(최악 490일)였다.
web_search 도구에는 날짜 필터 파라미터가 없으므로, 날짜를 프롬프트로
알려주고 코드에서 다시 검증하는 2단 방어가 유일한 방법이다.
"""

import database as db
import recency
from agents.agent_spec import TOPIC_DESC
from config import EXCLUDE_URL_PROMPT_LIMIT, WEB_SEARCH_MAX_USES

# 모델이 각 기사에 부여할 키워드 후보. article_keywords 테이블과 선호도 학습의 입력이 된다.
_KEYWORD_VOCAB = (
    "claude, chatgpt, gpt-4, gemini, llm, prompt engineering, rag, fine-tuning, mcp, "
    "ai agent, agentic, langchain, vector database, embedding, ai coding, openai, anthropic, "
    "ai game development, game client, unreal, unity, godot, ai game art, ai game ui, "
    "game asset workflow, procedural generation, ai animation, ai sound design"
)

_OUTPUT_SCHEMA = (
    '[{"url":"...","title":"...","source":"...","description":"2-3 sentences",'
    '"author":"","published_at":"YYYY-MM-DD","curator_reason":"one sentence",'
    '"keywords":["keyword1","keyword2","keyword3"]}]'
)


def _intent_lines(intent: dict) -> list[str]:
    lines = ["", "Runtime Editorial Intent:"]
    summary = intent.get("summary")
    if summary:
        lines.append(summary)

    focus_areas = intent.get("focus_areas") or []
    if focus_areas:
        lines.append("Focus areas:")
        lines.extend(f"- {item}" for item in focus_areas)

    for label, key in (("Boost topics", "boost_topics"), ("Avoid topics", "avoid_topics")):
        topics = intent.get(key) or []
        if topics:
            lines.append(f"{label}:")
            lines.extend(f"- {topic}: {TOPIC_DESC.get(topic, topic)}" for topic in topics)

    focus_keywords = intent.get("focus_keywords") or []
    if focus_keywords:
        lines.append(f"Focus keywords: {', '.join(focus_keywords)}")
    avoid_keywords = intent.get("avoid_keywords") or []
    if avoid_keywords:
        lines.append(f"Avoid keywords: {', '.join(avoid_keywords)}")

    search_hints = intent.get("search_hints") or []
    if isinstance(search_hints, str):
        search_hints = [search_hints] if search_hints else []
    if search_hints:
        lines.append("Search hints:")
        lines.extend(f"- {hint}" for hint in search_hints)

    return lines


def _preference_lines(preferences: dict) -> list[str]:
    liked_sources = preferences.get("liked_sources") or []
    disliked_sources = preferences.get("disliked_sources") or []
    liked_keywords = preferences.get("liked_keywords") or []
    curation_hints = preferences.get("curation_hints") or {}
    skip_keywords = curation_hints.get("skip_keywords") or [] if isinstance(curation_hints, dict) else []

    if not (liked_sources or disliked_sources or liked_keywords or skip_keywords):
        return []

    lines = ["", "Learned Preference Hints:"]
    if liked_sources:
        lines.append(f"User prefers these sources: {', '.join(liked_sources)}")
    if disliked_sources:
        lines.append(f"User avoids these sources: {', '.join(disliked_sources)}")
    if liked_keywords:
        lines.append(f"User enjoys these topics: {', '.join(liked_keywords)}")
    if skip_keywords:
        lines.append(f"User wants to skip these topics: {', '.join(skip_keywords)}")
    return lines


def build_search_prompt(
    topics: list[str],
    count: int,
    already_collected: set[str],
    preferences: dict | None = None,
    intent: dict | None = None,
    max_age_days: int | None = None,
    round_index: int = 0,
) -> str:
    """기사 탐색 프롬프트를 구성한다."""
    effective_age = max_age_days if max_age_days is not None else recency.max_age_from_intent(intent)

    lines = recency.prompt_lines(effective_age)
    lines.append(f"Find {count} high-quality AI articles from ANY of these topics:")
    lines.append("")
    lines.extend(f"- {t}: {TOPIC_DESC.get(t, t)}" for t in topics)

    if intent and intent.get("active"):
        lines += _intent_lines(intent)

    lines += _preference_lines(preferences or {})

    lines += [
        "",
        "Rules:",
        "- Recency is a HARD filter for articles whose date you CAN determine: outside the window, skip it.",
        '- "published_at" (YYYY-MM-DD): take it from the search result metadata, the snippet, or the URL '
        'path. If none of those yield a date, return the article with "published_at": "" rather than '
        "dropping it. Returning zero articles is a worse outcome than returning undated candidates.",
        "- Prefer pages that look recent (year/month in the URL, 'N days ago', current-month coverage) "
        "over undated evergreen SEO pages, docs pages, and 'awesome-list' repos.",
        "- Within the window, prefer tutorials, case studies, and posts with concrete techniques.",
        "- Treat Unreal, Unity, and Godot equally; do not require one engine to appear in the results.",
        "- For game content, focus on workflows ordinary game client programmers can reproduce: code, 3D, "
        "textures, UI/UX, animation, sound, and asset integration.",
        "- Exclude papers, preprints, academic abstracts, sponsored content, press releases, generic AI news, "
        "and shallow listicles.",
        "- Exclude Chinese-language pages. A Chinese-owned source is acceptable when the article itself is "
        "written in English or Korean.",
        f"- Run at most {WEB_SEARCH_MAX_USES} targeted searches, then output JSON.",
    ]

    if round_index > 0:
        lines.append(
            f"- RETRY ROUND {round_index}: previous searches did not yield enough fresh articles. "
            "Use DIFFERENT queries, different sites, and different subtopics than the obvious ones."
        )

    excluded = _excluded_urls(already_collected)
    if excluded:
        lines += ["", "Skip these URLs (already collected or posted):"]
        lines.extend(f"- {url}" for url in excluded)

    lines += [
        "",
        f"Output a JSON array of up to {count} items:",
        _OUTPUT_SCHEMA,
        f"Assign 3-5 relevant keywords to each article from: {_KEYWORD_VOCAB}",
        "If nothing found: []",
    ]

    return "\n".join(lines)


def _excluded_urls(already_collected: set[str]) -> list[str]:
    """게시 이력 + 이번 실행에서 수집한 URL을 최근순으로 잘라 반환한다.

    기존에는 `get_todays_posted_urls()`를 썼는데 브리핑 시각(06:00)에는
    항상 빈 배열이라 중복 회피가 전혀 동작하지 않았다.
    """
    posted = db.get_recent_posted_urls()
    ordered = list(already_collected) + [url for url in posted if url not in already_collected]
    return ordered[:EXCLUDE_URL_PROMPT_LIMIT]
