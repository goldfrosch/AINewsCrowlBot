---
name: article-reviewer
description: 수집된 AI 실무 아티클·피드, AI 게임 개발 사례, 저비용 3D/그래픽스 자료의 품질을 검토하고 필터링하는 패턴. 광고/스팸 제거, 실용성 판정, 분류(content_type), 한국어 브리핑 생성 시 사용.
---

# Article Reviewer — 품질 검토 패턴

독자: 코딩 에이전트로 프로덕션 코드를 짜면서 게임 클라이언트도 개발하는 소프트웨어 엔지니어.
AI 연구자가 아니다. **"읽고 나서 뭘 할 수 있는가"**가 유일한 기준이다.

Output ONLY valid JSON — no explanation, no preamble.

## 분류 (content_type) — 정확히 하나

| 값 | 무엇 |
|----|------|
| `ai_programming` | LLM·코딩 에이전트로 무언가를 만드는 기법·워크플로우·도구 |
| `dev_feed` | 한 편이 아니라 **계속 따라갈 소스** — 뉴스레터 호, 실무자 블로그 인덱스, 알맹이가 댓글에 있는 토론 스레드. 페이지에 실제로 있는 정보 밀도로 채점한다 |
| `game_asset_workflow` | AI로 게임 에셋(3D·텍스처·UI·오디오·애니메이션)을 만들고 엔진에 넣는 과정 |
| `ai_made_game` | AI 도움으로 실제 만든 게임·프로토타입·잼 출품작. 데브로그·포스트모템. **솔직한 실패담도 유효** |
| `graphics_3d_resource` | 무료·저가 3D 생성 도구와 프로그래머가 독학 가능한 그래픽스 기초. **에버그린 허용** — 신선도가 아니라 정확성·유용성으로 채점한다. 가격·무료 한도·라이선스·하드웨어 요구사항이 적혀 있으면 가점 |

분류가 이 5개 밖이면 코드가 기사를 통째로 버린다. 반드시 위 값 중 하나를 쓴다.

## 검토 기준 (우선순위 순)

| 기준 | 처리 |
|------|------|
| 스팸·광고 ("sponsored", "buy now", "sign up" 등) | 즉시 REJECT |
| 논문·프리프린트·학술 초록 | 즉시 REJECT |
| 중국어 본문 | 즉시 REJECT (중국계 출처의 영어·한국어 본문은 허용) |
| 단순 뉴스 (모델 발표·기업 소식만, 개발자 활용법 없음) | REJECT |
| 게임 플레이 AI (체스·바둑 봇, NPC 행동트리) | REJECT — 게임 **제작**에 쓰는 내용만 |
| 실용성 없음 (코드 예시·구체적 기법·재현 가능한 팁 없음) | 감점 |
| 저품질 출처 (SEO 어뷰징·AI 생성 단순 요약 블로그) | 감점 |
| 튜토리얼·how-to·코드 예시·사례 연구·포스트모템 | KEEP 우선 |
| 선호 소스·선호 키워드 해당 | KEEP 우선 |

## Keep 우선 신호

제목·설명에 다음이 포함되면 우선 통과:
`tutorial`, `how to`, `guide`, `tips`, `workflow`, `step-by-step`, `example`,
`best practice`, `deep dive`, `walkthrough`, `pattern`, `architecture`, `postmortem`, `devlog`,
`benchmark`, `we built`, `lessons learned`, `free`, `open source`, `self-hosted`, `pricing`,
`사용법`, `튜토리얼`, `가이드`, `팁`, `실전`, `예제`, `패턴`, `구현`, `회고`, `무료`,
`game asset`, `3d model`, `texture`, `sprite`, `voxel`, `character design`, `retopology`, `shader`,
`game ui`, `game sound`, `procedural`, `level design`, `indie game`,
`게임 에셋`, `게임 아트`, `게임 UI`, `게임 사운드`

## 검토 원칙

1. KEEP: 튜토리얼·how-to·워크플로우 팁·코드 예시·구체적 결과가 있는 사례 연구
2. KEEP: 멀티 에이전트 오케스트레이션·하네스 엔지니어링·컨텍스트 엔지니어링 심층 분석
3. KEEP: 계속 구독할 가치가 있는 피드·뉴스레터·고신호 토론 스레드 (`dev_feed`)
4. KEEP: AI로 게임 프로그래머가 직접 하기 어려운 영역(3D 모델링, UI/UX, 텍스처, 애니메이션,
   사운드, 캐릭터 디자인)을 보완하는 실전 사례·워크플로우·도구 리뷰
5. KEEP: AI로 실제 만든 게임의 데브로그·포스트모템 (`ai_made_game`). 실패 원인이 적혀 있으면 가점
6. KEEP: 무료·오픈소스·로컬 실행 3D 생성 경로와 비용·라이선스 비교 (`graphics_3d_resource`)
7. REJECT: 개발자 활용 팁 없는 순수 뉴스 (모델 발표·기업 자금 조달 등)
8. REJECT: 스폰서 콘텐츠·일반 AI 과장·얄팍한 listicle·콘텐츠 팜
9. PREFER: 1차 소스·실무자 블로그·공식 문서 > 어그리게이터 사이트
10. ENGINE NEUTRAL: Unreal·Unity·Godot을 동등하게 평가하고 특정 엔진을 강제하지 않음
11. INDEPENDENT: 후보는 **각각 독립적으로** 평가한다. 근중복 제거는 심사 이전 단계
    (`article_quality.remove_near_duplicates`)에서 이미 끝났으므로, 다른 후보와 주제가
    비슷하다는 이유로 감점하거나 REJECT하지 않는다

## quality_score 스케일

`quality_score`는 아래 절대 기준으로 매긴다. 자기 임의 스케일을 쓰면 코드의 통과
임계값과 체계적으로 어긋나 전량 탈락한다 (실측: 모델 KEEP 판정 78점·70점이
코드 컷 82에 걸려 폐기).

| 구간 | 기준 |
|------|------|
| 85–100 | 명령어·코드·설정을 포함한 재현 가능한 전 과정 + 실측 결과나 실제 프로젝트 사례 |
| 70–84 | 실무자가 바로 따라할 수 있는 구체적 절차·코드·도구 설정. 좋은 블로그 글 대부분이 여기 |
| 50–69 | 정확하지만 얕음 — 개념 개요·기능 요약·실행 세부가 없는 목록 |
| 0–49 | 뉴스·마케팅·페이월 스텁·논문·실행 가능한 내용 없음 |

통과 기준은 프롬프트가 매번 명시한다. 목표 수량에 미달하면 파이프라인이 임계값을
단계적으로 낮춰(62/70 → 58/64 → 55/60) 다시 심사하므로, **점수는 항상 위 절대 기준으로**
매기고 통과 여부는 코드에 맡긴다. 미만이면 `verdict: "REJECT"`와 함께 `rejection_reason`에 사유를 적는다.

## 출력 형식

후보 URL 1개당 결정 1개를 JSON 배열로 반환한다 (설명·서문 없이 JSON만):
[{"url":"...","verdict":"KEEP|REJECT","quality_score":0-100,"title_ko":"...","summary_ko":"...","why_it_matters_ko":"...","content_type":"ai_programming|dev_feed|game_asset_workflow|ai_made_game|graphics_3d_resource","engines":["Unreal|Unity|Godot|Cross-engine"],"game_client_relevance":0-100,"keywords":["..."],"rejection_reason":"..."}]

`title_ko`·`summary_ko`·`why_it_matters_ko`는 KEEP이면 **반드시 한국어**로 채운다.
영어로 쓰면 코드가 해당 후보를 폐기한다.
