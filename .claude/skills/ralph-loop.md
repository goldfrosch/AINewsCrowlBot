---
name: ralph-loop
description: Multi-round iterative research pattern used in curator.py. Use when modifying the Claude research engine — round management, topic rotation, deduplication, early stopping, and final selection phases. Target content: practical AI tool usage articles for developers (Claude Code, Cursor, prompt engineering, MCP, agent patterns).
---

# Ralph Loop — 다중 라운드 리서치 패턴

`curator.py`의 AI 실용 아티클 수집 방식.
대상 독자: Claude Code, Cursor 등 AI 코딩 도구를 매일 사용하는 소프트웨어 엔지니어.

## 3단계 구조

```
Phase 1: 다중 라운드 리서치   (최대 10회, 라운드당 ~4000 토큰)
    ↓
Phase 2: URL 기준 중복 제거  (url을 키로 하는 dict → 자동 dedup)
    ↓
Phase 3: 최종 선별 호출      (web_search 없이 판단만, ~8000 토큰)
```

구현: `curator.py` `curate()`, `_research_round()`, `_select_best()` 참조.

## Phase 1 — 라운드 설계 원칙

### 토픽 배정

각 라운드는 서로 다른 토픽에 집중한다 (일반 뉴스 X, 실용 아티클 O):

| 라운드 | 토픽 |
|--------|------|
| R1 | `claude_code_tips` |
| R2 | `prompt_engineering` |
| R3 | `ai_coding_tools` |
| R4 | `mcp_tools` |
| R5 | `dev_productivity` |
| R6 | `llm_best_practices` |
| R7 | `agent_patterns` |
| R8 | `korean_practitioner` |
| R9 | `community_tips` |
| R10 | `tutorials_deep_dive` |

### 핵심 규칙

- **조기 종료**: 후보 기사가 목표의 3배 이상 확보되면 즉시 루프 종료
- **라운드 실패 허용**: `RateLimitError` / `APIStatusError` 발생 시 해당 라운드만 건너뜀 — 전체 중단 없음
- **중복 제거**: URL을 딕셔너리 키로 사용해 자동 dedup
- **제외 URL**: 오늘 이미 게시된 URL + 현재 루프에서 수집한 URL 모두 제외

## Phase 3 — 최종 선별 기준

`_select_best()` 호출 시 적용 순서:
1. 최신성 (24h 이내 > 24–48h)
2. 중요도 (1차 소스 > 2차 커버리지)
3. 다양성 (토픽 중복 배제)
4. 근중복 제거 (같은 이벤트 → 최선 1개 유지)
5. 사용자 선호도 반영

## 새 토픽 추가 방법

`curator.py`의 `_TOPICS` 리스트에 `(topic_name, topic_desc)` 튜플 추가.

- 최대 10개 (`MAX_ROUNDS` 기본값과 맞춤)
- `topic_desc`는 "튜토리얼/가이드/팁" 성격임을 명시 — 단순 뉴스 검색어 금지

## 파라미터 조정 기준

| 파라미터              | 기본값              | 조정 기준             |
| --------------------- | ------------------- | --------------------- |
| `max_rounds`          | 10                  | API 비용 절감 시 줄임 |
| `per_round_count`     | `max(3, target//2)` | 라운드당 수집 밀도    |
| `target_count * 3`    | 조기종료 임계값     | 후보 다양성 vs 속도   |
| 라운드간 `sleep`      | 1초                 | rate limit 여유       |
| RateLimit 재시도 대기 | 30초                | 새벽 부하 상황 맞춤   |
