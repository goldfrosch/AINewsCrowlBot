# CLAUDE.md

## Project Overview

**AINewsCrawlBot** — 매일 02:00 KST에 선호도를 분석하고 06:00 KST에 AI 아티클을 Discord에 자동 게시하는 봇.
사용자의 👍/👎 반응을 학습해 다음 날 브리핑의 소스·키워드 가중치를 조정한다.

## Tech Stack

- Python 3.11+ / pip / SQLite (`data/bot.db`)
- discord.py 2.x · Anthropic SDK · `web_search_20260209` (기본 모델 `claude-opus-5`)
- feedparser / requests — HN·RSS 후보풀

## File Map

```
main.py          진입점
bot.py           Discord 봇 (이벤트·스케줄·커맨드)
pipeline.py      큐레이션 파이프라인 (Discord 무의존)
curator.py       Claude 리서치 엔진 (에이전트 래퍼 + 폴백)
claude_search.py Claude 웹 검색 공용 레이어 (stop_reason·pause_turn·재시도)
recency.py       발행일 파싱 / 신선도 컷오프 / 랭킹 배율
text_utils.py    JSON 배열 추출 (단일 구현)
ranker.py        기사 점수 계산 & 피드백 처리
database.py      SQLite CRUD
config.py        환경변수 & 전역 상수
curation_intent.py  런타임 큐레이션 의도 로더
crawlers/
  base.py        Article 데이터클래스
  hackernews.py  HN Algolia search_by_date 후보 수집
  rss.py         RSS_FEEDS 파싱 + AI 키워드 필터
  feed_pool.py   HN+RSS 병합·티어링·신선도 필터 (2번째 수집 경로)
agents/
  agent_spec.py           .claude 문서에서 토픽·스킬 로드
  search_prompt.py        탐색 프롬프트 구성 (날짜 주입)
  news_curation_agent.py  ★ 오버페치 + 톱업 루프 큐레이션 에이전트
  preference_analysis.py  02:00 선호도 심층 분석
```

## Dev Commands

```bash
pip install -r requirements.txt
python main.py                          # 봇 실행
python dry_run.py --count 3 --verbose   # Discord 없이 파이프라인 실행
python -m pytest -q                     # 테스트
python -m ruff check --fix . && python -m ruff format .
```

`dry_run.py`는 기본적으로 `data/bot.db`에 씁니다. 실험할 때는 `--db`로 임시 경로를 지정하세요.

## Environment Variables

필수: `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`
필수: `ANTHROPIC_API_KEY` (웹 검색과 별도 품질 심사에 사용)
선택: `CLAUDE_MODEL`, `ALLOWED_USER_IDS`

## 수집 파이프라인

```
1. curator.research()   Claude 웹 검색 — 목표 N개면 N×4개 요청, 부족하면 최대 2회 톱업 재검색
2. 신선도 컷오프         published_at이 RECENCY_MAX_AGE_DAYS(7일)를 넘기면 폐기
                        발행일 미상은 통과시키되 랭킹에서 감점
3. crawlers.feed_pool   1·2번 결과가 목표 미달일 때 HN(30점 이상)·RSS로 보충
```

검색 경로는 2개라 웹 검색만 실패하면 feed로 보충한다. 단, 공통 품질 심사에 필요한
Anthropic API를 사용할 수 없으면 검수되지 않은 기사를 게시하지 않는다.

## 신선도 정책 (중요)

`web_search` 도구에는 날짜/기간 필터 파라미터가 **존재하지 않는다**. 그래서 2단으로 강제한다.

1. 프롬프트에 **오늘 날짜와 컷오프 날짜를 명시**한다 (`recency.prompt_lines()`).
   모델은 오늘이 며칠인지 모르므로 `"within 48 hours"` 같은 지시만으로는 무의미하다.
2. 반환된 `published_at`을 코드에서 다시 검증한다 (`recency.is_stale()`).
   미래 날짜는 파싱 불가로 취급해 위조를 막는다.

## Ranking Formula

`final_score = normalize(platform_score) × source_multiplier × avg(keyword_multipliers) × recency_multiplier`

- `platform_score`는 모든 프로듀서가 0~100 밴드로 emit한다 (소스별 상한 없음)
- `recency_multiplier`: 1일 이내 1.6 / 3일 1.35 / 7일 1.15 / 발행일 미상 0.85 / 30일 초과 0.5
- 👍 → source +0.15 / keyword +0.05 · 👎 → source -0.15 / keyword -0.05 · 범위 0.1~5.0
- 키워드는 `canonical_keyword()`로 정규화해 저장한다 (`"ai agent"` → `"ai_agent"`)

## 주요 튜닝 상수 (config.py)

| 상수 | 기본값 | 의미 |
|------|--------|------|
| `RECENCY_MAX_AGE_DAYS` | 7 | 신선도 컷오프 |
| `OVERFETCH_MULTIPLIER` | 4 | 목표 대비 요청 배수 |
| `TOPUP_MAX_ROUNDS` | 2 | 목표 미달 시 추가 검색 횟수 |
| `SEARCH_MAX_TOKENS` | 16000 | thinking·검색 블록·JSON이 한 예산을 나눠 쓰므로 여유 필요 |
| `REVIEW_MAX_TOKENS` | 16000 | 편집 심사 출력 예산. 잘리면 2배로 1회 재시도 |
| `CLAUDE_EFFORT` | `medium` | thinking 분량 제어 (`low`~`max`) |
| `EXCLUDE_URL_LOOKBACK_DAYS` | 45 | 중복 회피용 게시 이력 조회 기간 |
| `FEED_MAX_PER_SOURCE` | 2 | 후보풀 소스별 상한 |

## Skills

이 프로젝트 작업 시 아래 스킬 문서를 참조한다.

| 파일                                                                                     | 언제 사용                                  |
| ---------------------------------------------------------------------------------------- | ------------------------------------------ |
| [`.claude/skills/claude-api.md`](.claude/skills/claude-api.md)                           | Anthropic SDK 호출·모델·스트리밍·에러 처리 |
| [`.claude/skills/discord-bot.md`](.claude/skills/discord-bot.md)                         | `bot.py` 이벤트·임베드·커맨드·반응 처리    |
| [`.claude/skills/sqlite-ops.md`](.claude/skills/sqlite-ops.md)                           | `database.py` 스키마·쿼리·선호도 업데이트  |
| [`.claude/skills/mcp-builder.md`](.claude/skills/mcp-builder.md)                         | MCP 인터페이스 추가 시                     |
| [`.claude/skills/preference-analyzer.md`](.claude/skills/preference-analyzer.md)         | DB 선호도 읽기·쓰기·프롬프트 주입 패턴     |
| [`.claude/skills/preference-intelligence.md`](.claude/skills/preference-intelligence.md) | 선호도 심층 분석·티어링·큐레이션 힌트 생성 |
| [`.claude/skills/article-finder.md`](.claude/skills/article-finder.md)                   | 웹 검색 기반 AI 기사 탐색 패턴             |
| [`.claude/skills/article-reviewer.md`](.claude/skills/article-reviewer.md)               | 기사 품질 검토·필터링·랭킹 파이프라인      |
| [`.claude/skills/hackernews.md`](.claude/skills/hackernews.md)                           | HN API로 고득점 AI 스레드 수집 패턴        |
| [`.claude/skills/ai-social-media-content.md`](.claude/skills/ai-social-media-content.md) | inference.sh로 소셜 미디어 콘텐츠 생성     |
