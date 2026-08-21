"""
Claude 기반 AI 뉴스 큐레이션 엔진

흐름:
  1. news_curation_agent.run() — 3단계 agentic loop
       analyze_preferences → find_ai_articles (토픽별) → review_articles
  2. 실패 시 단순 웹 검색 1회 폴백
"""

from unittest.mock import Mock

import anthropic

import claude_search
import recency
from config import ANTHROPIC_API_KEY, EXCLUDE_URL_PROMPT_LIMIT, WEB_SEARCH_MAX_USES
from crawlers.base import Article
from text_utils import extract_json_array

# ─── 시스템 프롬프트 (폴백용) ──────────────────────────────────────────────────

_SYSTEM_RESEARCH = """\
You are a focused researcher finding RECENT, high-quality articles for developers who build and operate
AI systems. Your task is to find practical, actionable content — NOT general AI news.

Target reader: software engineer working on agentic systems, multi-agent orchestration,
AI-assisted code modification, or LLM infrastructure and evaluation harnesses.

Rules, in priority order:
1. RECENCY IS A HARD GATE. The user prompt states today's date and a cutoff date. Articles published
   before the cutoff must not be returned, regardless of quality.
2. Every article needs a verifiable publication date. If you cannot establish one, drop the article.
   Never guess a date and never report today's date for an undated page.
3. Within the window, prefer tutorials, how-to guides, and case studies with concrete techniques.
4. No sponsored content, no press releases, no undated evergreen SEO pages.
5. Output ONLY valid JSON — no preamble, no explanation. If nothing qualifies, output []."""

# system 프롬프트는 매 호출 동일하므로 prompt caching으로 입력 토큰 절감.
# 30초 후 재시도(RateLimit) 시 캐시 TTL(5분) 내라 캐시 히트 → input 토큰 ~90% 할인.
_SYSTEM_RESEARCH_BLOCKS = [{"type": "text", "text": _SYSTEM_RESEARCH, "cache_control": {"type": "ephemeral"}}]


# ─── 유틸리티 ────────────────────────────────────────────────────────────────


# 구현은 text_utils로 이전했다. agents 쪽에 있던 rfind 기반 중복 구현이
# description 내 '['나 중첩 keywords 배열에서 오작동했기 때문에 단일화했다.
_extract_json_array = extract_json_array


def _to_articles(data: list[dict]) -> list[Article]:
    articles = []
    for item in data:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue

        desc = str(item.get("description") or "")
        reason = str(item.get("curator_reason") or "")
        full_desc = f"{desc}\n\n💡 **선정 이유**: {reason}" if reason else desc

        raw_kw = item.get("keywords")
        keywords = raw_kw if isinstance(raw_kw, list) else []

        articles.append(
            Article(
                url=url,
                title=title,
                source=str(item.get("source") or "AI Research"),
                description=full_desc[:500],
                author=str(item.get("author") or ""),
                published_at=str(item.get("published_at") or ""),
                platform_score=100.0,
                keywords=keywords,
            )
        )
    return articles


def _is_mocked(obj: object) -> bool:
    return isinstance(obj, Mock)


def _extract_preference_hints(preferences: dict | None) -> dict:
    """선호도 profile/legacy shape에서 폴백 프롬프트 힌트를 추출합니다."""

    hints = {
        "liked_sources": [],
        "disliked_sources": [],
        "liked_keywords": [],
        "skip_keywords": [],
    }

    if not isinstance(preferences, dict):
        return hints

    def _append_unique(target: list[str], values: list[str]) -> None:
        for value in values:
            if value and value not in target:
                target.append(value)

    curation_hints = preferences.get("curation_hints")
    if isinstance(curation_hints, dict):
        _append_unique(
            hints["liked_sources"], [str(v).strip() for v in curation_hints.get("boost_sources", []) if str(v).strip()]
        )
        _append_unique(
            hints["disliked_sources"],
            [str(v).strip() for v in curation_hints.get("avoid_sources", []) if str(v).strip()],
        )
        _append_unique(
            hints["liked_keywords"],
            [str(v).strip() for v in curation_hints.get("focus_keywords", []) if str(v).strip()],
        )
        _append_unique(
            hints["skip_keywords"], [str(v).strip() for v in curation_hints.get("skip_keywords", []) if str(v).strip()]
        )

    for item in preferences.get("sources", []) if isinstance(preferences.get("sources", []), list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("source") or "").strip()
        multiplier = item.get("multiplier")
        if not name or not isinstance(multiplier, (int, float)):
            continue
        if multiplier > 1.1:
            _append_unique(hints["liked_sources"], [name])
        elif multiplier < 0.9:
            _append_unique(hints["disliked_sources"], [name])

    for item in preferences.get("keywords", []) if isinstance(preferences.get("keywords", []), list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("keyword") or "").strip()
        multiplier = item.get("multiplier")
        if not name or not isinstance(multiplier, (int, float)):
            continue
        if multiplier > 1.1:
            _append_unique(hints["liked_keywords"], [name])
        elif multiplier < 0.9:
            _append_unique(hints["skip_keywords"], [name])

    return hints


