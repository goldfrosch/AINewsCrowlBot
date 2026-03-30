---
name: preference-intelligence
description: 누적된 👍/👎 피드백 데이터를 심층 분석해 큐레이션 품질을 개선하는 패턴. 최근 7일 데이터 최우선, 부족 시 14일→30일→전체로 점진 확장. 신뢰도 필터링, 소스/키워드 티어링, 큐레이션 힌트 생성까지 포함. preference-analyzer.md의 CRUD를 넘어서는 인사이트 추출 레이어.
---

# Preference Intelligence — 선호도 심층 분석 패턴

`preference-analyzer.md`가 DB 읽기/쓰기를 담당한다면,
이 스킬은 **최근 데이터를 우선**해 패턴을 추출하고 **큐레이션 힌트**로 변환하는 방법을 다룬다.

## 데이터 소스 구분

| 테이블                | 타임스탬프                        | 용도                           |
| --------------------- | --------------------------------- | ------------------------------ |
| `articles`            | `posted_at` (기사별)              | **시간 윈도우 분석의 주 소스** |
| `source_preferences`  | `last_updated` (마지막 변경 시각) | 전체 누적 배율 참조용          |
| `keyword_preferences` | `last_updated` (마지막 변경 시각) | 전체 누적 배율 참조용          |

> `source_preferences`·`keyword_preferences`는 이벤트별 타임스탬프가 없어 시간 윈도우 필터링 불가.
> 시간 기반 분석은 반드시 `articles` 테이블을 직접 집계한다.

---

## 1. 시간 윈도우 기반 데이터 조회

```python
import json
import sqlite3
from pathlib import Path

DB_PATH = Path("data/bot.db")

# 점진 확장 순서: 7일 → 14일 → 30일 → 전체(None)
WINDOWS: list[int | None] = [7, 14, 30, None]
MIN_ARTICLES_WITH_FEEDBACK = 3  # 윈도우를 '충분'하다고 볼 최소 기사 수


def get_windowed_feedback(days: int | None) -> dict:
    """
    지정 기간 내 게시 기사의 소스·키워드별 likes/dislikes를 집계한다.

    Args:
        days: 몇 일 전까지 볼지. None이면 전체 기간.

    반환:
      {
        "days": int | None,           # 사용된 윈도우 크기
        "total_articles_with_feedback": int,
        "by_source": [
            {"source": str, "likes": int, "dislikes": int, "ratio": float}, ...
        ],
        "by_keyword": [
            {"keyword": str, "likes": int, "dislikes": int}, ...
        ],
        "most_liked_titles":    [str, ...],
        "most_disliked_titles": [str, ...],
      }
    """
    time_filter = ""
    if days is not None:
        time_filter = f"AND posted_at >= datetime('now', '-{days} days')"

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        # 피드백 있는 기사 수
        total = conn.execute(f"""
            SELECT COUNT(*) FROM articles
            WHERE status = 'posted'
              AND (likes > 0 OR dislikes > 0)
              {time_filter}
        """).fetchone()[0]

        # 소스별 집계
        by_source = conn.execute(f"""
            SELECT source,
                   SUM(likes)    AS likes,
                   SUM(dislikes) AS dislikes,
                   CAST(SUM(likes) AS REAL) /
                       MAX(SUM(likes) + SUM(dislikes), 1) AS ratio
            FROM articles
            WHERE status = 'posted'
              AND (likes > 0 OR dislikes > 0)
              {time_filter}
            GROUP BY source
            ORDER BY ratio DESC
        """).fetchall()

        # 키워드별 집계 — keywords 컬럼은 JSON 배열 문자열
        raw_articles = conn.execute(f"""
            SELECT keywords, likes, dislikes
            FROM articles
            WHERE status = 'posted'
              AND (likes > 0 OR dislikes > 0)
              {time_filter}
        """).fetchall()

        liked_titles = conn.execute(f"""
            SELECT title FROM articles
            WHERE status = 'posted' AND likes > 0
              {time_filter}
            ORDER BY likes DESC LIMIT 10
        """).fetchall()

        disliked_titles = conn.execute(f"""
            SELECT title FROM articles
            WHERE status = 'posted' AND dislikes > 0
              {time_filter}
            ORDER BY dislikes DESC LIMIT 10
        """).fetchall()

    # 키워드 집계 (Python에서 JSON 파싱)
    kw_map: dict[str, dict] = {}
    for row in raw_articles:
        try:
            keywords = json.loads(row["keywords"] or "[]")
        except (json.JSONDecodeError, TypeError):
            keywords = []
        for kw in keywords:
            if not kw:
                continue
            entry = kw_map.setdefault(kw, {"keyword": kw, "likes": 0, "dislikes": 0})
            entry["likes"]    += row["likes"]
            entry["dislikes"] += row["dislikes"]

    by_keyword = sorted(kw_map.values(), key=lambda x: x["likes"], reverse=True)

    return {
        "days":                       days,
        "total_articles_with_feedback": total,
        "by_source":                  [dict(r) for r in by_source],
        "by_keyword":                 by_keyword,
        "most_liked_titles":          [r["title"] for r in liked_titles],
        "most_disliked_titles":       [r["title"] for r in disliked_titles],
    }
```

