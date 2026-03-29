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

### 1. 계획

- 노출할 기능 목록 작성 (예: `curate_news`, `get_stats`, `add_feedback`)
- 도구 이름 컨벤션: `botname_action` (예: `ainews_curate`, `ainews_stats`)

### 2. 구현 (Python FastMCP 예시)

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("AINewsCrawlBot")

@mcp.tool()
def curate_news(count: int = 5) -> list[dict]:
    """Curate top AI news articles using Claude research."""
    import curator, database as db
    articles = curator.curate(target_count=count)
    return [a.to_dict() for a in articles]

@mcp.tool()
def get_stats() -> dict:
    """Get bot statistics and preference data."""
    import database as db
    return db.get_stats()

if __name__ == "__main__":
    mcp.run()
```

### 3. 도구 설계 원칙

- **명확한 이름**: 동작을 동사로 시작 (`curate_`, `get_`, `update_`)
- **집중된 반환값**: 필요한 필드만 반환
- **actionable 오류 메시지**: 다음 단계를 안내

### 4. 평가

- 10개 실제 시나리오로 검증
- READ-ONLY 작업만 테스트에 사용

## 이 프로젝트에서 MCP 추가 시 노출할 도구 후보

| 도구                 | 설명                     |
| -------------------- | ------------------------ |
| `ainews_curate`      | Claude로 AI 뉴스 수집    |
| `ainews_stats`       | 기사 통계 및 선호도 조회 |
| `ainews_preferences` | 소스/키워드 배율 조회    |
| `ainews_pending`     | 미게시 기사 목록         |

## 참고 문서

- Python SDK: `https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/README.md`
- MCP 스펙: `https://modelcontextprotocol.io/specification/draft.md`