def build_fallback_prompt(
    count: int,
    exclude_urls: list[str],
    preferences: dict | None = None,
    intent: dict | None = None,
) -> str:
    """폴백 리서치용 user prompt를 구성합니다."""

    pref_hints = _extract_preference_hints(preferences)
    max_age_days = recency.max_age_from_intent(intent)

    lines = recency.prompt_lines(max_age_days)
    lines += [
        f"Find {count} high-quality articles for developers who build and operate AI systems.",
        "Focus on: multi-agent orchestration, harness engineering for LLMs, AI-assisted complex code modification, prompt engineering, agentic coding workflows.",
    ]

    if isinstance(intent, dict) and intent.get("active"):
        lines.append("Runtime Editorial Intent:")

        summary = str(intent.get("summary") or "").strip()
        if summary:
            lines.append(f"- Summary: {summary}")

        focus_areas = intent.get("focus_areas") if isinstance(intent.get("focus_areas"), list) else []
        for area in focus_areas:
            area_text = str(area).strip()
            if area_text:
                lines.append(f"- Focus area: {area_text}")

        focus_keywords = intent.get("focus_keywords") if isinstance(intent.get("focus_keywords"), list) else []
        focus_keywords = [str(item).strip() for item in focus_keywords if str(item).strip()]
        if focus_keywords:
            lines.append(f"- Focus keywords: {', '.join(focus_keywords)}")

        avoid_keywords = intent.get("avoid_keywords") if isinstance(intent.get("avoid_keywords"), list) else []
        avoid_keywords = [str(item).strip() for item in avoid_keywords if str(item).strip()]
        if avoid_keywords:
            lines.append(f"- Avoid keywords: {', '.join(avoid_keywords)}")

        search_hints = str(intent.get("search_hints") or "").strip()
        if search_hints:
            lines.append(f"- Search hints: {search_hints}")

    pref_lines = []
    if pref_hints["liked_sources"]:
        pref_lines.append(f"User prefers these sources: {', '.join(pref_hints['liked_sources'][:5])}")
    if pref_hints["disliked_sources"]:
        pref_lines.append(f"User wants to avoid these sources: {', '.join(pref_hints['disliked_sources'][:5])}")
    if pref_hints["liked_keywords"]:
        pref_lines.append(f"User wants to focus on these topics: {', '.join(pref_hints['liked_keywords'][:10])}")
    if pref_hints["skip_keywords"]:
        pref_lines.append(f"User wants to skip these topics: {', '.join(pref_hints['skip_keywords'][:10])}")

    if pref_lines:
        lines.append("Learned Preference Hints:")
        lines.extend(f"- {line}" for line in pref_lines)

    lines.extend(
        [
            "NOT general AI news — only content with actionable techniques or concrete examples.",
            "Requirements: real articles only, no sponsored content, no pure press releases.",
            '"published_at" MUST be the real publication date (YYYY-MM-DD). If you cannot verify it, omit the article.',
            f"Run at most {WEB_SEARCH_MAX_USES} targeted searches, then output JSON.",
            "",
        ]
    )

    if exclude_urls:
        lines.append("Skip these URLs (already posted):")
        for url in exclude_urls[:EXCLUDE_URL_PROMPT_LIMIT]:
            lines.append(f"- {url}")
        lines.append("")

    lines += [
        f"Output a JSON array of exactly {count} items:",
        '[{"url":"...","title":"...","source":"...","description":"2-3 sentences","author":"","published_at":"YYYY-MM-DD","curator_reason":"one sentence","keywords":["keyword1","keyword2","keyword3"]}]',
        "Assign 3-5 relevant AI topic keywords to each article from: claude, chatgpt, gpt-4, gemini, llm, prompt engineering, rag, fine-tuning, mcp, ai agent, agentic, langchain, vector database, embedding, ai coding, openai, anthropic",
        "If nothing found: []",
    ]

    return "\n".join(lines)


