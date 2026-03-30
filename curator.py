"""
Claude 기반 AI 뉴스 큐레이션 엔진 — Ralph Loop (다중 라운드)

흐름:
  1. [Phase 1] 토픽별 최대 10라운드 소형 리서치 호출 (라운드당 ~4 000 토큰)
     - 라운드마다 다른 토픽 영역 집중 → 다양성 보장
     - RateLimit / 토큰 초과 시 해당 라운드만 건너뜀 (전체 실패 방지)
     - 목표의 3배 이상 후보 확보 시 조기 종료
  2. [Phase 2] 전체 수집분 URL 기준 중복 제거 → 후보 풀 구성
  3. [Phase 3] web_search 없이 판단 전용 호출로 최종 N개 선별
"""
import json
import time
import anthropic

from crawlers.base import Article
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
import token_tracker

# ─── 라운드별 리서치 토픽 ────────────────────────────────────────────────────
# (name, search_instruction)  — 순서대로 각 라운드에 배정됨
_TOPICS: list[tuple[str, str]] = [
    (
        "claude_code_tips",
        "Articles, blog posts, and tutorials about using Claude Code CLI effectively: "
        "tips, workflows, slash commands, hooks, MCP servers, CLAUDE.md patterns, agentic coding techniques. "
        "Search: 'Claude Code tips', 'Claude Code workflow', 'Claude Code tutorial', 'Claude Code MCP'.",
    ),
    (
        "prompt_engineering",
        "Practical guides and best practices for prompt engineering with LLMs: "
        "system prompt design, chain-of-thought, few-shot examples, structured output, context management. "
        "Focus on actionable techniques developers can apply immediately. (last 7 days)",
    ),
    (
        "ai_coding_tools",
        "How-to articles and comparisons for AI coding assistants: Cursor, GitHub Copilot, Codeium, "
        "Windsurf, Aider, and similar tools. Real usage patterns, productivity tips, configuration guides. "
        "Search: 'AI coding assistant tips', 'Cursor workflow', 'Copilot best practices'. (last 7 days)",
    ),
    (
        "mcp_tools",
        "Tutorials and articles about Model Context Protocol (MCP): building MCP servers, "
        "integrating tools with Claude, agent tool-use patterns, function calling best practices. "
        "Search: 'MCP server tutorial', 'Claude tool use', 'model context protocol guide'. (last 7 days)",
    ),
    (
        "dev_productivity",
        "Articles about AI-assisted developer workflows: using LLMs for code review, "
        "test generation, documentation, refactoring, debugging. Real practitioner case studies "
        "and measurable productivity improvements. (last 7 days)",
    ),
    (
        "llm_best_practices",
        "Technical articles on working effectively with LLMs in production: context window management, "
        "RAG patterns, structured output, error handling, cost optimization, latency reduction. "
        "Targeted at developers integrating LLMs into their stack. (last 7 days)",
    ),
    (
        "agent_patterns",
        "Guides and tutorials on building AI agents: multi-agent systems, planning loops, "
        "tool orchestration, LangChain/LlamaIndex/CrewAI/AutoGen patterns, agentic coding workflows. "
        "Search: 'AI agent tutorial', 'agentic coding', 'LLM agent pattern'. (last 7 days)",
    ),
    (
        "korean_practitioner",
        "한국어 AI 활용 아티클 — 개발자를 위한 Claude, ChatGPT, Cursor 실전 사용법, "
        "프롬프트 엔지니어링 팁, AI 코딩 워크플로우 가이드. "
        "검색어: 'Claude Code 사용법', '프롬프트 엔지니어링', 'AI 코딩 도구', 'LLM 개발 팁'. "
        "출처: 개인 기술 블로그, 브런치, 벨로그, 미디엄 한국어.",
    ),
    (
        "community_tips",
        "Developer community discussions about AI tool usage: HackerNews threads, Reddit r/ClaudeAI "
        "r/LocalLLaMA, Twitter/X threads from practitioners sharing concrete tips and workflows. "
        "Focus on posts with real code examples or measurable results. (last 7 days)",
    ),
    (
        "tutorials_deep_dive",
        "In-depth technical tutorials: step-by-step guides for building AI-powered apps, "
        "integrating Claude/OpenAI APIs, fine-tuning workflows, embeddings and vector databases "
        "for developers. Search: 'Claude API tutorial', 'LLM app tutorial'. (last 7 days)",
    ),
]

# ─── Ralph Loop 활성화 플래그 ────────────────────────────────────────────────
# False → 단일 프롬프트 호출(웹 검색 1회)로 대체  /  True → 다중 라운드 루프
RALPH_LOOP_ENABLED = False

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