---

## 2. 점진적 윈도우 확장

최근 7일 데이터가 충분하면 그것만 사용하고, 부족하면 자동으로 윈도우를 넓힌다.

```python
def find_sufficient_window(
    min_articles: int = MIN_ARTICLES_WITH_FEEDBACK,
) -> tuple[dict, int | None]:
    """
    피드백 기사가 min_articles개 이상인 가장 좁은 윈도우를 찾아 반환한다.

    탐색 순서: 7일 → 14일 → 30일 → 전체
    모든 윈도우가 부족하면 전체 기간 데이터를 반환한다.

    반환: (windowed_feedback_dict, used_days)
    """
    for days in WINDOWS:
        data = get_windowed_feedback(days)
        if data["total_articles_with_feedback"] >= min_articles:
            label = f"{days}일" if days else "전체 기간"
            print(f"[PreferenceIntelligence] 윈도우: {label} "
                  f"(피드백 기사 {data['total_articles_with_feedback']}개)")
            return data, days

    # 이 줄에는 도달하지 않지만 타입 안전성 확보
    fallback = get_windowed_feedback(None)
    return fallback, None


def describe_window(days: int | None) -> str:
    """윈도우 크기를 사람이 읽기 쉬운 문자열로 변환."""
    if days is None:
        return "전체 기간"
    return f"최근 {days}일"
```

---

## 3. 신뢰도 필터링

피드백이 너무 적은 소스/키워드는 배율이 왜곡될 수 있다.

```python
MIN_FEEDBACK = 3

def filter_reliable(items: list[dict], min_feedback: int = MIN_FEEDBACK) -> list[dict]:
    """likes + dislikes >= min_feedback인 항목만 반환."""
    return [
        item for item in items
        if item["likes"] + item["dislikes"] >= min_feedback
    ]
```

---

## 4. 소스/키워드 티어 분류

윈도우 집계 데이터에서 likes/dislikes 비율로 5단계 티어를 결정한다.

```python
def _ratio_to_tier(likes: int, dislikes: int) -> str:
    total = likes + dislikes
    if total == 0:
        return "중립"
    ratio = likes / total
    if ratio >= 0.8:   return "강선호"
    if ratio >= 0.6:   return "선호"
    if ratio >= 0.4:   return "중립"
    if ratio >= 0.2:   return "비선호"
    return "강비선호"


def build_tiered_profile(windowed: dict, min_feedback: int = MIN_FEEDBACK) -> dict:
    """
    시간 윈도우 집계 데이터를 티어별로 분류한다.

    반환:
      {
        "sources":  {"강선호": [...], "선호": [...], "중립": [...], "비선호": [...], "강비선호": [...]},
        "keywords": {"강선호": [...], "선호": [...], ...},
        "reliable_source_count": int,
        "reliable_keyword_count": int,
      }
    """
    reliable_sources  = filter_reliable(windowed["by_source"],  min_feedback)
    reliable_keywords = filter_reliable(windowed["by_keyword"], min_feedback)

    src_tiers = {"강선호": [], "선호": [], "중립": [], "비선호": [], "강비선호": []}
    kw_tiers  = {"강선호": [], "선호": [], "중립": [], "비선호": [], "강비선호": []}

    for s in reliable_sources:
        src_tiers[_ratio_to_tier(s["likes"], s["dislikes"])].append(s["source"])
    for k in reliable_keywords:
        kw_tiers[_ratio_to_tier(k["likes"], k["dislikes"])].append(k["keyword"])

    return {
        "sources":               src_tiers,
        "keywords":              kw_tiers,
        "reliable_source_count": len(reliable_sources),
        "reliable_keyword_count": len(reliable_keywords),
    }
```

