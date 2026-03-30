"""
뉴스 큐레이션 에이전트 — tool-use 기반 agentic loop

3단계를 자율적으로 수행한다:
  1. analyze_preferences  — DB 선호도 분석
  2. find_ai_articles     — 웹 검색으로 기사 탐색 (토픽별 다중 호출 가능)
  3. review_articles      — 품질 검토 및 최종 선별

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
from config import ANTHROPIC_API_KEY, AI_KEYWORDS, CLAUDE_MODEL
from curator import _research_round, _extract_json_array

# ── 에이전트 문서 로더 ─────────────────────────────────────────────────────────

_AGENT_DOC_PATH = Path(__file__).resolve().parent.parent / ".claude" / "agents" / "news-curation-agent.md"

def _load_agent_spec() -> dict:
    """
    .claude/agents/news-curation-agent.md 에서 설정과 시스템 프롬프트 템플릿을 로드한다.

    반환 키:
        topics           — dict[str, str]  토픽명 → 설명
        default_topics   — list[str]       기본 탐색 토픽 목록
        system_prompt_template — str       {target_count}, {topics_list} 플레이스홀더 포함
    """
    text = _AGENT_DOC_PATH.read_text(encoding="utf-8")

    # YAML 프론트매터 분리
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError(f"에이전트 문서 형식 오류: {_AGENT_DOC_PATH}")

    frontmatter = yaml.safe_load(match.group(1))
    system_prompt_template = match.group(2).strip()

    return {
        "topics":                   frontmatter.get("topics", {}),
        "default_topics":           frontmatter.get("default_topics", []),
        "system_prompt_template":   system_prompt_template,
    }


# ── 에이전트 문서에서 상수 로드 ───────────────────────────────────────────────

_AGENT_SPEC    = _load_agent_spec()
_TOPIC_DESC: dict[str, str] = _AGENT_SPEC["topics"]
_DEFAULT_TOPICS: list[str]  = _AGENT_SPEC["default_topics"]

_SPAM_RE = re.compile(
    r"\b(sponsored|advertisement|buy now|sign up|subscribe|promo code|affiliate)\b",
    re.IGNORECASE,
)


# ── 도구 구현 ─────────────────────────────────────────────────────────────────

def _tool_analyze_preferences() -> dict:
    """DB에서 선호도 데이터를 읽어 요약 딕셔너리를 반환한다."""
    prefs = db.get_all_preferences()

    liked_sources    = [s for s in prefs["sources"]  if s["multiplier"] > 1.1][:5]
    disliked_sources = [s for s in prefs["sources"]  if s["multiplier"] < 0.9][:5]
    liked_keywords   = [k for k in prefs["keywords"] if k["multiplier"] > 1.1][:10]
    total_feedback   = sum(
        s["total_likes"] + s["total_dislikes"] for s in prefs["sources"]
    )

    return {
        "liked_sources":    [s["source"]  for s in liked_sources],
        "disliked_sources": [s["source"]  for s in disliked_sources],
        "liked_keywords":   [k["keyword"] for k in liked_keywords],
        "total_feedback":   total_feedback,
        "summary": (
            f"피드백 누적 {total_feedback}건 | "
            f"선호 소스 {len(liked_sources)}개, 비선호 소스 {len(disliked_sources)}개, "
            f"선호 키워드 {len(liked_keywords)}개"
        ),
    }


def _tool_find_ai_articles(
    client: anthropic.Anthropic,
    topic: str,
    count: int,
    already_collected: set[str],
) -> dict:
    """지정 토픽의 AI 기사를 웹 검색으로 수집한다."""
    topic_desc = _TOPIC_DESC.get(topic, topic)
    exclude_urls = db.get_todays_posted_urls()

    articles = _research_round(
        client=client,
        round_num=1,
        topic_name=topic,
        topic_desc=topic_desc,
        exclude_urls=exclude_urls,
        already_found_urls=already_collected,
        count=count,
    )

    # 필드 정규화
    cleaned = [
        {
            "url":            a.get("url", "").strip(),
            "title":          a.get("title", "제목 없음"),
            "source":         a.get("source", "Unknown"),
            "description":    a.get("description", "")[:500],
            "published_at":   a.get("published_at", ""),
            "curator_reason": a.get("curator_reason", ""),
        }
        for a in articles
        if a.get("url") and a.get("title")
    ]

    return {
        "topic":    topic,
        "articles": cleaned,
        "count":    len(cleaned),
        "message":  f"'{topic}' 토픽에서 {len(cleaned)}개 기사 수집",
    }


def _tool_review_articles(
    client: anthropic.Anthropic,
    articles: list[dict],
    preferences: dict,
    target_count: int,
) -> dict:
    """수집된 기사 목록을 품질 검토 후 최종 선별 결과를 반환한다."""

    # 1. 규칙 기반 빠른 필터
    filtered: list[dict] = []
    for a in articles:
        text = (a.get("title", "") + " " + a.get("description", "")).lower()
        if _SPAM_RE.search(text):
            continue
        if not any(kw in text for kw in AI_KEYWORDS):
            continue
        filtered.append(a)

    # URL 기준 중복 제거
    seen: set[str] = set()
    deduped: list[dict] = []
    for a in filtered:
        url = a.get("url", "").strip().rstrip("/")
        if url and url not in seen:
            seen.add(url)
            deduped.append(a)

    if len(deduped) <= target_count:
        return {
            "kept":    deduped,
            "summary": f"필터 후 {len(deduped)}개 (Claude 검토 생략)",
        }

    # 2. Claude 심층 검토
    liked    = ", ".join(preferences.get("liked_sources",   [])) or "없음"
    disliked = ", ".join(preferences.get("disliked_sources", [])) or "없음"
    keywords = ", ".join(preferences.get("liked_keywords",  [])) or "없음"

    prompt = f"""Review these AI news articles for a daily Discord briefing.

