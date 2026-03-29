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
from config import ANTHROPIC_API_KEY

# ─── 라운드별 리서치 토픽 ────────────────────────────────────────────────────
# (name, search_instruction)  — 순서대로 각 라운드에 배정됨
_TOPICS: list[tuple[str, str]] = [
    (
        "models",
        "Latest AI model releases, benchmark results, and model evaluations published in the last 48 hours. "
        "Include GPT-4o, Claude, Gemini, Llama, Mistral, and other major models.",
    ),
    (
        "company_news",
        "AI company announcements: funding rounds, product launches, acquisitions, partnerships, "
        "and safety/policy statements from OpenAI, Anthropic, Google, Meta, xAI, Mistral, etc. (last 48h)",
    ),
    (
        "arxiv_papers",
        "Notable ArXiv preprints in cs.AI, cs.LG, cs.CL submitted in the last 48 hours. "
        "Focus on papers with significant findings or from well-known research groups.",
    ),
    (
        "dev_tools",
        "New AI developer tools, open-source releases, frameworks, and APIs launched this week. "
        "Include HuggingFace releases, LangChain updates, new inference engines, etc.",
    ),
    (
        "korean_news",
        "한국어 AI 뉴스 — 최근 48시간 이내 발표된 인공지능 관련 소식. "
        "검색어: '인공지능 최신', 'LLM 발표', 'AI 스타트업', '딥러닝 논문', 'AI 규제'. "
        "출처: IT조선, AI타임스, ZDNet Korea, 전자신문, 네이버 뉴스.",
    ),
    (
        "safety_policy",
        "AI safety, alignment, ethics, and government policy news from the last 48 hours. "
        "EU AI Act, US AI policy, alignment research, red-teaming results, safety papers.",
    ),
    (
        "research_labs",
        "Research breakthroughs and technical blog posts from major AI labs: "
        "DeepMind, FAIR, Microsoft Research, Stanford HAI, MIT CSAIL, CMU (last 48h).",
    ),
    (
        "applications",
        "Real-world AI applications launched or announced recently: robotics, healthcare, "
        "autonomous vehicles, creative tools, enterprise AI, coding assistants (last 48h).",
    ),
    (
        "community_buzz",
        "Viral AI discussions, influential posts from key researchers (Karpathy, LeCun, Bengio, "
        "Altman, Hassabis), and trending HackerNews AI threads from the last 24 hours.",
    ),
    (
        "hardware_infra",
        "AI hardware and infrastructure news: new GPU/TPU/NPU releases, data center investments, "
        "inference optimization, chip announcements from Nvidia, AMD, Intel, Groq (last 48h).",
    ),
]

# ─── Ralph Loop 활성화 플래그 ────────────────────────────────────────────────
# 임시 비활성화 시 False로 설정 → curate() 호출 시 빈 리스트 반환
RALPH_LOOP_ENABLED = False

# ─── 시스템 프롬프트 ──────────────────────────────────────────────────────────

_SYSTEM_RESEARCH = """\
You are a focused AI news researcher. Your task is to find the most important recent AI news
on a specific topic using web search.

Rules:
- Only articles published within the last 48 hours
- No listicles, roundups of old news, or sponsored content
- 2–4 targeted searches, then output JSON immediately
- If nothing relevant found, output an empty array []
- Output ONLY valid JSON — no preamble, no explanation"""

_SYSTEM_SELECT = """\
You are an expert AI news curator making final editorial decisions.
You will receive a list of candidate articles and must select the best ones for a daily briefing.
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
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4000,
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            system=_SYSTEM_RESEARCH,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        ) as stream:
            response = stream.get_final_message()

    except anthropic.RateLimitError:
        print(f"[Curator] R{round_num} ({topic_name}): RateLimit — 30초 대기 후 재시도")
        time.sleep(30)
        try:
            with client.messages.stream(
                model="claude-opus-4-6",
                max_tokens=4000,
                tools=[{"type": "web_search_20260209", "name": "web_search"}],
                system=_SYSTEM_RESEARCH,
                messages=[{"role": "user", "content": "\n".join(lines)}],
            ) as stream:
                response = stream.get_final_message()
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

    prompt = f"""You have {len(candidates)} AI news articles collected across multiple research rounds.
Select the best {target_count} for a daily Discord briefing.
{pref_block}

## Selection Criteria
1. **Recency** — prefer articles from the last 24h over 24–48h
2. **Significance** — major announcements > minor updates
3. **Diversity** — mix of topics (models, research, tools, company news, Korean news)
4. **No near-duplicates** — if two articles cover the same event, keep only the best one
5. **Quality** — prefer primary sources over secondary coverage

## Candidates
{candidates_json}

Output ONLY a JSON array of exactly {target_count} items selected from the candidates above.
Preserve all original fields. Add or improve `curator_reason` if missing or weak."""

    try:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=8000,
            system=_SYSTEM_SELECT,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "text":
                data = _extract_json_array(block.text)
                if data:
                    return data[:target_count]

    except Exception as e:
        print(f"[Curator] 최종 선별 실패: {e} — 수집 순서대로 상위 {target_count}개 반환")

    return candidates[:target_count]


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
        print("[Curator] Ralph Loop 비활성화 상태 — 빈 리스트 반환")
        return []

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
