"""
뉴스 큐레이션 에이전트

흐름:
  1. _tool_analyze_preferences — DB 선호도 분석
  2. _tool_find_ai_articles    — 웹 검색으로 기사 수집 (오버페치)
  3. 신선도·중복 필터 → 목표 미달이면 다른 토픽 각도로 톱업 재검색

기존 구현은 검색을 딱 1회만 수행하고 결과를 그대로 반환했다. 그래서
중복(이미 게시)이나 응답 절단으로 후보가 사라지면 그날 브리핑이
1건 또는 0건이 됐다. 이 모듈은 "목표 수량 확보"를 명시적 종료 조건으로 삼는다.

실행:
  python agents/news_curation_agent.py [--count N] [--topics topic1,topic2]
"""

import argparse
import json
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from concurrent.futures import ThreadPoolExecutor

import anthropic

import claude_search
import database as db
import recency
from agents.agent_spec import (
    DEFAULT_TOPICS,
    get_topic_keys,
    pillar_keys,
    pillar_label,
    pillar_max_age_days,
    pillar_of,
    pillar_topics,
    pillar_weight,
    topics_for_round,
)
from agents.search_prompt import build_search_prompt
from config import (
    ANTHROPIC_API_KEY,
    ARTICLES_PER_POST,
    CANDIDATES_PER_PUBLISHED,
    OVERFETCH_MAX,
    OVERFETCH_MIN,
    OVERFETCH_MULTIPLIER,
    PILLAR_SEARCH_WORKERS,
    TOPUP_MAX_ROUNDS,
)

__all__ = ["FatalSearchError", "build_search_prompt", "get_topic_keys", "run"]

# 예외의 본거지는 claude_search다. 여기서는 호출부 편의를 위해 재노출만 한다.
FatalSearchError = claude_search.FatalSearchError

_SYSTEM_RESEARCH = """\
You are a research librarian building a daily study feed for one reader: a working software engineer
who ships production code, uses coding agents every day, and also develops games as a client programmer.

You cover three areas, and the user prompt tells you which one this call is for:
  1. AI engineering practice — how people actually build with LLMs and coding agents, right now.
  2. AI for game development — using AI to produce the art, UI, audio, and animation a programmer
     cannot make by hand, plus real cases of games built with AI help.
  3. Graphics and cheap 3D — free or low-cost ways to generate and finish 3D assets, and graphics
     fundamentals a programmer can self-teach.

Rules, in priority order:
1. RECENCY IS A HARD GATE FOR DATED ARTICLES. The user prompt states today's date and a cutoff date.
   If you can determine an article was published before the cutoff, do not return it. The cutoff
   differs per area — an evergreen tutorial can be entirely valid when the window is wide.
2. You only have web_search — you cannot open pages, so you often cannot confirm a publication date.
   That is expected. Report the date when the search result, snippet, or URL gives you one; otherwise
   set "published_at" to "" and still return the article. Never guess a date and never report today's
   date for an undated page. Every candidate you return is fetched and date-checked downstream, so a
   missing date costs nothing while a dropped article cannot be recovered.
3. NEVER fabricate a URL. Return only links that appeared verbatim in your search results. Every URL
   is fetched downstream; an invented link is a guaranteed loss.
4. Prefer first-hand practical material — tutorials, how-to guides, case studies, postmortems, devlogs,
   high-signal discussion threads, newsletter issues — over announcements and roundups.
5. Spend your whole search budget. Each query you skip is a topic the reader never sees. Cover as many
   of the listed topics as your budget allows instead of filling the quota from one easy topic.
6. No sponsored content, no press releases, no 'awesome-list' repos, no content farms.
7. Output ONLY a valid JSON array — no preamble, no explanation. Return [] only when the searches
   genuinely surfaced nothing on topic; a partial list always beats an empty one."""


def _overfetch_target(target_count: int) -> int:
    """중복·본문검증·편집심사 손실을 흡수하기 위한 요청 수량."""
    return max(OVERFETCH_MIN, min(target_count * OVERFETCH_MULTIPLIER, OVERFETCH_MAX))


def _split_by_weight(total: int, pillars: list[str]) -> dict[str, int]:
    """가중치 비율대로 요청 수량을 필라에 배분한다. 각 필라는 최소 2건을 받는다."""
    if not pillars:
        return {}
    weights = {key: pillar_weight(key) for key in pillars}
    weight_sum = sum(weights.values()) or 1
    quota = {key: max(2, round(total * weights[key] / weight_sum)) for key in pillars}
    return quota


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


