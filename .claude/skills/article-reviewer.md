---
name: article-reviewer
description: 수집된 AI 실용 아티클의 품질을 검토하고 필터링하는 패턴. 광고/스팸 제거, 중복 탐지, 실용성 점수화, 최종 선별 시 사용. 대상 독자는 Claude Code·Cursor 등 AI 코딩 도구를 사용하는 개발자.
---

# Article Reviewer — AI 실용 아티클 품질 검토 패턴

수집된 아티클 후보에서 저품질·중복·단순 뉴스 기사를 제거하고
개발자에게 실질적으로 유용한 콘텐츠를 선별한다.

---

## 1. 검토 기준 (우선순위 순)

| 기준 | 설명 | 처리 |
|------|------|------|
| 스팸·광고 | 제목에 "sponsored", "ad", "buy now", "sign up" 등 | 즉시 제외 |
| 단순 뉴스 | 모델 발표·기업 소식만 있고 개발자 활용법 없음 | 제외 |
| 실용성 없음 | 코드 예시·구체적 기법·재현 가능한 팁 없음 | 점수 감점 |
| 중복 이벤트 | 동일 주제·기법을 다루는 아티클 ≥2개 | 1개만 유지 |
| 저품질 출처 | SEO 어뷰징 도메인, AI 생성 단순 요약 블로그 | 점수 감점 |
| 실용 콘텐츠 | 튜토리얼·how-to·사례 연구·코드 포함 | 점수 가점 |
| 선호 소스/키워드 | DB 선호도 multiplier 반영 | 점수 가점 |

---

## 2. 규칙 기반 1차 필터 (빠른 제외)

```python
import re
from config import AI_KEYWORDS

_SPAM_PATTERNS = re.compile(
    r"\b(sponsored|advertisement|buy now|sign up|subscribe|promo code)\b",
    re.IGNORECASE,
)

# 실용 콘텐츠 신호 — 이 중 하나라도 있으면 우선 통과
_PRACTICAL_SIGNALS = re.compile(
    r"\b(tutorial|how to|guide|tips?|workflow|step.by.step|example|"
    r"best practice|cheatsheet|cheat sheet|deep dive|walkthrough|"
    r"사용법|튜토리얼|가이드|팁|워크플로우|실전|예제)\b",
    re.IGNORECASE,
)

def quick_filter(articles: list[dict]) -> list[dict]:
    """광고·단순뉴스 기사를 규칙 기반으로 빠르게 제거."""
    kept = []
    for a in articles:
        text = (a.get("title", "") + " " + a.get("description", "")).lower()

        # 스팸 제거
        if _SPAM_PATTERNS.search(text):
            continue

        # AI 관련성 확인
        if not any(kw in text for kw in AI_KEYWORDS):
            continue

        kept.append(a)
    return kept
```

---

## 3. URL 기반 중복 제거

```python
def dedup_by_url(articles: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result = []
    for a in articles:
        url = a.get("url", "").strip().rstrip("/")
        if url and url not in seen:
            seen.add(url)
            result.append(a)
    return result
```

---

## 4. Claude를 이용한 심층 검토

규칙 기반 필터 이후, Claude에게 나머지 아티클을 판정하게 한다.

```python
import json
import anthropic
from curator import _extract_json_array
from config import ANTHROPIC_API_KEY

_REVIEW_SYSTEM = """\
You are a strict reviewer for a developer-focused AI tools newsletter.
Target reader: software engineer using Claude Code, Cursor, or similar AI coding tools daily.
Output ONLY valid JSON — no explanation, no preamble."""

def claude_review(
    articles: list[dict],
    preferences: dict,
    client: anthropic.Anthropic | None = None,
) -> list[dict]:
    """
    Claude로 아티클 품질 검토 후 keep 목록 반환.
    preferences: build_preference_summary() 결과 딕셔너리
    """
    if not articles:
        return []

    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    liked   = ", ".join(preferences.get("liked_sources", [])) or "없음"
    disliked = ", ".join(preferences.get("disliked_sources", [])) or "없음"
    keywords = ", ".join(preferences.get("liked_keywords", [])) or "없음"

    prompt = f"""Review these articles for a developer AI tools newsletter and output verdict JSON.

## Target Reader
Software engineer using Claude Code, Cursor, or similar AI coding tools daily.
Wants actionable techniques, not general AI news.

## User Preferences
- Preferred sources: {liked}
- Disliked sources: {disliked}
- Preferred topics: {keywords}

## Articles
{json.dumps(articles, ensure_ascii=False, indent=2)}

## Criteria
1. KEEP: tutorials, how-to guides, workflow tips, code examples, case studies with concrete results
2. KEEP: deep dives into prompt engineering, MCP, agent patterns, LLM integration
3. REJECT: pure news (model release announcements without developer usage tips)
4. REJECT: sponsored content, generic AI hype, thin listicles
5. REJECT: near-duplicate (same technique covered by multiple articles — keep only the best)
6. PREFER: primary sources, practitioner blogs, official docs > aggregator sites

Output a JSON array — one entry per article:
[{{"url": "...", "verdict": "keep" | "reject", "reason": "one sentence", "quality_score": 0.0-1.0}}]"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4000,
        system=_REVIEW_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    for block in response.content:
        if block.type == "text":
            reviews = _extract_json_array(block.text)
            if not reviews:
                continue
            verdict_map = {r["url"]: r for r in reviews}
            return [
                a for a in articles
                if verdict_map.get(a.get("url"), {}).get("verdict") == "keep"
            ]

    return articles  # 검토 실패 시 원본 반환
```

---

## 5. 선호도 점수 계산 (랭킹 통합)

Claude 검토 통과 후 `ranker.rank_articles`로 최종 정렬:

```python
from ranker import rank_articles

def score_and_rank(articles: list[dict]) -> list[dict]:
    """
    DB 선호도 기반 final_score 계산 후 내림차순 정렬.
    articles 원소는 'id', 'source', 'title', 'description',
    'platform_score' 필드를 포함해야 함.
    """
    return rank_articles(articles)
```

플랫폼 점수가 없는 경우 기본값 설정:

```python
for a in articles:
    a.setdefault("platform_score", 100.0)
    a.setdefault("id", 0)
```

---

## 6. 전체 파이프라인

```python
def review_pipeline(
    raw_articles: list[dict],
    preferences: dict,
    target_count: int = 5,
) -> list[dict]:
    # 1. 빠른 규칙 필터
    filtered = quick_filter(raw_articles)
    filtered = dedup_by_url(filtered)

    # 2. Claude 심층 검토 (후보가 target_count보다 많을 때만)
    if len(filtered) > target_count:
        filtered = claude_review(filtered, preferences)

    # 3. 선호도 기반 정렬
    for a in filtered:
        a.setdefault("platform_score", 100.0)
        a.setdefault("id", 0)
    ranked = score_and_rank(filtered)

    return ranked[:target_count]
```

---

## 7. 검토 결과 로깅

```python
def log_review_result(
    original: list[dict],
    kept: list[dict],
    label: str = "Review",
) -> None:
    rejected = len(original) - len(kept)
    print(f"[{label}] 입력 {len(original)}개 → 유지 {len(kept)}개 / 제외 {rejected}개")
    for a in kept:
        score = a.get("final_score", a.get("quality_score", "-"))
        print(f"  ✅ [{score}] {a.get('title', '')[:60]}")
```
