---
name: article-finder
description: 웹 검색으로 최신 AI 기사를 탐색하는 패턴. curator.py의 Ralph Loop 단일 라운드 또는 에이전트 도구로 AI 기사를 수집할 때 사용.
---

# Article Finder — AI 기사 탐색 패턴

`curator.py`의 `_research_round()`를 기반으로 한 AI 기사 수집 방법.
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
    topic_name="models",
    topic_desc="Latest AI model releases and benchmark results in the last 48 hours.",
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
| `models` | GPT-4o·Claude·Gemini·Llama 등 신규 모델·벤치마크 |
| `company_news` | OpenAI·Anthropic·Google 등 펀딩·출시·인수 |
| `arxiv_papers` | cs.AI / cs.LG / cs.CL 최신 논문 |
| `dev_tools` | HuggingFace·LangChain·vLLM 등 개발 도구 |
| `korean_news` | 한국어 AI 뉴스 (IT조선·AI타임스·ZDNet Korea) |
| `safety_policy` | AI 안전·정책·규제 뉴스 |
| `research_labs` | DeepMind·FAIR·Stanford HAI 연구 |
| `applications` | 로봇·헬스케어·코딩 어시스턴트 응용 사례 |
| `community_buzz` | HackerNews·Karpathy·LeCun 등 커뮤니티 동향 |
| `hardware_infra` | Nvidia·AMD·Groq GPU/TPU/NPU 뉴스 |

---

## 3. 커스텀 토픽으로 탐색

특정 주제를 찾을 때는 `topic_desc`를 자유롭게 구성한다:

```python
articles = _research_round(
    client=client,
    round_num=1,
    topic_name="custom",
    topic_desc=(
        "AI robotics news: new robot models, autonomous systems, "
        "manipulation tasks, humanoid robots published in the last 48 hours."
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

`_research_round` 가 반환하는 각 기사 딕셔너리:

```json
{
  "url": "https://...",
  "title": "GPT-5 Releases with Extended Context",
  "source": "OpenAI Blog",
  "description": "2–3 문장 요약",
  "author": "Sam Altman",
  "published_at": "2026-03-29",
  "curator_reason": "주요 모델 출시 — 성능 지표 포함"
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
    print("[ArticleFinder] 이 토픽에서 기사를 찾지 못했습니다.")
```