def _system_blocks() -> list[dict]:
    """article-finder 스킬을 붙인 system 프롬프트 (prompt caching 적용)."""
    from agents.agent_spec import SKILL_FINDER

    text = f"{_SYSTEM_RESEARCH}\n\n---\n\n{SKILL_FINDER}" if SKILL_FINDER else _SYSTEM_RESEARCH
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _normalize_article(raw: dict) -> dict | None:
    url = str(raw.get("url") or "").strip()
    title = str(raw.get("title") or "").strip()
    if not url or not title:
        return None

    keywords = raw.get("keywords")
    return {
        "url": url,
        "title": title,
        "source": str(raw.get("source") or "Unknown"),
        "description": str(raw.get("description") or "")[:500],
        "author": str(raw.get("author") or ""),
        "published_at": str(raw.get("published_at") or ""),
        "curator_reason": str(raw.get("curator_reason") or ""),
        "keywords": [str(k).strip() for k in keywords if str(k).strip()] if isinstance(keywords, list) else [],
    }


def _tool_find_ai_articles(
    client: anthropic.Anthropic,
    topics: list[str],
    count: int,
    already_collected: set[str],
    preferences: dict | None = None,
    intent: dict | None = None,
    max_age_days: int | None = None,
    round_index: int = 0,
    pillar: str | None = None,
) -> dict:
    """여러 토픽에 걸쳐 최신·고품질 AI 기사를 웹 검색으로 수집한다."""
    prompt = build_search_prompt(
        topics,
        count,
        already_collected,
        preferences,
        intent,
        max_age_days=max_age_days,
        round_index=round_index,
        pillar=pillar,
    )

    suffix = f"_{pillar}" if pillar else ""
    caller = f"agent_find{suffix}" if round_index == 0 else f"agent_find{suffix}_topup{round_index}"
    outcome = claude_search.search_articles(
        client,
        prompt=prompt,
        system_blocks=_system_blocks(),
        caller=caller,
    )

    cleaned = []
    for raw in outcome.articles:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_article(raw)
        if normalized is None:
            continue
        normalized["pillar"] = pillar or ""
        cleaned.append(normalized)
    return {
        "articles": cleaned,
        "count": len(cleaned),
        "stop_reason": outcome.stop_reason,
        "error": outcome.error,
        "fatal": outcome.fatal,
    }


# ── 에이전트 실행 ─────────────────────────────────────────────────────────────


def _apply_external_preferences(preferences: dict, external: dict | None) -> dict:
    """새벽 2시 분석 프로파일을 선호도 요약에 덮어씌운다."""
    if not external:
        return preferences
    hints = external.get("curation_hints", {})
    if not hints:
        return preferences

    preferences["liked_sources"] = hints.get("boost_sources", preferences["liked_sources"])
    preferences["disliked_sources"] = hints.get("avoid_sources", preferences["disliked_sources"])
    preferences["liked_keywords"] = hints.get("focus_keywords", preferences["liked_keywords"])
    preferences["curation_hints"] = hints
    preferences["summary"] += f" [외부 프로파일 적용: {hints.get('data_window', '')}]"
    print(f"[Agent] 외부 선호도 프로파일 적용 — {hints.get('data_window', '')}, 신뢰도: {hints.get('confidence', '')}")
    return preferences


def _plan_pillars(topics: list[str] | None) -> dict[str, list[str]]:
    """탐색할 필라 → 토픽 매핑. 토픽을 직접 지정하면 그 토픽이 속한 필라만 돈다."""
    if not topics:
        return {key: pillar_topics(key) for key in pillar_keys() if pillar_topics(key)}

    plan: dict[str, list[str]] = {}
    for topic in topics:
        plan.setdefault(pillar_of(topic) or "", []).append(topic)
    return {key: value for key, value in plan.items() if value}


def _absorb(
    articles: list[dict],
    collected: dict[str, dict],
    known_urls: set[str],
    dropped: dict[str, int],
    max_age_days: int,
) -> None:
    for article in articles:
        url = article["url"]
        if url in known_urls or url in collected:
            dropped["duplicate"] += 1
            continue
        if recency.is_stale(article["published_at"], max_age_days=max_age_days):
            dropped["stale"] += 1
            continue
        collected[url] = article


