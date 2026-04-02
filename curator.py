"""
Claude 기반 AI 뉴스 큐레이션 엔진

흐름:
  1. news_curation_agent.run() — 3단계 agentic loop
       analyze_preferences → find_ai_articles (토픽별) → review_articles
  2. 실패 시 단순 웹 검색 1회 폴백
"""
import json
import time
import anthropic

from crawlers.base import Article
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
import token_tracker

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
    """응답 텍스트에서 마지막 JSON 배열을 추출합니다."""
    start = text.rfind("[")
    if start == -1:
        return []

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
        return []

    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return []


def _to_articles(data: list[dict]) -> list[Article]:
    articles = []
    for item in data:
        url = item.get("url", "").strip()
        title = item.get("title", "").strip()
        if not url or not title:
            continue

        desc = item.get("description", "")
        reason = item.get("curator_reason", "")
        full_desc = f"{desc}\n\n💡 **선정 이유**: {reason}" if reason else desc

        articles.append(Article(
            url=url,
            title=title,
            source=item.get("source", "AI Research"),
            description=full_desc[:500],
            author=item.get("author", ""),
            published_at=item.get("published_at", ""),
            platform_score=100.0,
        ))
    return articles


# ─── 폴백: 단순 웹 검색 1회 ──────────────────────────────────────────────────

def _fallback_research(
    count: int,
    exclude_urls: list[str],
    preferences: dict,
) -> list[Article]:
    """에이전트 실패 시 웹 검색 1회로 기사를 수집합니다."""
    liked_kws = [k["keyword"] for k in preferences.get("keywords", []) if k["multiplier"] > 1.1][:5]
    pref_hint = f"\nUser enjoys these topics: {', '.join(liked_kws)}" if liked_kws else ""

    lines = [
        f"Find {count} high-quality articles for developers who build and operate AI systems.",
        "Focus on: multi-agent orchestration, harness engineering for LLMs, AI-assisted complex code modification, prompt engineering, agentic coding workflows.",
        "NOT general AI news — only content with actionable techniques or concrete examples.",
        pref_hint,
        "Requirements: real articles only, no sponsored content, no pure press releases.",
        "Run 2–4 targeted searches, then output JSON.",
        "",
    ]

    if exclude_urls:
        lines.append("Skip these URLs (already posted):")
        for url in exclude_urls[:40]:
            lines.append(f"- {url}")
        lines.append("")

    lines += [
        f"Output a JSON array of exactly {count} items:",
        '[{"url":"...","title":"...","source":"...","description":"2-3 sentences","author":"","published_at":"YYYY-MM-DD","curator_reason":"one sentence"}]',
        "If nothing found: []",
    ]

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        _t0 = time.perf_counter()
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            system=_SYSTEM_RESEARCH,
            messages=[{"role": "user", "content": "\n".join(lines)}],
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
                messages=[{"role": "user", "content": "\n".join(lines)}],
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
) -> list[Article]:
    """
    뉴스 큐레이션 에이전트로 개발자용 AI 아티클을 수집합니다.
    에이전트 실패 시 단순 웹 검색으로 폴백합니다.

    Args:
        count:        수집할 기사 수
        exclude_urls: 이미 게시된 URL 목록 (에이전트가 DB에서 직접 조회하므로 폴백 전용)
        preferences:  DB 소스/키워드 선호도 (에이전트에 external_preferences로 전달)

    Returns:
        Article 리스트 (len ≤ count)
    """
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY가 .env에 설정되어 있지 않습니다.")

    from agents.news_curation_agent import run as _agent_run
    try:
        raw = _agent_run(target_count=count, external_preferences=preferences or {})
        if raw:
            articles = _to_articles(raw)
            print(f"[Curator] 에이전트 완료: {len(articles)}개 선정")
            return articles
        print("[Curator] 에이전트 결과 없음 — 폴백 실행")
    except Exception as e:
        print(f"[Curator] 에이전트 실패 — 폴백 실행: {e}")

    return _fallback_research(count, exclude_urls or [], preferences or {})
