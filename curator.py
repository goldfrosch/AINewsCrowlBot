"""
Claude 기반 AI 뉴스 큐레이션 엔진

흐름:
  1. news_curation_agent.run() — 3단계 agentic loop
       analyze_preferences → find_ai_articles (토픽별) → review_articles
  2. 실패 시 단순 웹 검색 1회 폴백
"""

import json
import time
from unittest.mock import Mock

import anthropic

import token_tracker
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from crawlers.base import Article

# ─── 시스템 프롬프트 (폴백용) ──────────────────────────────────────────────────

_SYSTEM_RESEARCH = """\
You are a focused researcher finding high-quality articles for developers who build and operate AI systems.
Your task is to find practical, actionable content — NOT general AI news.

Target reader: software engineer working on agentic systems, multi-agent orchestration,
AI-assisted code modification, or LLM infrastructure and evaluation harnesses.

Rules:
- Prioritize tutorials, how-to guides, best practices, and case studies over news
- Articles should contain concrete techniques, code examples, or measurable insights
- No sponsored content, generic AI hype, or press releases
- No pure news about model releases unless it directly affects developer workflow
- 2–4 targeted searches, then output JSON immediately
- If nothing relevant found, output an empty array []
- Output ONLY valid JSON — no preamble, no explanation"""


# ─── 유틸리티 ────────────────────────────────────────────────────────────────


def _extract_json_array(text: str) -> list[dict]:
    """응답 텍스트에서 바깥 JSON 배열을 추출합니다.

    '['부터 브래킷 매칭하여 유효한 바깥 배열 후보를 모두 찾고,
    모델 응답 끝부분의 최종 JSON 배열을 우선 사용합니다.
    """
    pos = 0
    candidates: list[list[dict]] = []
    while True:
        start = text.find("[", pos)
        if start == -1:
            return candidates[-1] if candidates else []

        depth = 0
        end = -1
        for i, ch in enumerate(text[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        if end == -1:
            pos = start + 1
            continue

        try:
            result = json.loads(text[start:end])
            if isinstance(result, list) and (not result or isinstance(result[0], dict)):
                candidates.append(result)
        except json.JSONDecodeError:
            pass

        pos = start + 1


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

    lines = [
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
            "Run 2–4 targeted searches, then output JSON.",
            "",
        ]
    )

    if exclude_urls:
        lines.append("Skip these URLs (already posted):")
        for url in exclude_urls[:40]:
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
    """에이전트 실패 시 웹 검색 1회로 기사를 수집합니다."""
    prompt = build_fallback_prompt(count, exclude_urls, preferences, intent)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        _t0 = time.perf_counter()
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            system=_SYSTEM_RESEARCH,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()

        token_tracker.log_token_usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            caller="curator_fallback",
            elapsed_seconds=round(time.perf_counter() - _t0, 2),
        )

        for block in response.content:
            if block.type == "text":
                data = _extract_json_array(block.text)
                if data:
                    print(f"[Curator] 폴백 완료: {len(data)}개 수집")
                    return _to_articles(data[:count])

    except anthropic.RateLimitError:
        print("[Curator] 폴백 RateLimit — 30초 대기 후 재시도")
        time.sleep(30)
        try:
            _t0 = time.perf_counter()
            with client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=4000,
                tools=[{"type": "web_search_20260209", "name": "web_search"}],
                system=_SYSTEM_RESEARCH,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                response = stream.get_final_message()
            token_tracker.log_token_usage(
                response.usage.input_tokens,
                response.usage.output_tokens,
                caller="curator_fallback_retry",
                elapsed_seconds=round(time.perf_counter() - _t0, 2),
            )
            for block in response.content:
                if block.type == "text":
                    data = _extract_json_array(block.text)
                    if data:
                        print(f"[Curator] 폴백 재시도 완료: {len(data)}개 수집")
                        return _to_articles(data[:count])
        except Exception as e:
            print(f"[Curator] 폴백 재시도 실패: {e}")

    except anthropic.APIStatusError as e:
        print(f"[Curator] 폴백 API 오류 ({e.status_code}): {e}")

    except Exception as e:
        print(f"[Curator] 폴백 예상치 못한 오류: {e}")

    return []


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
