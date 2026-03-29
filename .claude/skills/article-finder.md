---
name: article-finder
description: 웹 검색으로 AI 도구 실용 아티클을 탐색하는 패턴. curator.py의 Ralph Loop 단일 라운드 또는 에이전트 도구로 Claude Code·Cursor·프롬프트 엔지니어링 등 개발자 실용 콘텐츠를 수집할 때 사용.
---

# Article Finder — AI 실용 아티클 탐색 패턴

`curator.py`의 `_research_round()`를 기반으로 한 아티클 수집 방법.
대상 독자: Claude Code, Cursor 등 AI 코딩 도구를 매일 사용하는 소프트웨어 엔지니어.
`web_search_20260209` 도구로 Claude에게 웹 검색을 위임한다.

---

## 1. 단일 라운드 직접 호출

```python
import anthropic
from curator import _research_round
from database import get_todays_posted_urls
from config import ANTHROPIC_API_KEY

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

articles = _research_round(
    client=client,
    round_num=1,
    topic_name="claude_code_tips",
    topic_desc="Articles and tutorials about using Claude Code CLI effectively: tips, workflows, hooks, MCP servers.",
    exclude_urls=get_todays_posted_urls(),   # 오늘 이미 게시된 URL 제외
    already_found_urls=set(),
    count=5,
)
# 반환: list[dict] — url, title, source, description, author, published_at, curator_reason
```

---

## 2. 탐색 토픽 목록

| 토픽명 | 검색 대상 |
|--------|----------|
| `claude_code_tips` | Claude Code CLI 사용법·워크플로우·슬래시 커맨드·훅·CLAUDE.md 패턴 |
| `prompt_engineering` | 프롬프트 엔지니어링 기법·시스템 프롬프트·CoT·구조화 출력 |
| `ai_coding_tools` | Cursor·GitHub Copilot·Codeium·Aider 실전 사용 팁·설정 가이드 |
| `mcp_tools` | MCP 서버 구축·Claude 도구 통합·에이전트 tool-use 패턴 |
| `dev_productivity` | LLM 기반 코드 리뷰·테스트 생성·문서화·리팩터링 워크플로우 |
| `llm_best_practices` | 컨텍스트 윈도우 관리·RAG·비용 최적화·지연 감소 실전 기법 |
| `agent_patterns` | AI 에이전트 구축·LangChain·CrewAI·AutoGen·멀티에이전트 패턴 |
| `korean_practitioner` | 한국어 AI 활용 아티클 (Velog·브런치·블로그) |
| `community_tips` | HN·Reddit r/ClaudeAI·Twitter 개발자 커뮤니티 실전 팁 스레드 |
| `tutorials_deep_dive` | Claude/OpenAI API 통합·임베딩·벡터 DB 심층 튜토리얼 |

---

## 3. 커스텀 토픽으로 탐색

특정 주제를 찾을 때는 `topic_desc`를 자유롭게 구성한다:

```python
articles = _research_round(
    client=client,
    round_num=1,
    topic_name="custom",
    topic_desc=(
        "Step-by-step tutorials for building MCP servers that integrate with Claude Code. "
        "Include code examples and configuration patterns."
    ),
    exclude_urls=[],
    already_found_urls=set(),
    count=5,
)
```

---

## 4. 다중 토픽 병렬 수집 (비동기)

여러 토픽을 동시에 수집해야 할 때:

```python
import asyncio
from curator import _research_round, _TOPICS
from config import ANTHROPIC_API_KEY
import anthropic

async def fetch_topic(topic_name: str, topic_desc: str, count: int) -> list[dict]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return await asyncio.to_thread(
        _research_round,
        client, 1, topic_name, topic_desc, [], set(), count,
    )

async def find_articles_parallel(topics: list[str], count: int = 3) -> list[dict]:
    topic_map = {t[0]: t[1] for t in _TOPICS}
    tasks = [fetch_topic(t, topic_map[t], count) for t in topics if t in topic_map]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    combined = []
    seen = set()
    for batch in results:
        if isinstance(batch, list):
            for a in batch:
                if a.get("url") and a["url"] not in seen:
                    seen.add(a["url"])
                    combined.append(a)
    return combined
```

---

## 5. 응답 JSON 구조

`_research_round` 가 반환하는 각 아티클 딕셔너리:

```json
{
  "url": "https://...",
  "title": "10 Claude Code Workflows That Changed How I Write Code",
  "source": "Simon Willison's Weblog",
  "description": "2–3 문장 요약",
  "author": "Simon Willison",
  "published_at": "2026-03-29",
  "curator_reason": "Claude Code 훅과 CLAUDE.md 패턴에 대한 구체적 예시 포함"
}
```

필드가 없을 경우 기본값 처리:

```python
articles = [
    {
        "url": a.get("url", "").strip(),
        "title": a.get("title", "제목 없음"),
        "source": a.get("source", "Unknown"),
        "description": a.get("description", "")[:500],
        "published_at": a.get("published_at", ""),
        "curator_reason": a.get("curator_reason", ""),
    }
    for a in raw_articles
    if a.get("url") and a.get("title")
]
```

---

## 6. 오류 처리

`_research_round`는 내부적으로 오류를 흡수하고 `[]`를 반환한다.
호출 측에서 추가 방어가 필요한 경우:

```python
try:
    articles = _research_round(...)
except Exception as e:
    print(f"[ArticleFinder] 탐색 실패: {e}")
    articles = []

if not articles:
    print("[ArticleFinder] 이 토픽에서 아티클을 찾지 못했습니다.")
```
