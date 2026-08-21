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

import anthropic

import claude_search
import database as db
import recency
from agents.agent_spec import DEFAULT_TOPICS, get_topic_keys, topics_for_round
from agents.search_prompt import build_search_prompt
from config import (
    ANTHROPIC_API_KEY,
    OVERFETCH_MAX,
    OVERFETCH_MIN,
    OVERFETCH_MULTIPLIER,
    TOPUP_MAX_ROUNDS,
)

__all__ = ["build_search_prompt", "get_topic_keys", "run"]

_SYSTEM_RESEARCH = """\
You are a focused researcher finding RECENT, high-quality articles for developers who build and operate
AI systems AND developers who use AI to enhance game development — especially in areas programmers
can't easily do themselves (3D modeling, UI/UX design, textures, animation, sound/music, character design).

Rules, in priority order:
1. RECENCY IS A HARD GATE. The user prompt states today's date and a cutoff date. Any article published
   before the cutoff is worthless and must not be returned, no matter how good it is.
2. Every article needs a verifiable publication date. If you cannot establish one, drop the article.
   Never guess a date and never report today's date for an undated page.
3. Within the allowed window, prefer practical content — tutorials, how-to guides, case studies,
   postmortems — over announcements. Concrete techniques, code, or measured results beat opinion.
4. For game dev AI: favor tools/workflows that let programmers produce art, UI, or sound without
   specialized skills.
5. No sponsored content, no press releases, no undated evergreen SEO pages, no 'awesome-list' repos.
6. Output ONLY a valid JSON array — no preamble, no explanation. If nothing qualifies, output []."""


def _overfetch_target(target_count: int) -> int:
    """중복·신선도 손실을 흡수하기 위한 요청 수량."""
    return max(OVERFETCH_MIN, min(target_count * OVERFETCH_MULTIPLIER, OVERFETCH_MAX))


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
    )

    outcome = claude_search.search_articles(
        client,
        prompt=prompt,
        system_blocks=_system_blocks(),
        caller="agent_find_articles" if round_index == 0 else f"agent_find_articles_topup{round_index}",
    )

    cleaned = [
        normalized for raw in outcome.articles if isinstance(raw, dict) and (normalized := _normalize_article(raw))
    ]
    return {
        "articles": cleaned,
        "count": len(cleaned),
        "stop_reason": outcome.stop_reason,
        "error": outcome.error,
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


def run(
    target_count: int = 3,
    topics: list[str] | None = None,
    external_preferences: dict | None = None,
    intent: dict | None = None,
) -> list[dict]:
    """
    뉴스 큐레이션 에이전트를 실행한다.

    목표 수량을 확보할 때까지 최대 (1 + TOPUP_MAX_ROUNDS)회 검색한다.
    반환량은 오버페치 때문에 target_count보다 많을 수 있으며, 남는 기사는
    pipeline에서 pending 저수지로 쌓여 다음 실패한 날의 보충분이 된다.

    Returns:
        선별된 기사 딕셔너리 목록 (최신순)
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY가 .env에 설정되지 않았습니다.")

    topics = topics or DEFAULT_TOPICS
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    max_age_days = recency.max_age_from_intent(intent)
    want = _overfetch_target(target_count)

    print(f"[Agent] 시작 — 목표 {target_count}개 (요청 {want}개) / 최근 {max_age_days}일 / 토픽: {', '.join(topics)}")

    preferences = _apply_external_preferences(_tool_analyze_preferences(), external_preferences)
    print(f"[Agent] 선호도 분석 → {preferences['summary']}")

    known_urls = db.get_all_article_urls()
    collected: dict[str, dict] = {}
    dropped = {"duplicate": 0, "stale": 0}

    for round_index in range(1 + TOPUP_MAX_ROUNDS):
        if round_index and len(collected) >= target_count:
            break

        result = _tool_find_ai_articles(
            client,
            topics_for_round(topics, round_index),
            max(want - len(collected), target_count),
            set(collected),
            preferences=preferences,
            intent=intent,
            max_age_days=max_age_days,
            round_index=round_index,
        )

        for article in result["articles"]:
            url = article["url"]
            if url in known_urls or url in collected:
                dropped["duplicate"] += 1
                continue
            if recency.is_stale(article["published_at"], max_age_days=max_age_days):
                dropped["stale"] += 1
                continue
            collected[url] = article

        print(
            f"[Agent] 라운드 {round_index}: 반환 {result['count']}개 → 누적 {len(collected)}개 "
            f"(중복 {dropped['duplicate']} / 기한초과 {dropped['stale']})"
        )

    articles = sorted(collected.values(), key=lambda a: a["published_at"], reverse=True)
    if len(articles) < target_count:
        print(f"[Agent] 경고 — 목표 {target_count}개 미달, {len(articles)}개만 확보 (feed 보충 필요)")
    print(f"[Agent] 완료 — {len(articles)}개 선별")
    return articles


# ── CLI 진입점 ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 뉴스 큐레이션 에이전트")
    parser.add_argument("--count", type=int, default=3, help="선별할 기사 수 (기본: 3)")
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
