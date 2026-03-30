---
name: preference-intelligence
description: 누적된 👍/👎 피드백 데이터를 심층 분석해 큐레이션 품질을 개선하는 패턴. 신뢰도 필터링, 소스/키워드 티어링, 기사 패턴 분석, 큐레이션 힌트 생성까지 포함. preference-analyzer.md의 CRUD를 넘어서는 인사이트 추출 레이어.
---

# Preference Intelligence — 선호도 심층 분석 패턴

`preference-analyzer.md`가 DB 읽기/쓰기를 담당한다면,
이 스킬은 누적 데이터에서 **패턴을 추출**하고 **큐레이션 힌트**로 변환하는 방법을 다룬다.

---

## 1. 신뢰도 필터링

피드백이 너무 적은 소스/키워드는 배율이 왜곡될 수 있다.
최소 피드백 임계값을 넘은 항목만 인사이트에 반영한다.

```python
MIN_FEEDBACK = 3  # 이 이상의 피드백이 있어야 신뢰 가능

def filter_reliable(items: list[dict], min_feedback: int = MIN_FEEDBACK) -> list[dict]:
    """total_likes + total_dislikes >= min_feedback인 항목만 반환."""
    return [
        item for item in items
        if item["total_likes"] + item["total_dislikes"] >= min_feedback
    ]
```

---

## 2. 소스/키워드 티어 분류

배율 기준으로 5단계로 분류해 큐레이터에게 명확한 신호를 전달한다.

```python
def tier(multiplier: float) -> str:
    if multiplier >= 1.5:  return "강선호"
    if multiplier >= 1.1:  return "선호"
    if multiplier >= 0.9:  return "중립"
    if multiplier >= 0.5:  return "비선호"
    return "강비선호"

def build_tiered_profile(prefs: dict, min_feedback: int = MIN_FEEDBACK) -> dict:
    """
    source_preferences / keyword_preferences를 티어별로 분류.

    반환 구조:
      {
        "sources":  {"강선호": [...], "선호": [...], "중립": [...], "비선호": [...], "강비선호": [...]},
        "keywords": {"강선호": [...], "선호": [...], ...},
        "reliable_source_count": int,
        "reliable_keyword_count": int,
      }
    """
    reliable_sources  = filter_reliable(prefs["sources"],  min_feedback)
    reliable_keywords = filter_reliable(prefs["keywords"], min_feedback)

    src_tiers = {"강선호": [], "선호": [], "중립": [], "비선호": [], "강비선호": []}
    kw_tiers  = {"강선호": [], "선호": [], "중립": [], "비선호": [], "강비선호": []}

    for s in reliable_sources:
        src_tiers[tier(s["multiplier"])].append(s["source"])
    for k in reliable_keywords:
        kw_tiers[tier(k["multiplier"])].append(k["keyword"])

    return {
        "sources":               src_tiers,
        "keywords":              kw_tiers,
        "reliable_source_count": len(reliable_sources),
        "reliable_keyword_count": len(reliable_keywords),
    }
```

---

## 3. articles 테이블 기반 패턴 분석

`source_preferences`·`keyword_preferences`만 보는 대신,
실제 게시된 기사의 반응을 직접 집계하면 더 풍부한 패턴을 얻는다.

```python
import sqlite3
from pathlib import Path

DB_PATH = Path("data/bot.db")

def get_article_feedback_patterns(top_n: int = 10) -> dict:
    """
    게시된 기사의 likes/dislikes를 소스별로 집계.

    반환:
      {
        "by_source": [{"source": str, "likes": int, "dislikes": int, "ratio": float}, ...],
        "most_liked_titles":    [str, ...],  # 상위 N개
        "most_disliked_titles": [str, ...],  # 상위 N개
      }
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        by_source = conn.execute("""
            SELECT source,
                   SUM(likes)    AS likes,
                   SUM(dislikes) AS dislikes,
                   CAST(SUM(likes) AS REAL) /
                       MAX(SUM(likes) + SUM(dislikes), 1) AS ratio
            FROM articles
            WHERE status = 'posted'
            GROUP BY source
            HAVING SUM(likes) + SUM(dislikes) > 0
            ORDER BY ratio DESC
        """).fetchall()

        liked_titles = conn.execute("""
            SELECT title FROM articles
            WHERE status = 'posted' AND likes > 0
            ORDER BY likes DESC LIMIT ?
        """, (top_n,)).fetchall()

        disliked_titles = conn.execute("""
            SELECT title FROM articles
            WHERE status = 'posted' AND dislikes > 0
            ORDER BY dislikes DESC LIMIT ?
        """, (top_n,)).fetchall()

    return {
        "by_source":             [dict(r) for r in by_source],
        "most_liked_titles":     [r["title"] for r in liked_titles],
        "most_disliked_titles":  [r["title"] for r in disliked_titles],
    }
```

---

## 4. 큐레이션 힌트 딕셔너리 생성

분석 결과를 큐레이터/에이전트가 바로 쓸 수 있는 힌트 구조로 변환한다.

