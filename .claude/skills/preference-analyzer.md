---
name: preference-analyzer
description: 사용자의 기사 선호도를 DB에서 분석하고 저장하는 패턴. database.py의 source_preferences / keyword_preferences 테이블을 읽고 쓸 때 사용.
---

# Preference Analyzer — 선호도 분석 & 저장 패턴

이 프로젝트에서 사용자 선호도는 SQLite의 두 테이블에 누적된다.

| 테이블 | 키 | 배율 범위 | delta |
|--------|-----|-----------|-------|
| `source_preferences` | source (문자열) | 0.1 – 5.0 | ±0.15 |
| `keyword_preferences` | keyword (문자열) | 0.1 – 5.0 | ±0.05 |

---

## 1. 선호도 읽기 (분석용)

```python
import database as db

prefs = db.get_all_preferences()
# prefs = {
#   "sources":  [{"source": "ArXiv", "multiplier": 1.45, "total_likes": 3, "total_dislikes": 0}, ...],
#   "keywords": [{"keyword": "llm",  "multiplier": 1.30, "total_likes": 6, "total_dislikes": 0}, ...],
# }
```

### 선호/비선호 분리

```python
liked_sources   = [s for s in prefs["sources"]  if s["multiplier"] > 1.1]
disliked_sources = [s for s in prefs["sources"] if s["multiplier"] < 0.9]
liked_keywords  = [k for k in prefs["keywords"] if k["multiplier"] > 1.1][:10]
```

### 선호도 요약 딕셔너리 생성

에이전트/큐레이터에 전달할 간결한 구조:

```python
def build_preference_summary(prefs: dict) -> dict:
    return {
        "liked_sources":    [s["source"]  for s in prefs["sources"]  if s["multiplier"] > 1.1][:5],
        "disliked_sources": [s["source"]  for s in prefs["sources"]  if s["multiplier"] < 0.9][:5],
        "liked_keywords":   [k["keyword"] for k in prefs["keywords"] if k["multiplier"] > 1.1][:10],
        "total_feedback":   sum(s["total_likes"] + s["total_dislikes"] for s in prefs["sources"]),
    }
```

---

## 2. 선호도 업데이트

### 소스 선호도 변경

```python
db.update_source_preference(source="ArXiv", liked=True)   # multiplier += 0.15
db.update_source_preference(source="SpamBlog", liked=False) # multiplier -= 0.15
```

### 키워드 선호도 변경

```python
from ranker import extract_keywords

keywords = extract_keywords(article["title"] + " " + article["description"])
for kw in set(keywords):
    db.update_keyword_preference(kw, liked=True)
```

### 피드백 일괄 적용 (추천 방식)

`ranker.apply_feedback`가 소스 + 키워드를 한 번에 처리:

```python
from ranker import apply_feedback

success = apply_feedback(message_id="123456789", liked=True)
# Discord message_id → DB에서 기사 찾아 소스/키워드 모두 업데이트
```

---

## 3. 선호도를 Claude 프롬프트에 주입

```python
def format_preferences_for_prompt(summary: dict) -> str:
    lines = []
    if summary["liked_sources"]:
        lines.append(f"User prefers: {', '.join(summary['liked_sources'])}")
    if summary["disliked_sources"]:
        lines.append(f"User dislikes: {', '.join(summary['disliked_sources'])}")
    if summary["liked_keywords"]:
        lines.append(f"User enjoys topics: {', '.join(summary['liked_keywords'])}")
    return "\n".join(lines) if lines else "No preference data yet."
```

큐레이터 Phase 3(`_select_best`)에서 이 패턴을 사용:

```python
pref_block = "\n## User Preferences\n" + format_preferences_for_prompt(summary)
```

---

## 4. 피드백 부족 시 콜드 스타트

데이터가 없을 때(total_feedback == 0)는 기본값 1.0이 모두 동일 → 다양성 위주 선별 적용:

```python
if summary["total_feedback"] == 0:
    # 선호도 힌트 없이 다양성 기준으로만 큐레이션
    pass  # curator.py _select_best가 선호도 블록을 생략함
```

---

## 5. 선호도 초기화

```python
db.reset_preferences()  # 모든 source_preferences, keyword_preferences 삭제
```

Discord 커맨드: `!reset` (관리자 전용)
