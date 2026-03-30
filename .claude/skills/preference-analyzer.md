---
name: preference-analyzer
description: 사용자의 기사 선호도를 DB에서 분석하고 저장하는 패턴. database.py의 source_preferences / keyword_preferences 테이블을 읽고 쓸 때 사용.
---

# Preference Analyzer — 선호도 읽기·쓰기·프롬프트 주입 패턴

## 선호도 테이블 구조

| 테이블 | 키 | 배율 범위 | delta |
|--------|-----|-----------|-------|
| `source_preferences` | source (문자열) | 0.1 – 5.0 | ±0.15 |
| `keyword_preferences` | keyword (문자열) | 0.1 – 5.0 | ±0.05 |

---

## 1. 선호도 읽기

**`db.get_all_preferences()`** — `{sources: [...], keywords: [...]}` 반환.

각 항목 필드: `source`/`keyword`, `multiplier`, `total_likes`, `total_dislikes`.

선호/비선호 분리 기준:
- 선호: `multiplier > 1.1`
- 비선호: `multiplier < 0.9`
- 신뢰 임계값: `total_likes + total_dislikes >= 3` (데이터 부족 항목 제외)

에이전트 내 사용 예: `agents/news_curation_agent.py` `_tool_analyze_preferences()` 참조.

---

## 2. 선호도 업데이트

**`db.update_source_preference(source, liked)`** — 소스 배율 ±0.15 조정.

**`db.update_keyword_preference(keyword, liked)`** — 키워드 배율 ±0.05 조정.

**`ranker.apply_feedback(message_id, liked)`** — Discord `message_id` 기준으로 소스 + 키워드를 한 번에 업데이트. 피드백 처리 시 이 함수를 사용할 것.

키워드 추출: `ranker.extract_keywords(title + " " + description)` 사용.

---

## 3. 선호도를 Claude 프롬프트에 주입

`get_all_preferences()` 결과에서 선호/비선호 소스·키워드를 추출해 프롬프트에 삽입한다.

형식 예시:
```
User prefers: ArXiv, Hugging Face Blog
User dislikes: SpamBlog
User enjoys topics: llm, agent, fine-tuning
```

에이전트 내 사용 예: `agents/news_curation_agent.py` `_tool_review_articles()` 참조.

---

## 4. 콜드 스타트

`total_feedback == 0`이면 모든 배율이 기본값 1.0 → 선호도 힌트 없이 다양성 기준으로만 큐레이션.
`curator.py` `_select_best()`가 이 경우 선호도 블록을 생략한다.

---

## 5. 선호도 초기화

**`db.reset_preferences()`** — 전체 삭제. Discord 커맨드: `!reset` (관리자 전용).
