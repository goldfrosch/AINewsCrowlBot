# CLAUDE.md

## Project Overview

**AINewsCrawlBot** — 매일 02:00 KST에 선호도를 분석하고 06:00 KST에 AI 아티클을 Discord에 자동 게시하는 봇.
사용자의 👍/👎 반응을 학습해 다음 날 브리핑의 소스·키워드 가중치를 조정한다.

큐레이션은 **3개 필라**로 나뉘며 필라마다 검색 호출·신선도 정책·목표 배분이 따로 간다.

| 필라 | 내용 | 신선도 | 배분 |
|------|------|--------|------|
| `ai_practice` | AI 개발 실무 최신 흐름 — 에이전틱 코딩, 컨텍스트 엔지니어링, 평가 하네스, 구독할 피드·스레드 | 14일 | 5 |
| `ai_game` | AI로 게임 에셋·UI·사운드 제작 + **AI로 만든 게임 사례·포스트모템** | 45일 | 3 |
| `graphics_3d` | **무료·저비용 AI 3D 생성**(오픈소스 image-to-3D, 무료 티어)과 그래픽스 기초 | 120일 | 2 |

필라 정의의 단일 출처는 [`.claude/agents/news-curation-agent.md`](.claude/agents/news-curation-agent.md) 프론트매터다.

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
article_fetch.py canonicalize_url(저장 키) / request_url(요청 URL) / fetch_page(사유 반환)
article_quality.py 페이지 병렬 검증 · 근중복/주제중복 판정 · 신뢰 도메인
editorial_review.py 배치 심사 · 완화 단계별 임계값 · 한국어 브리핑 생성
crawlers/
  base.py        Article 데이터클래스 (pillar 필드 포함)
  hackernews.py  HN Algolia search_by_date 후보 수집
  rss.py         RSS_FEEDS 파싱 + AI 키워드 필터
  feed_pool.py   HN+RSS 병합·티어링·신선도 필터 (2번째 수집 경로)
agents/
  agent_spec.py           .claude 문서에서 필라·토픽·스킬 로드
  search_prompt.py        필라별 탐색 프롬프트 구성 (날짜 주입)
  news_curation_agent.py  ★ 필라 병렬 검색 + 톱업 루프 큐레이션 에이전트
  preference_analysis.py  02:00 선호도 심층 분석
tools/
  loop_runner.py 연속 운영 시뮬레이션 (0건 발생률·비용 측정)
```

## Dev Commands

```bash
pip install -r requirements.txt
python main.py                          # 봇 실행
python dry_run.py --count 6 --verbose   # Discord 없이 파이프라인 실행
python dry_run.py --mark-posted --db data/tmp.db   # 연속일 조건 재현
python tools/loop_runner.py --days 5 --db data/loop.db   # 0건 발생률 측정
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
0. 저수지 확인        전날 잉여(pending)가 목표를 채우면 검색 없이 종료 — 비용 $0
1. 완화 패스 1~3      목표에 미달할 때마다 창과 품질컷을 한 단계씩 넓혀 재검색
                      창 14일/컷 62·70 → 30일/58·64 → 90일/55·60
   └ curator.research()  필라 3개를 동시 호출(각자 검색 예산 8회)
     └ 톱업 라운드      후보가 목표×2.5에 못 미칠 때만 1회 추가 (비용 방어)