def run(
    target_count: int = ARTICLES_PER_POST,
    topics: list[str] | None = None,
    external_preferences: dict | None = None,
    intent: dict | None = None,
    max_age_days: int | None = None,
) -> list[dict]:
    """
    뉴스 큐레이션 에이전트를 실행한다.

    필라(주제군)마다 검색 호출을 분리해 동시에 보내고, 목표에 미달하면
    토픽을 회전시켜 최대 TOPUP_MAX_ROUNDS회 더 검색한다.

    반환량은 오버페치 때문에 target_count보다 많다. 남는 기사는 pipeline에서
    pending 저수지로 쌓여 다음 날의 보충분이 된다 — 저수지가 있어야 검색이
    부진한 날에도 0건이 나오지 않는다.

    Args:
        max_age_days: 상위 완화 루프가 넘기는 전역 상한. 지정하면 필라별
                      신선도와 비교해 더 넓은 쪽을 쓴다.

    Returns:
        선별된 기사 딕셔너리 목록 (최신순)
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY가 .env에 설정되지 않았습니다.")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    intent_age = recency.max_age_from_intent(intent)
    plan = _plan_pillars(topics)
    if not plan:
        plan = {"": list(topics or DEFAULT_TOPICS)}
    want = _overfetch_target(target_count)
    quota = _split_by_weight(want, list(plan))
    # 톱업은 라운드당 필라 수만큼 호출이 더 나간다(실측 약 $1). 이미 목표를 채우고도
    # 남을 후보를 확보했으면 돌리지 않는다.
    sufficient = min(want, max(target_count, round(target_count * CANDIDATES_PER_PUBLISHED)))

    def window_for(pillar: str) -> int:
        """필라 신선도. 의도(intent)가 더 좁으면 의도를 따르고, 완화 루프가 더 넓으면 그쪽을 쓴다."""
        base = pillar_max_age_days(pillar, intent_age) if pillar else intent_age
        window = min(base, intent_age) if intent and intent.get("active") else base
        return max(window, max_age_days) if max_age_days else window

    summary = " / ".join(f"{pillar_label(key)} {quota.get(key, 0)}건({window_for(key)}일)" for key in plan)
    print(f"[Agent] 시작 — 목표 {target_count}개 (요청 {want}개) · {summary}")

    preferences = _apply_external_preferences(_tool_analyze_preferences(), external_preferences)
    print(f"[Agent] 선호도 분석 → {preferences['summary']}")

    known_urls = db.get_all_article_urls()
    collected: dict[str, dict] = {}
    dropped = {"duplicate": 0, "stale": 0}

    for round_index in range(1 + TOPUP_MAX_ROUNDS):
        if round_index and len(collected) >= sufficient:
            print(f"[Agent] 톱업 생략 — 후보 {len(collected)}개 ≥ 충분 기준 {sufficient}개")
            break

        pending = list(plan)

        def search(pillar: str, _round: int = round_index) -> tuple[str, dict]:
            return pillar, _tool_find_ai_articles(
                client,
                topics_for_round(plan[pillar], _round),
                quota.get(pillar, max(2, target_count)),
                set(collected),
                preferences=preferences,
                intent=intent,
                max_age_days=window_for(pillar),
                round_index=_round,
                pillar=pillar or None,
            )

        workers = max(1, min(PILLAR_SEARCH_WORKERS, len(pending)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(search, pending))

        fatal = next((result["error"] for _pillar, result in outcomes if result.get("fatal")), None)
        for pillar, result in outcomes:
            before = len(collected)
            _absorb(result["articles"], collected, known_urls, dropped, window_for(pillar))
            print(
                f"[Agent] R{round_index} {pillar_label(pillar) if pillar else '전체'}: "
                f"반환 {result['count']}개 → 신규 {len(collected) - before}개"
            )

        print(
            f"[Agent] 라운드 {round_index} 누적 {len(collected)}개 "
            f"(중복 {dropped['duplicate']} / 기한초과 {dropped['stale']})"
        )

        if fatal:
            # 크레딧 소진·인증 실패는 다시 불러도 같은 답이다. 톱업 라운드를 멈추고
            # 상위로 올려 보내 파이프라인이 남은 완화 패스까지 낭비하지 않게 한다.
            print("[Agent] 복구 불가 API 오류 — 추가 검색을 중단합니다.")
            raise claude_search.FatalSearchError(fatal)

    articles = sorted(collected.values(), key=lambda a: a["published_at"], reverse=True)
    if len(articles) < target_count:
        print(f"[Agent] 경고 — 목표 {target_count}개 미달, {len(articles)}개만 확보 (feed 보충 필요)")
    print(f"[Agent] 완료 — {len(articles)}개 선별")
    return articles


# ── CLI 진입점 ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 뉴스 큐레이션 에이전트")
    parser.add_argument(
        "--count", type=int, default=ARTICLES_PER_POST, help=f"선별할 기사 수 (기본: {ARTICLES_PER_POST})"
    )
    parser.add_argument(
        "--topics", type=str, default="", help=f"탐색 토픽 콤마 구분 (기본: {','.join(DEFAULT_TOPICS)})"
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
        print(
            f"   출처: {a.get('source', '-')} | {a.get('published_at', '-')} ({recency.describe(a.get('published_at'))})"
        )
        print(f"   URL: {a.get('url', '-')}")
        reason = a.get("curator_reason", "")
        if reason:
            print(f"   💡 {reason}")

    print("\n[JSON 출력]")
    print(json.dumps(articles, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
