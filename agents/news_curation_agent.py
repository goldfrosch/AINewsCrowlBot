"""
뉴스 큐레이션 에이전트

2단계를 순서대로 직접 실행한다:
  1. _tool_analyze_preferences — DB 선호도 분석
  2. _tool_find_ai_articles    — 웹 검색 최대 2회, 목표 수만큼 기사 수집 후 반환

실행:
  python agents/news_curation_agent.py [--count N] [--topics topic1,topic2]
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import yaml

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic

import database as db
import token_tracker
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

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
- Maximum 2 targeted searches, then output JSON immediately
- If nothing relevant found, output an empty array []
- Output ONLY valid JSON — no preamble, no explanation"""


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


# ── 문서 로더 ─────────────────────────────────────────────────────────────────

_CLAUDE_DIR = Path(__file__).resolve().parent.parent / ".claude"
_AGENT_DOC_PATH = _CLAUDE_DIR / "agents" / "news-curation-agent.md"
_SKILLS_DIR = _CLAUDE_DIR / "skills"


def _strip_frontmatter(text: str) -> str:
    """YAML 프론트매터(--- ... ---)를 제거하고 본문만 반환한다."""
    match = re.match(r"^---\n.*?\n---\n(.*)$", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def _load_agent_spec() -> dict:
    """
    .claude/agents/news-curation-agent.md 에서 토픽 설정을 로드한다.

    반환 키:
        topics         — dict[str, str]  토픽명 → 설명
        default_topics — list[str]       기본 탐색 토픽 목록
    """
    text = _AGENT_DOC_PATH.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValueError(f"에이전트 문서 형식 오류: {_AGENT_DOC_PATH}")
    frontmatter = yaml.safe_load(match.group(1))
    return {
        "topics": frontmatter.get("topics", {}),
        "default_topics": frontmatter.get("default_topics", []),
    }


def _load_skill(name: str) -> str:
    """
    .claude/skills/{name}.md 를 읽어 프론트매터를 제거한 본문을 반환한다.
    파일이 없으면 빈 문자열을 반환한다.
    """
    path = _SKILLS_DIR / f"{name}.md"
    if not path.exists():
        return ""
    return _strip_frontmatter(path.read_text(encoding="utf-8"))


# ── 에이전트·스킬 문서에서 상수 로드 ─────────────────────────────────────────

_AGENT_SPEC = _load_agent_spec()
_TOPIC_DESC: dict[str, str] = _AGENT_SPEC["topics"]
_DEFAULT_TOPICS: list[str] = _AGENT_SPEC["default_topics"]

# article-finder skill 본문 (모듈 로드 시 1회만 읽음)
_SKILL_FINDER: str = _load_skill("article-finder")


# ── 도구 구현 ─────────────────────────────────────────────────────────────────


def _tool_analyze_preferences() -> dict:
    """DB에서 선호도 데이터를 읽어 요약 딕셔너리를 반환한다."""
    prefs = db.get_all_preferences()

    liked_sources = [s for s in prefs["sources"] if s["multiplier"] > 1.1][:5]
    disliked_sources = [s for s in prefs["sources"] if s["multiplier"] < 0.9][:5]
    liked_keywords = [k for k in prefs["keywords"] if k["multiplier"] > 1.1][:10]
    total_feedback = sum(s["total_likes"] + s["total_dislikes"] for s in prefs["sources"])

    return {
        "liked_sources": [s["source"] for s in liked_sources],
        "disliked_sources": [s["source"] for s in disliked_sources],
        "liked_keywords": [k["keyword"] for k in liked_keywords],
        "total_feedback": total_feedback,
        "summary": (
            f"피드백 누적 {total_feedback}건 | "
            f"선호 소스 {len(liked_sources)}개, 비선호 소스 {len(disliked_sources)}개, "
            f"선호 키워드 {len(liked_keywords)}개"
        ),
    }


def _tool_find_ai_articles(
    client: anthropic.Anthropic,
    topics: list[str],
    count: int,
    already_collected: set[str],
) -> dict:
    """여러 토픽에 걸쳐 최신·고품질 AI 기사를 웹 검색으로 수집한다."""
    topic_lines = [f"- {t}: {_TOPIC_DESC.get(t, t)}" for t in topics]

    exclude_urls = db.get_todays_posted_urls()
    all_excluded = list(set(exclude_urls) | already_collected)

    lines = [
        f"Find {count} high-quality, RECENT (within 48 hours) AI articles from ANY of these topics:",
        "",
        *topic_lines,
        "",
        "Rules:",
        "- Pick the BEST articles regardless of topic distribution — quality and recency first",
        "- Real articles only, no sponsored content, no press releases",
        "- Run at most 2 targeted searches, then output JSON",
        "",
    ]
    if all_excluded:
        lines.append("Skip these URLs (already collected):")
        for url in all_excluded[:40]:
            lines.append(f"- {url}")
        lines.append("")
    lines += [
        f"Output a JSON array of up to {count} items:",
        '[{"url":"...","title":"...","source":"...","description":"2-3 sentences","author":"","published_at":"YYYY-MM-DD","curator_reason":"one sentence"}]',
        "If nothing found: []",
    ]

    # article-finder skill을 system prompt에 주입
    finder_system = _SYSTEM_RESEARCH
    if _SKILL_FINDER:
        finder_system = f"{_SYSTEM_RESEARCH}\n\n---\n\n{_SKILL_FINDER}"

    raw_articles: list[dict] = []
    try:
        _t0 = time.perf_counter()
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            system=finder_system,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        ) as stream:
            response = stream.get_final_message()
        token_tracker.log_token_usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            caller="agent_find_articles",
            elapsed_seconds=round(time.perf_counter() - _t0, 2),
        )
        for block in response.content:
            if block.type == "text":
                data = _extract_json_array(block.text)
                if data:
                    raw_articles = data
                    break
    except anthropic.RateLimitError:
        print(f"[FindArticles] ({topics}): RateLimit — 30초 대기 후 재시도")
        time.sleep(30)
        try:
            _t0 = time.perf_counter()
            with client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                tools=[{"type": "web_search_20260209", "name": "web_search"}],
                system=finder_system,
                messages=[{"role": "user", "content": "\n".join(lines)}],
            ) as stream:
                response = stream.get_final_message()
            token_tracker.log_token_usage(
                response.usage.input_tokens,
                response.usage.output_tokens,
                caller="agent_find_articles_retry",
                elapsed_seconds=round(time.perf_counter() - _t0, 2),
            )
            for block in response.content:
                if block.type == "text":
                    data = _extract_json_array(block.text)
                    if data:
                        raw_articles = data
                        break
        except Exception as e:
            print(f"[FindArticles] 재시도 실패 → 건너뜀 ({e})")
    except Exception as e:
        print(f"[FindArticles] 오류 → 건너뜀 ({e})")

    # 필드 정규화
    cleaned = [
        {
            "url": a.get("url", "").strip(),
            "title": a.get("title", "제목 없음"),
            "source": a.get("source", "Unknown"),
            "description": a.get("description", "")[:500],
            "published_at": a.get("published_at", ""),
            "curator_reason": a.get("curator_reason", ""),
            "keywords": a.get("keywords", []),
        }
        for a in raw_articles
        if a.get("url") and a.get("title")
    ]

    return {
        "articles": cleaned,
        "count": len(cleaned),
        "message": f"{len(cleaned)}개 기사 수집",
    }