2. article_quality    페이지를 병렬로 받아 본문·언어·발행일 검증 (사유별 계측)
3. editorial_review   8건씩 배치로 병렬 심사 — 한 배치가 잘려도 나머지는 생존
4. crawlers.feed_pool 그래도 미달이면 HN(30점 이상)·RSS 1차 소스로 보충
5. _select            분류별 신선도 창 + 소스/주제 다양성 상한으로 최종 선정
```

**서킷 브레이커**: 크레딧 소진·인증 실패 같은 복구 불가 오류는 `claude_search.FatalSearchError`로
즉시 전파해 남은 패스를 중단한다. 이게 없으면 실행 1회에 같은 실패를 18번 반복하고
"게시 0건"만 남아 원인이 묻힌다 (실측). Discord에도 조치 방법을 명시해 알린다.

## 신선도 정책 (중요)

`web_search` 도구에는 날짜/기간 필터 파라미터가 **존재하지 않는다**. 그래서 2단으로 강제한다.

1. 프롬프트에 **오늘 날짜와 컷오프 날짜를 명시**한다 (`recency.prompt_lines()`).
   모델은 오늘이 며칠인지 모르므로 `"within 48 hours"` 같은 지시만으로는 무의미하다.
2. 반환된 `published_at`을 코드에서 다시 검증한다 (`recency.is_stale()`).
   미래 날짜는 파싱 불가로 취급해 위조를 막는다.

창은 **필라별**로 다르고(14/45/120일), 저장 후에는 `content_type` 키워드로 복원한다
(`CONTENT_TYPE_MAX_AGE_DAYS`). 활성 `curation_intent`가 창을 좁혔다면 완화 루프도 넓히지 않는다 —
"최근 24시간" 요청에 90일 전 기사를 끼워 넣는 것은 0건보다 나쁘다.

## URL 정규화 (함정)

`canonicalize_url()`은 **저장·중복 판정용 키**이고 후행 슬래시를 뗀다.
HTTP 요청에는 반드시 `request_url()`을 쓴다 — 슬래시를 유지하지 않으면
Django·WordPress·Ghost 계열이 301을 돌려주고, 홉마다 재정규화되며 **무한 리다이렉트**에 빠진다.
실측에서 simonwillison.net을 비롯한 최고 품질 실무자 블로그가 이 버그로 전멸했다.

## Ranking Formula

`final_score = normalize(platform_score) × source_multiplier × avg(keyword_multipliers) × recency_multiplier`

- `platform_score`는 모든 프로듀서가 0~100 밴드로 emit한다 (소스별 상한 없음)
- `recency_multiplier`: 1일 이내 1.6 / 3일 1.35 / 7일 1.15 / 발행일 미상 0.85 / 30일 초과 0.5
- 👍 → source +0.15 / keyword +0.05 · 👎 → source -0.15 / keyword -0.05 · 범위 0.1~5.0
- 키워드는 `canonical_keyword()`로 정규화해 저장한다 (`"ai agent"` → `"ai_agent"`)

## 주요 튜닝 상수 (config.py)

| 상수 | 기본값 | 의미 |
|------|--------|------|
| `ARTICLES_PER_POST` | 6 | 하루 최대 게시 수 (학습용 피드라 폭이 중요) |
| `MIN_ACCEPTABLE_ARTICLES` | 2 | 이 아래면 경고를 남긴다 |
| `RECENCY_MAX_AGE_DAYS` | 14 | 기본 신선도 컷오프 (필라별로 재정의) |
| `RECENCY_RELAXATION_DAYS` | (14,30,90) | 목표 미달 시 단계적으로 넓히는 창 |
| `OVERFETCH_MULTIPLIER` | 4 | 목표 대비 요청 배수 (min 12 / max 36) |
| `CANDIDATES_PER_PUBLISHED` | 2.5 | 이 배수를 넘기면 톱업 라운드를 생략한다 (비용 방어) |
| `TOPUP_MAX_ROUNDS` | 1 | 라운드 1회 = 필라 수만큼 검색 호출 |
| `WEB_SEARCH_MAX_USES` | 8 | 필라당 검색 예산 |
| `REVIEW_BATCH_SIZE` | 8 | 심사 배치 크기. 잘려도 그 배치만 잃는다 |
| `VERIFY_FETCH_WORKERS` | 8 | 본문 검증 병렬도 |
| `MAX_PER_SOURCE_IN_POST` | 2 | 브리핑 1회 소스 상한 |
| `MAX_PER_TOPIC_IN_POST` | 2 | 브리핑 1회 동일 주제 상한 |
| `SEARCH_MAX_TOKENS` | 16000 | thinking·검색 블록·JSON이 한 예산을 나눠 쓰므로 여유 필요 |
| `REVIEW_MAX_TOKENS` | 16000 | 편집 심사 출력 예산. 잘리면 2배로 1회 재시도 |
| `CLAUDE_EFFORT` | `medium` | thinking 분량 제어 (`low`~`max`) |
| `EXCLUDE_URL_LOOKBACK_DAYS` | 45 | 중복 회피용 게시 이력 조회 기간 |
| `FEED_MAX_PER_SOURCE` | 2 | 후보풀 소스별 상한 |

실측 수율(2026-09-23 라이브): 검색 18 → 본문검증 9 → 심사통과 7 → 게시 6, 실행당 $1.14.

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