```python
def build_curation_hints(
    tiered: dict,
    patterns: dict,
    total_feedback: int,
) -> dict:
    """
    티어 프로파일 + 기사 패턴 → 큐레이션 힌트 딕셔너리.

    반환:
      {
        "boost_sources":  list[str],   # 우선 탐색할 소스
        "avoid_sources":  list[str],   # 수집 제외할 소스
        "focus_keywords": list[str],   # 웹 검색 쿼리에 포함할 키워드
        "skip_keywords":  list[str],   # 검색 쿼리에서 제외할 키워드
        "cold_start":     bool,        # 피드백 부족 → 다양성 우선 모드
        "confidence":     str,         # "low" | "medium" | "high"
      }
    """
    cold_start = total_feedback < 10

    if total_feedback < 10:
        confidence = "low"
    elif total_feedback < 30:
        confidence = "medium"
    else:
        confidence = "high"

    boost_sources = (
        tiered["sources"]["강선호"] + tiered["sources"]["선호"]
    )[:5]

    avoid_sources = (
        tiered["sources"]["강비선호"] + tiered["sources"]["비선호"]
    )[:5]

    # articles 테이블 패턴으로 boost/avoid 보완
    for row in patterns["by_source"]:
        if row["ratio"] >= 0.8 and row["source"] not in boost_sources:
            boost_sources.append(row["source"])
        elif row["ratio"] <= 0.2 and row["source"] not in avoid_sources:
            avoid_sources.append(row["source"])

    return {
        "boost_sources":  boost_sources,
        "avoid_sources":  avoid_sources,
        "focus_keywords": tiered["keywords"]["강선호"][:10],
        "skip_keywords":  tiered["keywords"]["강비선호"][:10],
        "cold_start":     cold_start,
        "confidence":     confidence,
    }
```

---

## 5. 전체 분석 파이프라인

```python
import database as db

def run_preference_analysis(min_feedback: int = MIN_FEEDBACK) -> dict:
    """
    선호도 전체 분석을 실행하고 큐레이션 힌트를 포함한 리포트를 반환.

    반환:
      {
        "tiered_profile": dict,     # 티어별 소스/키워드
        "article_patterns": dict,   # 기사 테이블 집계
        "curation_hints": dict,     # 바로 사용 가능한 힌트
        "total_feedback": int,
        "summary": str,
      }
    """
    prefs = db.get_all_preferences()
    total_feedback = sum(
        s["total_likes"] + s["total_dislikes"] for s in prefs["sources"]
    )

    tiered   = build_tiered_profile(prefs, min_feedback)
    patterns = get_article_feedback_patterns()
    hints    = build_curation_hints(tiered, patterns, total_feedback)

    summary = (
        f"피드백 {total_feedback}건 | "
        f"신뢰 소스 {tiered['reliable_source_count']}개 / "
        f"신뢰 키워드 {tiered['reliable_keyword_count']}개 | "
        f"신뢰도: {hints['confidence']}"
    )

    return {
        "tiered_profile":  tiered,
        "article_patterns": patterns,
        "curation_hints":  hints,
        "total_feedback":  total_feedback,
        "summary":         summary,
    }
```

---

## 6. Claude에 선호도 인사이트 요청

누적 데이터가 충분할 때 Claude에게 자연어 인사이트를 생성하게 할 수 있다.

```python
import json
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

def generate_preference_insights(analysis: dict) -> str:
    """
    분석 결과를 바탕으로 Claude가 자연어 인사이트를 생성.
    analysis: run_preference_analysis() 결과

    콜드 스타트(total_feedback < 10) 시에는 호출하지 말 것.
    """
    if analysis["curation_hints"]["cold_start"]:
        return "피드백 데이터가 아직 부족합니다. 10건 이상 쌓인 후 인사이트를 생성하세요."

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""다음은 Discord AI 뉴스 봇의 누적 피드백 분석 결과입니다.

## 티어 프로파일
{json.dumps(analysis["tiered_profile"], ensure_ascii=False, indent=2)}

## 기사별 반응 패턴
- 가장 많이 좋아요 받은 기사 제목:
{chr(10).join(f'  - {t}' for t in analysis["article_patterns"]["most_liked_titles"][:5])}

- 가장 많이 싫어요 받은 기사 제목:
{chr(10).join(f'  - {t}' for t in analysis["article_patterns"]["most_disliked_titles"][:5])}

## 현재 큐레이션 힌트
{json.dumps(analysis["curation_hints"], ensure_ascii=False, indent=2)}

위 데이터를 분석해 다음을 한국어로 작성하세요:
1. 사용자가 선호하는 콘텐츠 유형 (2-3문장)
2. 피해야 할 콘텐츠 패턴 (2-3문장)
3. 다음 큐레이션에서 집중해야 할 방향 (3가지 구체적 권고)"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
```

---

## 7. 큐레이터/에이전트 통합

`build_curation_hints()` 결과를 `news_curation_agent.py`의 `_tool_review_articles`에 주입:

```python
hints = analysis["curation_hints"]

# find_ai_articles 호출 시 boost_sources를 검색 쿼리에 반영
topic_query = f"{topic_desc} site:{' OR site:'.join(hints['boost_sources'][:2])}"

# review_articles의 preferences 인자로 전달
preferences = {
    "liked_sources":    hints["boost_sources"],
    "disliked_sources": hints["avoid_sources"],
    "liked_keywords":   hints["focus_keywords"],
}
```

콜드 스타트 시에는 힌트를 무시하고 다양성 기준만 적용:

```python
if hints["cold_start"]:
    preferences = {}  # 선호도 힌트 없이 다양성 위주
```
