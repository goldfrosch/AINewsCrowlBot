# CLAUDE.md

## Project Overview

**AINewsCrawlBot** — 매일 새벽 3시(KST)에 AI 뉴스·논문·영상을 크롤링해 Discord에 자동 게시하는 봇.
사용자의 👍/👎 반응을 학습해 다음 날 브리핑의 소스·키워드 가중치를 조정한다.

## Tech Stack

- Python 3.11+ / pip / SQLite (`data/bot.db`)
- discord.py 2.x · Anthropic SDK · Claude Opus 4.6 + `web_search_20260209`

## File Map

```
main.py          진입점
bot.py           Discord 봇 (이벤트·스케줄·커맨드)
curator.py       ★ Claude 웹 리서치 엔진 (Ralph Loop)
ranker.py        기사 점수 계산 & 피드백 처리
database.py      SQLite CRUD
config.py        환경변수 & 전역 상수
crawlers/        폴백 크롤러 (ANTHROPIC_API_KEY 없을 때)
agents/
  news_curation_agent.py  ★ tool-use 기반 독립 큐레이션 에이전트
```

## Dev Commands

```bash
pip install -r requirements.txt
python main.py
```

## Environment Variables

필수: `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`
권장: `ANTHROPIC_API_KEY` (없으면 크롤러 폴백)
선택: `YOUTUBE_API_KEY`, `REDDIT_CLIENT_ID/SECRET`, `THREADS_ACCESS_TOKEN`

## Ranking Formula

`final_score = normalize(platform_score) × source_multiplier × avg(keyword_multipliers)`

👍 → source +0.15 / keyword +0.05 · 👎 → source -0.15 / keyword -0.05 · 범위 0.1~5.0

## Skills

이 프로젝트 작업 시 아래 스킬 문서를 참조한다.

| 파일 | 언제 사용 |
|------|-----------|
| [`.claude/skills/claude-api.md`](.claude/skills/claude-api.md) | Anthropic SDK 호출·모델·스트리밍·에러 처리 |
| [`.claude/skills/ralph-loop.md`](.claude/skills/ralph-loop.md) | `curator.py` 다중 라운드 리서치 패턴 수정 |
| [`.claude/skills/discord-bot.md`](.claude/skills/discord-bot.md) | `bot.py` 이벤트·임베드·커맨드·반응 처리 |
| [`.claude/skills/sqlite-ops.md`](.claude/skills/sqlite-ops.md) | `database.py` 스키마·쿼리·선호도 업데이트 |
| [`.claude/skills/mcp-builder.md`](.claude/skills/mcp-builder.md) | MCP 인터페이스 추가 시 |
| [`.claude/skills/preference-analyzer.md`](.claude/skills/preference-analyzer.md) | DB 선호도 읽기·쓰기·프롬프트 주입 패턴 |
| [`.claude/skills/article-finder.md`](.claude/skills/article-finder.md) | 웹 검색 기반 AI 기사 탐색 패턴 |
| [`.claude/skills/article-reviewer.md`](.claude/skills/article-reviewer.md) | 기사 품질 검토·필터링·랭킹 파이프라인 |
