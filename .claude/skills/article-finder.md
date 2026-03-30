---
name: article-finder
description: 웹 검색으로 AI 도구 실용 아티클을 탐색하는 패턴. curator.py의 Ralph Loop 단일 라운드 또는 에이전트 도구로 Claude Code·Cursor·프롬프트 엔지니어링 등 개발자 실용 콘텐츠를 수집할 때 사용.
---

# Article Finder — AI 실용 아티클 탐색 패턴

`curator.py`의 `_research_round()`를 기반으로 한 아티클 수집 방법.
대상 독자: Claude Code, Cursor 등 AI 코딩 도구를 매일 사용하는 소프트웨어 엔지니어.
`web_search_20260209` 도구로 Claude에게 웹 검색을 위임한다.

---

## 1. 핵심 함수

**`curator._research_round(client, round_num, topic_name, topic_desc, exclude_urls, already_found_urls, count)`**

- `exclude_urls`: `db.get_todays_posted_urls()`로 오늘 이미 게시된 URL 제외
- `already_found_urls`: 현재 루프에서 수집한 URL 집합 (라운드 간 중복 방지)
- `count`: 이 라운드에서 목표할 기사 수
- 반환: `list[dict]` — 실패 시 `[]` 반환 (예외 흡수)

---

## 2. 탐색 토픽 목록

| 토픽명 | 검색 대상 |
|--------|----------|
| `claude_code_tips` | Claude Code CLI 사용법·워크플로우·슬래시 커맨드·훅·CLAUDE.md 패턴 |
| `prompt_engineering` | 프롬프트 엔지니어링 기법·시스템 프롬프트·CoT·구조화 출력 |
| `ai_coding_tools` | Cursor·GitHub Copilot·Codeium·Aider 실전 사용 팁·설정 가이드 |
| `mcp_tools` | MCP 서버 구축·Claude 도구 통합·에이전트 tool-use 패턴 |
| `dev_productivity` | LLM 기반 코드 리뷰·테스트 생성·문서화·리팩터링 워크플로우 |
| `llm_best_practices` | 컨텍스트 윈도우 관리·RAG·비용 최적화·지연 감소 실전 기법 |
| `agent_patterns` | AI 에이전트 구축·LangChain·CrewAI·AutoGen·멀티에이전트 패턴 |
| `korean_practitioner` | 한국어 AI 활용 아티클 (Velog·브런치·블로그) |
| `community_tips` | HN·Reddit r/ClaudeAI·Twitter 개발자 커뮤니티 실전 팁 스레드 |
| `tutorials_deep_dive` | Claude/OpenAI API 통합·임베딩·벡터 DB 심층 튜토리얼 |

---

## 3. 반환 아티클 필드

| 필드 | 설명 |
|------|------|
| `url` | 기사 URL |
| `title` | 제목 |
| `source` | 출처 (예: "Simon Willison's Weblog") |
| `description` | 2–3문장 요약 |
| `author` | 저자 |
| `published_at` | 발행일 (YYYY-MM-DD) |
| `curator_reason` | Claude가 선택한 이유 |

필드 누락 시 기본값 처리: `agents/news_curation_agent.py` `_tool_find_ai_articles()` 참조.

---

## 4. 다중 토픽 비동기 수집 (청사진)

여러 토픽을 동시에 수집해야 할 때는 `asyncio.to_thread(_research_round, ...)`로 각 토픽을 병렬 실행하고,
결과를 URL 기준으로 dedup해 병합한다.

에이전트 방식(순차)은 `agents/news_curation_agent.py` `run()` 참조.
