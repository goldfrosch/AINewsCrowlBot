---
name: ralph-loop
description: Multi-round iterative research pattern used in curator.py. Use when modifying the Claude research engine — round management, topic rotation, deduplication, early stopping, and final selection phases. Target content: practical AI tool usage articles for developers (Claude Code, Cursor, prompt engineering, MCP, agent patterns).
---

# Ralph Loop — 다중 라운드 리서치 패턴

이 프로젝트의 `curator.py`에서 사용하는 AI 실용 아티클 수집 방식.
대상 독자: Claude Code, Cursor 등 AI 코딩 도구를 매일 사용하는 소프트웨어 엔지니어.
단일 대형 호출 대신 토픽별 소형 호출을 반복해 안정성과 다양성을 모두 확보한다.

## 3단계 구조

```
Phase 1: 다중 라운드 리서치   (최대 10회, 라운드당 ~4000 토큰)
    ↓
Phase 2: URL 기준 중복 제거  (dict[url → article] 자동 dedup)
    ↓
Phase 3: 최종 선별 호출      (web_search 없이 판단만, ~8000 토큰)
```

## Phase 1 — 라운드 설계 원칙

### 토픽 배정

각 라운드는 서로 다른 토픽에 집중한다 (일반 뉴스 X, 실용 아티클 O):

```
R1: claude_code_tips      R6: llm_best_practices
R2: prompt_engineering    R7: agent_patterns
R3: ai_coding_tools       R8: korean_practitioner
R4: mcp_tools             R9: community_tips
R5: dev_productivity      R10: tutorials_deep_dive
```

### 조기 종료 조건

```python
if len(all_raw) >= target_count * 3:
    break  # 목표의 3배 확보 시 조기 종료
```

### 라운드 실패 허용 (핵심)

```python
except anthropic.RateLimitError:
    time.sleep(30)
    # 재시도 1회 후도 실패 시 → 해당 라운드만 건너뜀
    return []  # 전체 루프 중단 없음

except anthropic.APIStatusError as e:
    return []  # 529 과부하 등 → 건너뜀
```

### 제외 URL 관리

```python
all_excluded = list(set(exclude_urls) | already_found_urls)
# exclude_urls: 오늘 이미 Discord에 게시된 URL
# already_found_urls: 현재 루프에서 수집한 URL (라운드 간 중복 방지)
```

## Phase 2 — 중복 제거

```python
all_raw: dict[str, dict] = {}  # url → article dict

for item in raw:
    url = item.get("url", "").strip()
    if url and url not in all_raw and url not in exclude_urls:
        all_raw[url] = item  # URL이 키 → 자동 dedup
```

## Phase 3 — 최종 선별

- `web_search` 도구 **없이** 호출 (비용·속도 절감)
- 전체 후보 JSON을 프롬프트에 포함
- 선별 기준: 최신성 > 중요도 > 다양성 > 근중복 제거 > 사용자 선호도

```python
response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=8000,
    system=_SYSTEM_SELECT,   # 큐레이션 판단 전용 프롬프트
    messages=[{"role": "user", "content": prompt}],
    # tools 없음 — 판단만
)
```

## 새 토픽 추가 방법

`curator.py`의 `_TOPICS` 리스트에 튜플 추가:

```python
_TOPICS: list[tuple[str, str]] = [
    ("topic_name", "Search instruction targeting practical developer content..."),
    ...
]
```

- 최대 10개 (MAX_ROUNDS 기본값과 맞춤)
- 토픽이 10개 이하면 라운드 수 = 토픽 수
- `topic_desc`는 반드시 "튜토리얼/가이드/팁" 성격임을 명시 — 단순 뉴스 검색어는 피할 것

## 파라미터 조정 기준

| 파라미터              | 기본값              | 조정 기준             |
| --------------------- | ------------------- | --------------------- |
| `max_rounds`          | 10                  | API 비용 절감 시 줄임 |
| `per_round_count`     | `max(3, target//2)` | 라운드당 수집 밀도    |
| `target_count * 3`    | 조기종료 임계값     | 후보 다양성 vs 속도   |
| 라운드간 `sleep`      | 1초                 | rate limit 여유       |
| RateLimit 재시도 대기 | 30초                | 새벽 부하 상황 맞춤   |
