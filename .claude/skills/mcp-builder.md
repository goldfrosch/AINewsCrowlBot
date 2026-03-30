---
name: mcp-builder
description: Guide for creating MCP (Model Context Protocol) servers. Use when adding MCP integration to this bot — exposing bot functions (curate, stats, feedback) as MCP tools for external LLM clients.
source: https://skills.sh/anthropics/skills/mcp-builder
---

# MCP 서버 개발 가이드

이 봇에 MCP 인터페이스를 추가할 때 참고. 외부 LLM 클라이언트가 봇 기능을
직접 호출할 수 있도록 도구를 노출한다.

## 권장 스택

- **언어**: TypeScript (SDK 지원 최고) 또는 Python (FastMCP)
- **Transport**: 원격 서버 → Streamable HTTP / 로컬 서버 → stdio

## 4단계 개발 프로세스

1. **계획** — 노출할 기능 목록 작성. 도구 이름 컨벤션: `botname_action`
2. **구현** — FastMCP `@mcp.tool()` 데코레이터로 함수 노출. 이 봇 함수(`curator.curate`, `db.get_stats` 등)를 직접 임포트해 래핑
3. **도구 설계 원칙** — 동작을 동사로 시작 (`curate_`, `get_`, `update_`), 필요한 필드만 반환, actionable 오류 메시지
4. **평가** — 10개 실제 시나리오로 검증. READ-ONLY 작업만 테스트에 사용

## 이 프로젝트에서 노출할 도구 후보

| 도구                 | 설명                     |
| -------------------- | ------------------------ |
| `ainews_curate`      | Claude로 AI 뉴스 수집    |
| `ainews_stats`       | 기사 통계 및 선호도 조회 |
| `ainews_preferences` | 소스/키워드 배율 조회    |
| `ainews_pending`     | 미게시 기사 목록         |

## 참고 문서

- Python SDK: `https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/README.md`
- MCP 스펙: `https://modelcontextprotocol.io/specification/draft.md`
