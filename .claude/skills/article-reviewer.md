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
| 스팸·광고 | "sponsored", "buy now", "sign up" 등 | 즉시 제외 |
| 단순 뉴스 | 모델 발표·기업 소식만, 개발자 활용법 없음 | 제외 |
| 실용성 없음 | 코드 예시·구체적 기법·재현 가능한 팁 없음 | 점수 감점 |
| 중복 이벤트 | 동일 주제·기법 아티클 ≥ 2개 | 1개만 유지 |
| 저품질 출처 | SEO 어뷰징 도메인, AI 생성 단순 요약 블로그 | 점수 감점 |
| 실용 콘텐츠 | 튜토리얼·how-to·사례 연구·코드 포함 | 점수 가점 |
| 선호 소스/키워드 | DB 선호도 multiplier 반영 | 점수 가점 |

---

## 2. 검토 파이프라인

### 1단계: 규칙 기반 빠른 필터

`config.AI_KEYWORDS`에 포함된 키워드가 제목+설명에 없으면 제외.
스팸 정규식 패턴으로 광고성 문구 감지.

구현: `agents/news_curation_agent.py` `_tool_review_articles()` 참조.

### 2단계: URL 기준 중복 제거

URL을 집합(`set`)으로 관리해 중복 제거. 구현 동일 위치 참조.

### 3단계: Claude 심층 검토

후보 수가 `target_count`보다 많을 때만 Claude 호출.
선호 소스·비선호 소스·선호 키워드를 프롬프트에 주입하고,
Keep/Reject 판정 + `curator_reason` 보강을 요청한다.

출력: 선별된 기사 JSON 배열. `curator.py` `_extract_json_array()` 로 파싱.

### 4단계: 선호도 기반 정렬

`ranker.rank_articles(articles)` — `final_score = normalize(platform_score) × source_multiplier × avg(keyword_multipliers)`.
`platform_score`가 없는 기사는 기본값 `100.0` 설정 후 호출.

---

## 3. 실용 콘텐츠 신호 (Keep 우선)

제목·설명에 다음이 포함되면 우선 통과:
`tutorial`, `how to`, `guide`, `tips`, `workflow`, `step-by-step`, `example`,
`best practice`, `deep dive`, `walkthrough`, `사용법`, `튜토리얼`, `가이드`, `팁`, `실전`, `예제`

---

## 4. Claude 검토 프롬프트 원칙

1. KEEP: 튜토리얼·how-to·워크플로우 팁·코드 예시·구체적 결과가 있는 사례 연구
2. KEEP: 프롬프트 엔지니어링·MCP·에이전트 패턴·LLM 통합 심층 분석
3. REJECT: 개발자 활용 팁 없는 순수 뉴스 (모델 발표 등)
4. REJECT: 스폰서 콘텐츠, 일반 AI 과장, 얄팍한 listicle
5. REJECT: 근중복 (같은 기법을 다룬 아티클이 여러 개 → 최선 1개만 유지)
6. PREFER: 1차 소스·실무자 블로그·공식 문서 > 어그리게이터 사이트