_SYSTEM_SELECT = """\
You are a senior developer curating a daily briefing for AI-tool practitioners.
You will receive candidate articles and must select the best ones.
Prefer: actionable content, concrete examples, real workflow improvements.
Reject: general AI news, hype without substance, duplicate coverage.
Output ONLY valid JSON — no explanation, no preamble."""


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


# ─── Phase 1: 단일 토픽 리서치 라운드 ────────────────────────────────────────

def _research_round(
    client: anthropic.Anthropic,
    round_num: int,
    topic_name: str,
    topic_desc: str,
    exclude_urls: list[str],
    already_found_urls: set[str],
    count: int,
) -> list[dict]:
    """
    단일 리서치 라운드: 지정 토픽에 집중해 기사를 수집합니다.
    실패 시 빈 리스트를 반환해 전체 루프가 중단되지 않도록 합니다.
    """
    all_excluded = list(set(exclude_urls) | already_found_urls)

    lines = [
        f"Find up to {count} AI news articles specifically about:",
        f"**{topic_desc}**",
        "",
        "Requirements: published within 48 hours, real news only.",
        "Run 2–4 targeted searches, then output JSON.",
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
            caller=f"curator_research_r{round_num}_{topic_name}",
            elapsed_seconds=round(time.perf_counter() - _t0, 2),
        )

    except anthropic.RateLimitError:
        print(f"[Curator] R{round_num} ({topic_name}): RateLimit — 30초 대기 후 재시도")
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
                caller=f"curator_research_r{round_num}_{topic_name}_retry",
                elapsed_seconds=round(time.perf_counter() - _t0, 2),
            )
        except Exception as e:
            print(f"[Curator] R{round_num} ({topic_name}): 재시도 실패 → 건너뜀 ({e})")
            return []

    except anthropic.APIStatusError as e:
        # 529 (Overloaded) 등 서버 오류
        print(f"[Curator] R{round_num} ({topic_name}): API 오류 ({e.status_code}) → 건너뜀")
        return []

    except Exception as e:
        print(f"[Curator] R{round_num} ({topic_name}): 예상치 못한 오류 → 건너뜀 ({e})")
        return []

    for block in response.content:
        if block.type == "text":
            data = _extract_json_array(block.text)
            if data:
                return data

    return []


# ─── Phase 3: 후보 풀에서 최종 선별 ─────────────────────────────────────────

def _select_best(
    client: anthropic.Anthropic,
    candidates: list[dict],
    target_count: int,
    preferences: dict,
) -> list[dict]:
    """
    웹 검색 없이 후보 목록만 보고 최종 N개를 선별합니다.
    candidates가 target_count 이하면 그대로 반환합니다.
    """
    if len(candidates) <= target_count:
        return candidates

    # 선호도 힌트 구성
    liked_sources = [s["source"] for s in preferences.get("sources", []) if s["multiplier"] > 1.1][:5]
    disliked_sources = [s["source"] for s in preferences.get("sources", []) if s["multiplier"] < 0.9][:5]
    liked_kws = [k["keyword"] for k in preferences.get("keywords", []) if k["multiplier"] > 1.1][:10]

    pref_lines = []
    if liked_sources:
        pref_lines.append(f"User prefers these sources: {', '.join(liked_sources)}")
    if disliked_sources:
        pref_lines.append(f"User dislikes these sources: {', '.join(disliked_sources)}")
    if liked_kws:
        pref_lines.append(f"User enjoys these topics: {', '.join(liked_kws)}")
    pref_block = ("\n## User Preferences (soft signals)\n" + "\n".join(pref_lines)) if pref_lines else ""

    candidates_json = json.dumps(candidates, ensure_ascii=False, indent=2)

    prompt = f"""You have {len(candidates)} articles collected for developers who use AI coding tools.
Select the best {target_count} for a daily Discord briefing.
{pref_block}

## Selection Criteria
1. **Actionability** — contains concrete tips, code, or step-by-step techniques a developer can apply today
2. **Relevance** — directly useful for someone using Claude Code, Cursor, or similar AI dev tools
3. **Quality** — tutorial/guide > case study > opinion > news announcement
4. **Diversity** — mix across: prompting, coding tools, agents, workflows, Korean content
5. **No near-duplicates** — if two articles cover the same technique, keep only the best one
6. **Depth** — prefer in-depth content over surface-level listicles

## Candidates
{candidates_json}

Output ONLY a JSON array of exactly {target_count} items selected from the candidates above.
Preserve all original fields. Add or improve `curator_reason` if missing or weak."""

    try:
        _t0 = time.perf_counter()
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8000,
            system=_SYSTEM_SELECT,
            messages=[{"role": "user", "content": prompt}],
        )
        token_tracker.log_token_usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            caller="curator_select",
            elapsed_seconds=round(time.perf_counter() - _t0, 2),
        )
        for block in response.content:
            if block.type == "text":
                data = _extract_json_array(block.text)
                if data:
                    return data[:target_count]

    except Exception as e:
        print(f"[Curator] 최종 선별 실패: {e} — 수집 순서대로 상위 {target_count}개 반환")

    return candidates[:target_count]


