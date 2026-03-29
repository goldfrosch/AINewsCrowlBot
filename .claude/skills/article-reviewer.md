---
name: article-reviewer
description: 수집된 AI 기사의 품질을 검토하고 필터링하는 패턴. 광고/스팸 제거, 중복 탐지, 선호도 기반 점수화, 최종 선별 시 사용.
---

# Article Reviewer — 기사 품질 검토 패턴

수집된 기사 후보에서 저품질·중복·비관련 기사를 제거하고
사용자 선호도를 반영해 최종 브리핑 목록을 구성한다.

---

## 1. 검토 기준 (우선순위 순)

| 기준 | 설명 | 처리 |
|------|------|------|
| 스팸·광고 | 제목에 "sponsored", "ad", "buy now" 등 | 즉시 제외 |
| 비AI 기사 | `config.AI_KEYWORDS`와 연관 없음 | 제외 |
| 중복 이벤트 | 동일 발표를 다루는 기사 ≥2개 | 1개만 유지 |
| 너무 오래된 기사 | published_at 기준 48h 초과 | 점수 감점 |
| 저품질 출처 | 개인 블로그, SEO 어뷰징 도메인 | 점수 감점 |
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

def quick_filter(articles: list[dict]) -> list[dict]:
    """광고·비AI 기사를 규칙 기반으로 빠르게 제거."""
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

규칙 기반 필터 이후, Claude에게 나머지 기사를 판정하게 한다.

```python
import json
import anthropic
from curator import _extract_json_array
from config import ANTHROPIC_API_KEY

_REVIEW_SYSTEM = """\
You are a strict AI news quality reviewer.
Output ONLY valid JSON — no explanation, no preamble."""

def claude_review(
    articles: list[dict],
    preferences: dict,
    client: anthropic.Anthropic | None = None,
) -> list[dict]:
    """
    Claude로 기사 품질 검토 후 keep 목록 반환.
    preferences: build_preference_summary() 결과 딕셔너리
    """
    if not articles:
        return []

    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    liked   = ", ".join(preferences.get("liked_sources", [])) or "없음"
    disliked = ", ".join(preferences.get("disliked_sources", [])) or "없음"
    keywords = ", ".join(preferences.get("liked_keywords", [])) or "없음"

    prompt = f"""Review these AI news articles and output a JSON array of verdicts.

## User Preferences
- Preferred sources: {liked}
- Disliked sources: {disliked}
- Preferred topics: {keywords}

## Articles
{json.dumps(articles, ensure_ascii=False, indent=2)}

## Criteria
1. REJECT: sponsored content, listicles of old news, clickbait without substance
2. REJECT: near-duplicate (same event covered by multiple articles — keep only the best)
3. PREFER: primary sources > secondary coverage
4. PREFER: user's preferred sources and topics
5. PREFER: articles published within 24h over 24–48h

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
