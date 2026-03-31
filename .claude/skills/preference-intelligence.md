---
name: preference-intelligence
description: 누적된 👍/👎 피드백 데이터를 심층 분석해 큐레이션 품질을 개선하는 패턴. 최근 7일 데이터 최우선, 부족 시 14일→30일→전체로 점진 확장. 신뢰도 필터링, 소스/키워드 티어링, 큐레이션 힌트 생성까지 포함. preference-analyzer.md의 CRUD를 넘어서는 인사이트 추출 레이어.
---

# Preference Intelligence

구현 파일: `agents/preference_analysis.py`

## 주요 함수

| 함수 | 역할 |
|------|------|
| `get_windowed_feedback(days)` | N일 내 소스·키워드별 likes/dislikes 집계 |
| `find_sufficient_window()` | 7일→14일→30일→전체 순 자동 확장, 충분한 첫 윈도우 반환 |
| `describe_window(days)` | 윈도우를 가독 문자열로 변환 |
| `filter_reliable(items)` | likes+dislikes < MIN_FEEDBACK 항목 제거 |
| `_ratio_to_tier(likes, dislikes)` | 비율→5단계 티어(강선호/선호/중립/비선호/강비선호) |
| `build_tiered_profile(windowed)` | 소스·키워드를 티어별로 분류 |
| `build_curation_hints(tiered, windowed, total)` | 티어 프로파일 → 큐레이터 주입용 hints dict |
| `run_preference_analysis()` | 전체 파이프라인 실행, 결과 dict 반환 |
| `save_preference_profile(analysis)` | `data/preference_profile.json` 저장 |
| `load_preference_profile()` | 저장된 프로파일 로드 (없으면 None) |

## 힌트 구조 (`build_curation_hints` 반환값)

```json
{
  "boost_sources":  ["..."],
  "avoid_sources":  ["..."],
  "focus_keywords": ["..."],
  "skip_keywords":  ["..."],
  "cold_start":     false,
  "confidence":     "low|medium|high",
  "data_window":    "최근 7일"
}
```