# ── 에이전트 실행 ─────────────────────────────────────────────────────────────


def run(
    target_count: int = 5,
    topics: list[str] | None = None,
    external_preferences: dict | None = None,
) -> list[dict]:
    """
    뉴스 큐레이션 에이전트를 실행한다.

    Args:
        target_count:         최종 선별 기사 수
        topics:               탐색할 토픽 목록 (None이면 기본값)
        external_preferences: 새벽 2시 선호도 분석으로 미리 생성된 프로파일.
                              있으면 analyze_preferences 결과를 이것으로 대체한다.

    Returns:
        선별된 기사 딕셔너리 목록
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY가 .env에 설정되지 않았습니다.")

    topics = topics or _DEFAULT_TOPICS
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    print(f"[Agent] 시작 — 목표 {target_count}개 / 토픽: {', '.join(topics)}")

    # 1단계: 선호도 분석
    preferences = _tool_analyze_preferences()
    if external_preferences:
        hints = external_preferences.get("curation_hints", {})
        if hints:
            preferences["liked_sources"] = hints.get("boost_sources", preferences["liked_sources"])
            preferences["disliked_sources"] = hints.get("avoid_sources", preferences["disliked_sources"])
            preferences["liked_keywords"] = hints.get("focus_keywords", preferences["liked_keywords"])
            preferences["summary"] += f" [외부 프로파일 적용: {hints.get('data_window', '')}]"
            print(
                f"[Agent] 외부 선호도 프로파일 적용 — {hints.get('data_window', '')}, 신뢰도: {hints.get('confidence', '')}"
            )
    print(f"[Agent] 선호도 분석 → {preferences['summary']}")

    # 2단계: 기사 탐색
    result = _tool_find_ai_articles(client, topics, target_count, set())
    articles = result.get("articles", [])

    print(f"[Agent] 완료 — {len(articles)}개 선별")
    return articles


# ── CLI 진입점 ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 뉴스 큐레이션 에이전트")
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="선별할 기사 수 (기본: 5)",
    )
    parser.add_argument(
        "--topics",
        type=str,
        default="",
        help="탐색 토픽 콤마 구분 (기본: models,company_news,arxiv_papers,dev_tools,korean_news)",
    )
    args = parser.parse_args()

    topics = [t.strip() for t in args.topics.split(",") if t.strip()] or None

    db.init_db()

    try:
        articles = run(target_count=args.count, topics=topics)
    except RuntimeError as e:
        print(f"[Agent] 오류: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"최종 선별 기사 {len(articles)}개")
    print("=" * 60)
    for i, a in enumerate(articles, 1):
        print(f"\n{i}. {a.get('title', '제목 없음')}")
        print(f"   출처: {a.get('source', '-')} | {a.get('published_at', '-')}")
        print(f"   URL: {a.get('url', '-')}")
        reason = a.get("curator_reason", "")
        if reason:
            print(f"   💡 {reason}")

    print("\n[JSON 출력]")
    print(json.dumps(articles, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