# ─── 단일 호출 모드 (RALPH_LOOP_ENABLED = False) ─────────────────────────────

def _single_research(
    client: anthropic.Anthropic,
    count: int,
    exclude_urls: list[str],
    preferences: dict,
) -> list[Article]:
    """
    웹 검색 1회 호출로 AI 뉴스를 수집합니다.
    Ralph Loop 비활성화 시 폴백으로 사용됩니다.
    """
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
            caller="curator_single",
            elapsed_seconds=round(time.perf_counter() - _t0, 2),
        )

        for block in response.content:
            if block.type == "text":
                data = _extract_json_array(block.text)
                if data:
                    print(f"[Curator] 단일 호출 완료: {len(data)}개 수집")
                    return _to_articles(data[:count])

    except Exception as e:
        print(f"[Curator] 단일 호출 실패: {e}")

    return []


# ─── 공개 API ─────────────────────────────────────────────────────────────────

def curate(
    target_count: int = 5,
    exclude_urls: list[str] | None = None,
    preferences: dict | None = None,
    max_rounds: int = 10,
) -> list[Article]:
    """
    Ralph Loop로 AI 뉴스를 큐레이션합니다.

    Phase 1 — 토픽별 소형 리서치 라운드 (최대 max_rounds회)
              각 라운드는 독립적으로 실패 허용 (새벽 토큰 초과 대비)
              후보가 target_count × 3 이상이면 조기 종료
    Phase 2 — URL 기준 중복 제거 → 후보 풀 구성
    Phase 3 — 웹 검색 없이 판단 전용 호출로 최종 N개 선별

    Args:
        target_count:  최종 선정 기사 수
        exclude_urls:  오늘 이미 게시한 URL 목록 (중복 방지)
        preferences:   DB 소스/키워드 선호도
        max_rounds:    최대 리서치 라운드 수 (1–10)

    Returns:
        Article 리스트 (len ≤ target_count)
    """
    if not RALPH_LOOP_ENABLED:
        print("[Curator] Ralph Loop 비활성화 상태 — 단일 호출 모드로 실행")
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY가 .env에 설정되어 있지 않습니다.")
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        return _single_research(client, target_count, exclude_urls or [], preferences or {})

    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY가 .env에 설정되어 있지 않습니다.")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    exclude_urls = exclude_urls or []
    preferences = preferences or {}
    max_rounds = max(1, min(max_rounds, len(_TOPICS)))

    # ── Phase 1: 다중 라운드 리서치 ──────────────────────────────────────────
    all_raw: dict[str, dict] = {}   # url → article dict (중복 방지)
    early_stopped = False

    per_round_count = max(3, (target_count + 1) // 2)   # 라운드당 요청 기사 수

    for round_num in range(1, max_rounds + 1):
        topic_name, topic_desc = _TOPICS[round_num - 1]
        already_found = set(all_raw.keys())

        print(f"[Curator] 라운드 {round_num}/{max_rounds} — {topic_name} "
              f"(누적 {len(all_raw)}개)")

        raw = _research_round(
            client=client,
            round_num=round_num,
            topic_name=topic_name,
            topic_desc=topic_desc,
            exclude_urls=exclude_urls,
            already_found_urls=already_found,
            count=per_round_count,
        )

        new_count = 0
        for item in raw:
            url = (item.get("url") or "").strip()
            if url and url not in all_raw and url not in exclude_urls:
                all_raw[url] = item
                new_count += 1

        print(f"[Curator]   → +{new_count}개 신규 (총 {len(all_raw)}개)")

        # 조기 종료 조건: 목표의 3배 이상 확보
        if len(all_raw) >= target_count * 3:
            print(f"[Curator] 후보 충분 ({len(all_raw)}개 ≥ {target_count * 3}) — 조기 종료")
            early_stopped = True
            break

        # 라운드 간 짧은 대기 (과도한 API 호출 방지)
        if round_num < max_rounds and not early_stopped:
            time.sleep(1)

    if not all_raw:
        print("[Curator] 모든 라운드에서 기사를 수집하지 못했습니다.")
        return []

    # ── Phase 2: URL 기준 중복 제거 완료 (all_raw가 이미 중복 없음) ──────────
    candidates = list(all_raw.values())
    print(f"[Curator] 후보 풀: {len(candidates)}개 → 최종 {target_count}개 선별 시작")

    # ── Phase 3: 최종 선별 ───────────────────────────────────────────────────
    selected = _select_best(client, candidates, target_count, preferences)
    articles = _to_articles(selected)

    print(f"[Curator] 완료: {len(articles)}개 선정 "
          f"({max_rounds if not early_stopped else round_num}라운드 실행)")
    return articles