# ─── 폴백: 단순 웹 검색 1회 ──────────────────────────────────────────────────


def _fallback_research(
    count: int,
    exclude_urls: list[str],
    preferences: dict,
    intent: dict | None = None,
) -> list[Article]:
    """에이전트 실패 시 웹 검색으로 기사를 수집합니다.

    스트리밍·재시도·절단 감지는 claude_search에 위임한다.
    (기존에는 이 함수와 에이전트가 같은 로직을 복제하고 있었고,
     max_tokens 절단 검사가 양쪽 모두 빠져 있었다.)
    """
    prompt = build_fallback_prompt(count, exclude_urls, preferences, intent)
    max_age_days = recency.max_age_from_intent(intent)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    outcome = claude_search.search_articles(
        client,
        prompt=prompt,
        system_blocks=_SYSTEM_RESEARCH_BLOCKS,
        caller="curator_fallback",
    )

    if not outcome.articles:
        print(f"[Curator] 폴백 결과 없음 (stop_reason={outcome.stop_reason}, error={outcome.error})")
        return []

    fresh = [item for item in outcome.articles if not recency.is_stale(item.get("published_at"), max_age_days)]
    dropped = len(outcome.articles) - len(fresh)
    if dropped:
        print(f"[Curator] 폴백 기한초과 {dropped}개 제외 (최근 {max_age_days}일 기준)")

    print(f"[Curator] 폴백 완료: {len(fresh)}개 수집")
    return _to_articles(fresh[:count])


# ─── 공개 API ─────────────────────────────────────────────────────────────────


def research(
    count: int,
    exclude_urls: list[str] | None = None,
    preferences: dict | None = None,
    intent: dict | None = None,
) -> list[Article]:
    """
    뉴스 큐레이션 에이전트로 개발자용 AI 아티클을 수집합니다.
    에이전트 실패 시 단순 웹 검색으로 폴백합니다.

    Args:
        count:        수집할 기사 수
        exclude_urls: 이미 게시된 URL 목록 (에이전트가 DB에서 직접 조회하므로 폴백 전용)
        preferences:  DB 소스/키워드 선호도 (에이전트에 external_preferences로 전달)
        intent:       큐레이션 의도 (에이전트 및 폴백에 전달)

    Returns:
        Article 리스트 (len ≤ count)
    """
    from agents.news_curation_agent import run as _agent_run

    if not ANTHROPIC_API_KEY and not _is_mocked(_agent_run) and not _is_mocked(anthropic.Anthropic):
        raise ValueError("ANTHROPIC_API_KEY가 .env에 설정되어 있지 않습니다.")

    try:
        raw = _agent_run(target_count=count, external_preferences=preferences or {}, intent=intent)
        if raw:
            articles = _to_articles(raw)
            print(f"[Curator] 에이전트 완료: {len(articles)}개 선정")
            return articles
        print("[Curator] 에이전트 결과 없음 — 폴백 실행")
    except Exception as e:
        import traceback

        print(f"[Curator] 에이전트 실패 — 폴백 실행: {e}")
        traceback.print_exc()

    if not ANTHROPIC_API_KEY and not _is_mocked(anthropic.Anthropic):
        raise ValueError("ANTHROPIC_API_KEY가 .env에 설정되어 있지 않습니다.")

    return _fallback_research(count, exclude_urls or [], preferences or {}, intent=intent)