## User Preferences
- Preferred sources: {liked}
- Disliked sources: {disliked}
- Preferred topics: {keywords}

## Articles ({len(deduped)} candidates)
{json.dumps(deduped, ensure_ascii=False, indent=2)}

## Review Criteria
1. REJECT: sponsored/ad content, old-news roundups, clickbait
2. REJECT: near-duplicate (same event → keep only the best one)
3. PREFER: primary sources over secondary coverage
4. PREFER: user's preferred sources and topic keywords
5. PREFER: articles published within 24h over 24–48h

Select the best {target_count} articles.
Output ONLY a JSON array of the kept articles (preserve all fields, improve curator_reason if weak):
[{{"url":"...","title":"...","source":"...","description":"...","published_at":"...","curator_reason":"..."}}]"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=6000,
            system="You are a strict AI news curator. Output ONLY valid JSON — no explanation.",
            messages=[{"role": "user", "content": prompt}],
        )
        token_tracker.log_token_usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            caller="agent_review",
        )
        for block in response.content:
            if block.type == "text":
                kept = _extract_json_array(block.text)
                if kept:
                    return {
                        "kept":    kept[:target_count],
                        "summary": f"Claude 검토 완료: {len(deduped)}개 → {len(kept[:target_count])}개 선별",
                    }
    except anthropic.RateLimitError:
        time.sleep(30)
    except Exception as e:
        print(f"[Reviewer] Claude 검토 오류: {e}")

    return {
        "kept":    deduped[:target_count],
        "summary": f"규칙 필터만 적용: {len(deduped[:target_count])}개 반환",
    }


# ── 도구 스키마 ───────────────────────────────────────────────────────────────

_TOOLS = [
    {
        "name": "analyze_preferences",
        "description": (
            "SQLite DB에서 사용자의 기사 선호도를 분석한다. "
            "좋아하는/싫어하는 소스와 선호 키워드를 반환한다. "
            "에이전트 시작 시 가장 먼저 호출해야 한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "find_ai_articles",
        "description": (
            "지정 토픽의 최신 AI 기사를 웹 검색으로 수집한다. "
            "토픽별로 여러 번 호출해 다양한 기사를 모은다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "탐색할 AI 토픽. 가능한 값: "
                        "models, company_news, arxiv_papers, dev_tools, korean_news, "
                        "safety_policy, research_labs, applications, community_buzz, hardware_infra"
                    ),
                },
                "count": {
                    "type": "integer",
                    "description": "수집할 기사 수 (기본 5, 최대 10)",
                    "default": 5,
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "review_articles",
        "description": (
            "수집된 기사 목록을 품질 검토한다. "
            "광고·스팸 제거, 중복 탐지, 사용자 선호도 반영 후 최종 목록을 반환한다. "
            "충분한 기사가 모인 후 마지막에 한 번 호출한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "articles": {
                    "type": "array",
                    "description": "검토할 기사 목록 (find_ai_articles 결과들을 합친 것)",
                    "items": {"type": "object"},
                },
                "preferences": {
                    "type": "object",
                    "description": "사용자 선호도 (analyze_preferences 결과)",
                },
                "target_count": {
                    "type": "integer",
                    "description": "최종 선별할 기사 수",
                    "default": 5,
                },
            },
            "required": ["articles", "preferences"],
        },
    },
]


