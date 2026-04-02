---
name: article-finder
description: 웹 검색으로 AI 개발자 아티클을 탐색하는 패턴. 멀티 에이전트 오케스트레이션, 하네스 엔지니어링, AI 코드 수정 등 개발자 실용 콘텐츠를 수집할 때 사용.
---

# Article Finder — AI 개발자 아티클 탐색 패턴

대상 독자: AI 시스템을 구축하고 운영하는 소프트웨어 엔지니어.
`web_search_20260209` 도구로 웹 검색을 수행한다.

## 탐색 토픽 목록

| 토픽명 | 검색 대상 |
|--------|----------|
| `claude_code_tips` | Claude Code CLI 사용법·슬래시 커맨드·훅·CLAUDE.md 패턴·서브에이전트 워크플로우 |
| `prompt_engineering` | 프롬프트 엔지니어링 기법·시스템 프롬프트·CoT·구조화 출력 |
| `ai_coding_tools` | GitHub Copilot·Codeium 등 AI 코딩 도구 실전 팁 (Cursor 제외) |
| `multi_agent_orchestration` | 멀티 에이전트 시스템·supervisor-worker 패턴·LangGraph·CrewAI·AutoGen |
| `harness_engineering` | LLM 평가 하네스·프롬프트 회귀 테스트·AI CI/CD·LLM 옵저버빌리티 |
| `ai_code_modification` | AI 기반 대규모 코드 수정·자동화 마이그레이션·멀티파일 리팩토링 |
| `dev_productivity` | LLM 기반 코드 리뷰·테스트 생성·문서화·디버깅 워크플로우 |
| `llm_best_practices` | 컨텍스트 윈도우 관리·RAG·비용 최적화·지연 감소 실전 기법 |
| `korean_practitioner` | 한국어 AI 활용 아티클 (Velog·브런치·블로그) |
| `community_tips` | HN·Reddit r/ClaudeAI·Twitter 개발자 커뮤니티 실전 팁 스레드 |
| `tutorials_deep_dive` | Claude/OpenAI API 통합·에이전트 시스템 설계 심층 튜토리얼 |

## 반환 아티클 필드

| 필드 | 설명 |
|------|------|
| `url` | 기사 URL |
| `title` | 제목 |
| `source` | 출처 (예: "Simon Willison's Weblog") |
| `description` | 2–3문장 요약 |
| `author` | 저자 |
| `published_at` | 발행일 (YYYY-MM-DD) |
| `curator_reason` | 선택 이유 (개발자에게 구체적으로 어떤 가치가 있는지 1문장) |

## 검색 전략

- 토픽당 2–4회 타겟 검색 후 JSON 출력 (과도한 검색 금지)
- 이미 수집한 URL은 `already_collected` 집합으로 중복 방지
- 오늘 게시된 URL은 `db.get_todays_posted_urls()`로 제외
- 48시간 이내 발행 기사 우선

## 고품질 소스 예시

Simon Willison's Weblog, Hamel Husain's Blog, eugeneyan.com, LangChain Blog,
Weights & Biases Blog, HackerNews (AI 스레드), Anthropic Engineering Blog,
개인 기술 블로그 (Velog·브런치·GitHub Pages)