> **참고:** 전체 누적 배율(`source_preferences.multiplier`)을 함께 참조하고 싶다면
> `db.get_all_preferences()`를 별도 호출해 보조 가중치로 활용한다.
> 단, 최근 윈도우 데이터와 충돌 시 **최근 윈도우를 우선**한다.

---

## 5. 큐레이션 힌트 딕셔너리 생성

```python
def build_curation_hints(
    tiered: dict,
    windowed: dict,
    total_feedback: int,
) -> dict:
    """
    티어 프로파일 + 시간 윈도우 집계 → 큐레이션 힌트 딕셔너리.

    반환:
      {
        "boost_sources":  list[str],
        "avoid_sources":  list[str],
        "focus_keywords": list[str],
        "skip_keywords":  list[str],
        "cold_start":     bool,
        "confidence":     str,   # "low" | "medium" | "high"
        "data_window":    str,   # "최근 7일" | "최근 14일" | ... | "전체 기간"
      }
    """
    cold_start = total_feedback < 10

    if total_feedback < 10:
        confidence = "low"
    elif total_feedback < 30:
        confidence = "medium"
    else:
        confidence = "high"

    boost_sources = (tiered["sources"]["강선호"] + tiered["sources"]["선호"])[:5]
    avoid_sources = (tiered["sources"]["강비선호"] + tiered["sources"]["비선호"])[:5]

    return {
        "boost_sources":  boost_sources,
        "avoid_sources":  avoid_sources,
        "focus_keywords": tiered["keywords"]["강선호"][:10],
        "skip_keywords":  tiered["keywords"]["강비선호"][:10],
        "cold_start":     cold_start,
        "confidence":     confidence,
        "data_window":    describe_window(windowed["days"]),
    }
```

---

## 6. 전체 분석 파이프라인

```python
import database as db

def run_preference_analysis(
    min_articles: int = MIN_ARTICLES_WITH_FEEDBACK,
    min_feedback: int = MIN_FEEDBACK,
) -> dict:
    """
    선호도 전체 분석 실행. 최근 7일 우선, 부족 시 자동 확장.

    반환:
      {
        "windowed":        dict,   # 시간 윈도우 집계 원본
        "tiered_profile":  dict,   # 티어별 소스/키워드
        "curation_hints":  dict,   # 바로 사용 가능한 힌트
        "total_feedback":  int,
        "data_window":     str,    # 실제로 사용된 윈도우 레이블
        "summary":         str,
      }
    """
    windowed, used_days = find_sufficient_window(min_articles)
    total_feedback = windowed["total_articles_with_feedback"]

    # 전체 누적 피드백 수는 source_preferences 집계로 보완
    agg_prefs = db.get_all_preferences()
    total_agg = sum(s["total_likes"] + s["total_dislikes"] for s in agg_prefs["sources"])

    tiered = build_tiered_profile(windowed, min_feedback)
    hints  = build_curation_hints(tiered, windowed, total_agg)

    data_window = describe_window(used_days)
    summary = (
        f"데이터 윈도우: {data_window} | "
        f"피드백 기사 {total_feedback}개 | "
        f"누적 피드백 {total_agg}건 | "
        f"신뢰도: {hints['confidence']}"
    )

    return {
        "windowed":       windowed,
        "tiered_profile": tiered,
        "curation_hints": hints,
        "total_feedback": total_agg,
        "data_window":    data_window,
        "summary":        summary,
    }
```

---

## 7. Claude에 선호도 인사이트 요청

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
    windowed = analysis["windowed"]

    prompt = f"""다음은 Discord AI 뉴스 봇의 피드백 분석 결과입니다.
분석 기간: {analysis['data_window']} (피드백 기사 {windowed['total_articles_with_feedback']}개)

## 소스별 반응
{json.dumps(windowed["by_source"][:10], ensure_ascii=False, indent=2)}

## 키워드별 반응 (상위 10개)
{json.dumps(windowed["by_keyword"][:10], ensure_ascii=False, indent=2)}

## 좋아요 많은 기사 제목
{chr(10).join(f'  - {t}' for t in windowed["most_liked_titles"][:5])}

## 싫어요 많은 기사 제목
{chr(10).join(f'  - {t}' for t in windowed["most_disliked_titles"][:5])}

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

## 8. 큐레이터/에이전트 통합

`build_curation_hints()` 결과를 `news_curation_agent.py`의 `_tool_review_articles`에 주입:

```python
analysis = run_preference_analysis()
hints = analysis["curation_hints"]

print(f"[선호도] {analysis['summary']}")

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