# ── 에이전트 메인 루프 ─────────────────────────────────────────────────────────

def run(target_count: int = 5, topics: list[str] | None = None) -> list[dict]:
    """
    뉴스 큐레이션 에이전트를 실행한다.

    Args:
        target_count: 최종 선별 기사 수
        topics:       탐색할 토픽 목록 (None이면 기본 5개)

    Returns:
        선별된 기사 딕셔너리 목록
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY가 .env에 설정되지 않았습니다.")

    topics = topics or _DEFAULT_TOPICS
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    collected: set[str] = set()   # 이미 수집한 URL 추적

    system_prompt = _AGENT_SPEC["system_prompt_template"].format(
        target_count=target_count,
        topics_list=", ".join(topics),
    )

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"AI 뉴스를 큐레이션해주세요. "
                f"탐색 토픽: {', '.join(topics)}. "
                f"최종 {target_count}개 기사를 선별해 JSON 배열로 반환하세요."
            ),
        }
    ]

    print(f"[Agent] 시작 — 목표 {target_count}개 / 토픽: {', '.join(topics)}")

    final_articles: list[dict] = []
    preferences: dict = {}
    all_found: list[dict] = []

    loop_step = 0
    while True:
        loop_step += 1
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            system=system_prompt,
            tools=_TOOLS,
            messages=messages,
        )
        token_tracker.log_token_usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            caller=f"agent_loop_step{loop_step}",
        )

        messages.append({"role": "assistant", "content": response.content})

        # 완료
        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    result = _extract_json_array(block.text)
                    if result:
                        final_articles = result
                        break
            print(f"[Agent] 완료 — {len(final_articles)}개 선별")
            break

        # 도구 호출
        if response.stop_reason != "tool_use":
            print(f"[Agent] 예상치 못한 stop_reason: {response.stop_reason}")
            break

        tool_results = []
        for block in response.content:
            if not hasattr(block, "type") or block.type != "tool_use":
                continue

            name  = block.name
            inp   = block.input
            print(f"[Agent] 도구 호출: {name}", end="")

            if name == "analyze_preferences":
                result = _tool_analyze_preferences()
                preferences = result
                print(f" → {result['summary']}")

            elif name == "find_ai_articles":
                topic = inp.get("topic", "models")
                count = min(int(inp.get("count", 5)), 10)
                print(f"({topic}, {count}개)", end="")
                result = _tool_find_ai_articles(client, topic, count, collected)
                for a in result.get("articles", []):
                    if a.get("url") and a["url"] not in collected:
                        collected.add(a["url"])
                        all_found.append(a)
                print(f" → {result['message']}")

            elif name == "review_articles":
                articles_in = inp.get("articles", all_found)
                pref_in     = inp.get("preferences", preferences)
                t_count     = int(inp.get("target_count", target_count))
                result = _tool_review_articles(client, articles_in, pref_in, t_count)
                print(f" → {result['summary']}")

            else:
                result = {"error": f"알 수 없는 도구: {name}"}
                print(f" → 오류: {result['error']}")

            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     json.dumps(result, ensure_ascii=False),
            })

        messages.append({"role": "user", "content": tool_results})

    return final_articles


# ── CLI 진입점 ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AI 뉴스 큐레이션 에이전트")
    parser.add_argument(
        "--count", type=int, default=5,
        help="선별할 기사 수 (기본: 5)",
    )
    parser.add_argument(
        "--topics", type=str, default="",
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
