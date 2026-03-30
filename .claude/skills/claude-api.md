---
name: claude-api
description: Guide for using the Anthropic Claude API and SDKs in Python. Use when writing or modifying code that calls the Anthropic API — model selection, tool use, streaming, thinking mode, error handling.
source: https://skills.sh/anthropics/skills/claude-api
---

# Claude API — Python Reference

## 기본 원칙

- **기본 모델**: `claude-opus-4-6` (명시적 지시 없으면 항상 이 모델 사용)
- **Thinking**: `thinking={"type": "adaptive"}` — `"enabled"` 방식은 더 이상 사용하지 않음
- **Streaming**: 긴 입출력에는 반드시 `client.messages.stream()` 사용 (타임아웃 방지)

## 언제 어떤 방식을 쓸까

| 목적                              | 방식                       |
| --------------------------------- | -------------------------- |
| 분류·요약·추출·Q&A (단발)         | `client.messages.create()` |
| 멀티스텝 코드 제어 워크플로       | API + tool use             |
| 파일/웹/셸 접근이 필요한 에이전트 | Agent SDK                  |
| 커스텀 도구로 최대 유연성         | API + agentic loop         |

## Tool Use — web_search 도구 형식

`web_search_20260209`는 tools 리스트에 아래 형식으로 지정한다 (이름 오탈자 주의):

```python
tools=[{"type": "web_search_20260209", "name": "web_search"}]
```

이 프로젝트에서 사용 위치: `curator.py` `_research_round()` 참조.

## 에러 처리 — 주요 예외 클래스

| 예외 | 원인 | 처리 |
|------|------|------|
| `anthropic.AuthenticationError` | API 키 무효 | 즉시 중단 |
| `anthropic.RateLimitError` | 요청 한도 초과 | `time.sleep(30)` 후 재시도 |
| `anthropic.APIStatusError` | 서버 오류 (529 = 과부하) | 라운드 건너뜀 |
| `anthropic.APIError` | 기타 API 오류 | 로그 후 중단 |

이 프로젝트에서의 에러 처리 패턴: `curator.py` `_research_round()` 참조.

## 모델 가격표 (2026-02-17)

| 모델       | ID                          | 입력  | 출력   | 컨텍스트       |
| ---------- | --------------------------- | ----- | ------ | -------------- |
| Opus 4.6   | `claude-opus-4-6`           | $5/1M | $25/1M | 200K (1M beta) |
| Sonnet 4.6 | `claude-sonnet-4-6`         | $3/1M | $15/1M | 200K (1M beta) |
| Haiku 4.5  | `claude-haiku-4-5-20251001` | $1/1M | $5/1M  | 200K           |

## 이 프로젝트에서의 사용 위치

- `curator.py` — `_research_round()`: `web_search_20260209` 도구로 AI 뉴스 리서치, 라운드당 `max_tokens=4000`
- `curator.py` — `_select_best()`: web_search 없는 판단 전용 호출, `max_tokens=8000`
- `agents/news_curation_agent.py` — `run()`: tool-use 기반 agentic loop
