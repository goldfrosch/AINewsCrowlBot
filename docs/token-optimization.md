# 토큰 최적화 작업 기록

> 작성일: 2026-04-06  
> 대상 파일: `agents/news_curation_agent.py`, `.claude/agents/news-curation-agent.md`

---

## 배경

`_tool_find_ai_articles` 실행 시 토큰 소모가 과도하고 탐색 시간이 길다는 문제가 발견됐다.  
원인을 분석한 결과, 토큰 낭비 포인트가 크게 세 곳으로 확인됐다.

### 문제 원인 분석

| 원인 | 설명 |
|------|------|
| **오케스트레이터 루프** | Claude가 도구 호출 순서를 "판단"하는 `while True` 루프가 매 step마다 `client.messages.create()` 를 별도 호출하고, 이전 step의 모든 messages를 누적해서 전달 |
| **과도한 웹 검색** | `"2–4 targeted searches"` 지시로 인해 최대 24회(6토픽 × 4회)까지 검색 가능. 각 검색 결과 전체가 input_tokens로 계산됨 |
| **토픽 설명 비대** | 토픽당 설명이 40~80토큰으로 구성되어 user 메시지에 매번 포함 |
| **review 단계** | 수집된 기사 JSON 전체를 다시 Claude에 전달하고, 별도 API 호출(`max_tokens=6000`)로 검토 수행 |

---

## 변경 사항

### 1. 토픽 설명 단축

**파일:** `.claude/agents/news-curation-agent.md`

각 토픽 설명을 2~3문장에서 한 줄로 축약했다.

```yaml
# 변경 전 (토픽당 40~80토큰)
claude_code_tips: "Articles, tutorials, and tips for using Claude Code CLI effectively:
  slash commands, hooks, CLAUDE.md patterns, agentic coding techniques, subagent workflows.
  Search: 'Claude Code tips', 'Claude Code workflow', 'Claude Code tutorial', 'agentic coding Claude'."

# 변경 후 (토픽당 ~10토큰)
claude_code_tips: "Claude Code CLI tips, hooks, CLAUDE.md patterns, subagent workflows"
```

- 11개 토픽 전체 적용
- 절감량: 약 **500~700토큰/호출**

---

### 2. 웹 검색 횟수 제한

**파일:** `agents/news_curation_agent.py`, `.claude/agents/news-curation-agent.md`

```python
# 변경 전
"- 2–4 targeted searches, then output JSON immediately"

# 변경 후
"- Maximum 2 targeted searches, then output JSON immediately"
```

`news-curation-agent.md` 시스템 프롬프트에도 동일하게 "검색은 최대 2회" 명시.

---

### 3. `max_tokens` 축소

**파일:** `agents/news_curation_agent.py` — `_tool_find_ai_articles()`

```python
# 변경 전
max_tokens=4000

# 변경 후
max_tokens=2000
```

본문 호출 및 RateLimitError 재시도 분기 모두 적용.

---

### 4. `review_articles` 단계 완전 제거

**파일:** `agents/news_curation_agent.py`

수집된 기사를 다시 Claude에 전달해 검토하는 단계를 제거했다.  
동일한 기사를 필터링할 확률이 낮고, 추가 API 호출 비용이 더 크다는 판단.

제거된 항목:
- `_tool_review_articles()` 함수 전체
- `_TOOLS` 리스트의 `review_articles` 스키마
- `_SKILL_REVIEWER` 로더 (`_load_skill("article-reviewer")`)
- `AI_KEYWORDS` import (review 함수에서만 사용)
- `_SPAM_RE` 정규식 (review 함수에서만 사용)

절감량: **Claude 호출 1회 제거 + 기사 JSON 이중 전달 제거**

---

### 5. 오케스트레이터 `while` 루프 제거 (가장 큰 구조 변경)

**파일:** `agents/news_curation_agent.py` — `run()` 함수

#### 기존 구조 (agentic loop)

```
[step1] user 메시지 → Claude → "analyze_preferences 호출해야겠다" (tool_use)
           ↓ tool 실행 후 결과를 messages에 추가
[step2] step1 전체 + tool_result → Claude → "find_ai_articles 호출해야겠다" (tool_use)
           ↓ tool 실행 후 결과를 messages에 추가
[step3] step1+2 전체 + tool_result → Claude → JSON 출력 (end_turn)
```

- `client.messages.create()` **3회** 호출
- messages가 매 step마다 누적 → step3는 step1~2의 모든 내용을 input으로 받음
- 오케스트레이터 Claude가 "다음에 뭘 할지"를 판단하는 비용이 낭비

#### 변경 후 구조 (직접 호출)

```python
# 1단계: 선호도 분석 (DB 읽기, API 호출 없음)
preferences = _tool_analyze_preferences()

# 2단계: 기사 탐색 (Claude API 1회)
result = _tool_find_ai_articles(client, topics, target_count, set())
return result.get("articles", [])
```

- `client.messages.create()` **0회** (find 내부 1회만 남음)
- messages 누적 없음
- 흐름이 항상 고정되어 있으므로 Claude의 판단이 불필요

#### 제거된 코드

- `_TOOLS` 리스트 전체 (도구 스키마 정의)
- `while True` 루프 및 `loop_step` 카운터
- `messages` 리스트 및 누적 관리 로직
- `system_prompt` 생성 (`_AGENT_SPEC["system_prompt_template"]` 참조 포함)
- `hints_text` user 메시지 주입 로직
- `all_found`, `collected` 추적 변수
- `agent_loop_step{N}` 토큰 로깅 (오케스트레이터 호출 자체가 사라짐)
- `_load_agent_spec()`의 `system_prompt_template` 파싱

---

## 변경 전후 API 호출 비교

```
변경 전:
  오케스트레이터 step1  (analyze_preferences 판단)   ← Claude API 호출
  오케스트레이터 step2  (find_ai_articles 판단)      ← Claude API 호출
  오케스트레이터 step3  (JSON 출력 판단)             ← Claude API 호출
  find 내부             (web_search 실제 수행)        ← Claude API 호출
  review                (기사 검토)                   ← Claude API 호출
  ──────────────────────────────────────────────────────
  총 5번

변경 후:
  find 내부             (web_search 실제 수행)        ← Claude API 호출
  ──────────────────────────────────────────────────────
  총 1번
```

---

## 구조 변경 요약

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| 토픽 설명 | 토픽당 40~80토큰 | 토픽당 ~10토큰 |
| 웹 검색 횟수 | 최대 4회 | 최대 2회 |
| `max_tokens` (find) | 4000 | 2000 |
| review 단계 | Claude 호출 1회 (max 6000) | 제거 |
| 오케스트레이터 루프 | Claude 호출 3회 + messages 누적 | 제거 |
| 전체 API 호출 수 | 5회 | 1회 |
