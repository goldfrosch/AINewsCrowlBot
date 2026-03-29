---
name: claude-api
description: Guide for using the Anthropic Claude API and SDKs in Python. Use when writing or modifying code that calls the Anthropic API — model selection, tool use, streaming, thinking mode, error handling.
source: https://skills.sh/anthropics/skills/claude-api
---

# Claude API — Python Reference

## 기본 원칙

- **기본 모델**: `claude-opus-4-6` (명시적 지시 없으면 항상 이 모델 사용)
- **Thinking**: `thinking: {"type": "adaptive"}` (복잡한 추론 작업에 적용)
- **Streaming**: 긴 입출력에는 반드시 스트리밍 사용 (타임아웃 방지)

## 언제 어떤 방식을 쓸까

| 목적                              | 방식                       |
| --------------------------------- | -------------------------- |
| 분류·요약·추출·Q&A (단발)         | `client.messages.create()` |
| 멀티스텝 코드 제어 워크플로       | API + tool use             |
| 파일/웹/셸 접근이 필요한 에이전트 | Agent SDK                  |
| 커스텀 도구로 최대 유연성         | API + agentic loop         |

## 핵심 패턴

### 단순 호출

```python
import anthropic

client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수 자동 읽기

response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.content[0].text)
```

### 스트리밍 (긴 응답 필수)

```python
with client.messages.stream(
    model="claude-opus-4-6",
    max_tokens=8000,
    messages=[{"role": "user", "content": prompt}],
) as stream:
    response = stream.get_final_message()
```

### Tool Use (web_search 포함)

```python
with client.messages.stream(
    model="claude-opus-4-6",
    max_tokens=4000,
    tools=[{"type": "web_search_20260209", "name": "web_search"}],
    system=system_prompt,
    messages=[{"role": "user", "content": user_prompt}],
) as stream:
    response = stream.get_final_message()

# 응답에서 text block 추출
for block in response.content:
    if block.type == "text":
        print(block.text)
```

### Adaptive Thinking

```python
# ✅ 올바른 방식 (2026 이후)
thinking={"type": "adaptive"}

# ❌ 더 이상 사용하지 않음
# thinking={"type": "enabled", "budget_tokens": 5000}
```

## 에러 처리

```python
try:
    response = client.messages.create(...)
except anthropic.AuthenticationError:
    raise RuntimeError("API 키가 유효하지 않습니다.")
except anthropic.RateLimitError:
    time.sleep(30)  # 재시도 전 대기
    # 재시도 로직
except anthropic.APIStatusError as e:
    # e.status_code: 529 = 서버 과부하
    print(f"API 오류 ({e.status_code}): {e.message}")
except anthropic.APIError as e:
    raise RuntimeError(f"API 오류: {e}")
```

## 모델 가격표 (2026-02-17)

| 모델       | ID                          | 입력  | 출력   | 컨텍스트       |
| ---------- | --------------------------- | ----- | ------ | -------------- |
| Opus 4.6   | `claude-opus-4-6`           | $5/1M | $25/1M | 200K (1M beta) |
| Sonnet 4.6 | `claude-sonnet-4-6`         | $3/1M | $15/1M | 200K (1M beta) |
| Haiku 4.5  | `claude-haiku-4-5-20251001` | $1/1M | $5/1M  | 200K           |

## 이 프로젝트에서의 사용 위치

- `curator.py` — `curate()` 함수: `web_search_20260209` 도구로 AI 뉴스 리서치
  - 라운드당 `max_tokens=4000` (새벽 rate limit 대비)
  - 최종 선별 시 `max_tokens=8000` (web_search 없는 판단 전용 호출)
