"""
Claude 기반 AI 뉴스 큐레이션 엔진

흐름:
  1. web_search 도구로 2–4회 검색
  2. 개발자 워크플로우 중심 아티클 수집 (튜토리얼, 프롬프트 엔지니어링, AI 코딩 도구 등)
  3. JSON 배열로 반환
"""
import json
import time
import anthropic

from crawlers.base import Article
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
import token_tracker

# ─── 시스템 프롬프트 ──────────────────────────────────────────────────────────

_SYSTEM_RESEARCH = """\
You are a focused researcher finding high-quality articles for developers who use AI tools daily.
Your task is to find practical, actionable content — NOT general AI news.

Target reader: software engineer who uses Claude Code, Cursor, or similar AI coding tools.

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


# ─── 공개 API ─────────────────────────────────────────────────────────────────

def research(
    count: int,
    exclude_urls: list[str] | None = None,
    preferences: dict | None = None,
) -> list[Article]:
    """
    웹 검색으로 개발자용 AI 아티클을 수집합니다.

    Args:
        count:        수집할 기사 수
        exclude_urls: 이미 게시된 URL 목록 (중복 방지)
        preferences:  DB 소스/키워드 선호도

    Returns:
        Article 리스트 (len ≤ count)
    """
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY가 .env에 설정되어 있지 않습니다.")

    exclude_urls = exclude_urls or []
    preferences = preferences or {}

    liked_kws = [k["keyword"] for k in preferences.get("keywords", []) if k["multiplier"] > 1.1][:5]
    pref_hint = f"\nUser enjoys these topics: {', '.join(liked_kws)}" if liked_kws else ""

    lines = [
        f"Find {count} high-quality articles for developers who use AI coding tools like Claude Code or Cursor.",
        "Focus on: practical tutorials, prompt engineering tips, AI coding workflows, MCP/tool-use guides, developer productivity.",
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
            caller="curator_research",
            elapsed_seconds=round(time.perf_counter() - _t0, 2),
        )

        for block in response.content:
            if block.type == "text":
                data = _extract_json_array(block.text)
                if data:
                    print(f"[Curator] 완료: {len(data)}개 수집")
                    return _to_articles(data[:count])

    except anthropic.RateLimitError:
        print("[Curator] RateLimit — 30초 대기 후 재시도")
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
                caller="curator_research_retry",
                elapsed_seconds=round(time.perf_counter() - _t0, 2),
            )
            for block in response.content:
                if block.type == "text":
                    data = _extract_json_array(block.text)
                    if data:
                        print(f"[Curator] 재시도 완료: {len(data)}개 수집")
                        return _to_articles(data[:count])
        except Exception as e:
            print(f"[Curator] 재시도 실패: {e}")

    except anthropic.APIStatusError as e:
        print(f"[Curator] API 오류 ({e.status_code}): {e}")

    except Exception as e:
        print(f"[Curator] 예상치 못한 오류: {e}")

    return []
