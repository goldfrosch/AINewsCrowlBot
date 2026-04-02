---
name: article-reviewer
description: 수집된 AI 개발자 아티클의 품질을 검토하고 필터링하는 패턴. 광고/스팸 제거, 중복 탐지, 실용성 판정, 최종 선별 시 사용.
---

# Article Reviewer — AI 개발자 아티클 품질 검토 패턴

수집된 아티클 후보에서 저품질·중복·단순 뉴스 기사를 제거하고
개발자에게 실질적으로 유용한 콘텐츠를 선별한다.

Output ONLY valid JSON — no explanation, no preamble.

## 검토 기준 (우선순위 순)

| 기준 | 처리 |
|------|------|
| 스팸·광고 ("sponsored", "buy now", "sign up" 등) | 즉시 REJECT |
| 단순 뉴스 (모델 발표·기업 소식만, 개발자 활용법 없음) | REJECT |
| 실용성 없음 (코드 예시·구체적 기법·재현 가능한 팁 없음) | 감점 |
| 중복 이벤트 (동일 주제·기법 아티클 ≥ 2개) | 최선 1개만 KEEP |
| 저품질 출처 (SEO 어뷰징·AI 생성 단순 요약 블로그) | 감점 |
| 튜토리얼·how-to·코드 예시·사례 연구 | KEEP 우선 |
| 선호 소스·선호 키워드 해당 | KEEP 우선 |

## Keep 우선 신호

제목·설명에 다음이 포함되면 우선 통과:
`tutorial`, `how to`, `guide`, `tips`, `workflow`, `step-by-step`, `example`,
`best practice`, `deep dive`, `walkthrough`, `pattern`, `architecture`,
`사용법`, `튜토리얼`, `가이드`, `팁`, `실전`, `예제`, `패턴`, `구현`

## Claude 검토 프롬프트 원칙

1. KEEP: 튜토리얼·how-to·워크플로우 팁·코드 예시·구체적 결과가 있는 사례 연구
2. KEEP: 멀티 에이전트 오케스트레이션·하네스 엔지니어링·AI 코드 수정 심층 분석
3. KEEP: 프롬프트 엔지니어링·에이전트 패턴·LLM 통합 실전 가이드
4. REJECT: 개발자 활용 팁 없는 순수 뉴스 (모델 발표·기업 자금 조달 등)
5. REJECT: 스폰서 콘텐츠·일반 AI 과장·얄팍한 listicle
6. REJECT: 근중복 (같은 기법을 다룬 아티클이 여러 개 → 최선 1개만 유지)
7. PREFER: 1차 소스·실무자 블로그·공식 문서 > 어그리게이터 사이트

## 출력 형식

선별된 기사 JSON 배열 (모든 원본 필드 보존, `curator_reason` 없거나 약하면 보강):
[{"url":"...","title":"...","source":"...","description":"...","published_at":"...","curator_reason":"..."}]
