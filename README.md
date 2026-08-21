# AINewsCrawlBot

매일 오전 6시(KST)에 AI 뉴스·논문·개발 도구를 Claude로 자동 큐레이션해 Discord에 게시하는 봇.
사용자의 👍/👎 반응을 학습해 다음 날 브리핑의 소스·키워드 가중치를 자동으로 조정한다.

---

## 주요 기능

- **자동 브리핑** — 매일 06:00 KST에 AI 뉴스 상위 3개를 Discord 채널에 게시 (게임 개발+AI 기사 최소 1개 포함)
- **신선도 강제** — 발행일이 7일을 넘긴 기사는 코드에서 폐기. 프롬프트에 오늘 날짜를 주입해 모델이 최신 기사를 찾도록 유도
- **이중 수집 경로** — Claude 웹 검색이 실패하거나 목표 수량에 미달하면 HackerNews·RSS 후보풀로 자동 보충
- **수량 보장** — 목표 대비 4배 오버페치 + 최대 2회 톱업 재검색으로 중복·기한초과 손실을 흡수
- **선호도 학습** — 👍/👎 반응 누적 → 소스·키워드 배율 자동 조정
- **새벽 선호도 분석** — 02:00 KST에 DB 데이터를 심층 분석해 큐레이션 힌트 생성
- **토큰 사용량 추적** — Anthropic API 호출 비용을 일별/5시간 윈도우별로 모니터링

---

## 아키텍처

```
main.py
└─ bot.py                      Discord 봇 (이벤트·스케줄·커맨드)
   └─ pipeline.py              큐레이션 파이프라인 (Discord 무의존)
      ├─ curator.py            Claude 리서치 엔진 (에이전트 래퍼 + 폴백)
      │  └─ claude_search.py   웹 검색 공용 레이어 (stop_reason·pause_turn·재시도)
      ├─ agents/
      │  ├─ news_curation_agent.py  ★ 오버페치 + 톱업 루프 큐레이터
      │  ├─ search_prompt.py        탐색 프롬프트 (오늘 날짜·컷오프 주입)
      │  ├─ agent_spec.py           .claude 문서에서 토픽·스킬 로드
      │  └─ preference_analysis.py  02:00 선호도 심층 분석
      ├─ crawlers/
      │  ├─ feed_pool.py       HN+RSS 병합·티어링·신선도 필터 (2번째 경로)
      │  ├─ hackernews.py      HN Algolia search_by_date
      │  ├─ rss.py             RSS_FEEDS 파싱 + AI 키워드 필터
      │  └─ base.py            Article 데이터클래스
      ├─ recency.py            발행일 파싱 / 신선도 컷오프 / 랭킹 배율
      ├─ ranker.py             기사 점수 계산 & 피드백 처리
      ├─ database.py           SQLite CRUD (articles, keywords, preferences)
      ├─ token_tracker.py      API 토큰 사용량 로깅
      └─ config.py             환경변수 & 전역 상수
```

### 큐레이션 파이프라인

```
[02:00 KST] 선호도 분석 에이전트
    └─ DB 피드백 읽기 → 선호 소스/키워드 프로파일 생성 → data/preference_profile.json 저장

[06:00 KST] 뉴스 브리핑
    1. data/preference_profile.json + data/curation_intent.json 로드
    2. 최근 45일 게시 URL을 제외 목록으로 전달 (중복 재추천 차단)
    3. Claude 웹 검색 — 목표 3개면 12개 요청, 부족하면 토픽을 회전시켜 최대 2회 재검색
    4. 신선도 컷오프 — published_at이 7일을 넘긴 기사 폐기
    5. 목표 미달이면 HN(30점 이상)·RSS 후보풀로 보충
    6. 랭킹 → Discord 게시 (임베드 + 👍/👎 반응 자동 추가)
```

수집 경로가 2개이므로 한쪽이 완전히 죽어도(예: Anthropic 크레딧 소진) 브리핑이 0건이 되지 않는다.

### 신선도 정책

`web_search` 도구에는 날짜/기간 필터 파라미터가 **존재하지 않는다**. 그래서 2단으로 강제한다.

1. **프롬프트에 오늘 날짜와 컷오프 날짜를 명시** — 모델은 오늘이 며칠인지 모르므로
   `"within 48 hours"` 같은 지시만으로는 아무 효과가 없다.
2. **반환된 `published_at`을 코드에서 재검증** — 미래 날짜는 파싱 불가로 취급해 위조를 막는다.
   발행일 미상은 폐기하지 않고 통과시키되 랭킹에서 감점한다(전부 버리면 0건 문제가 재발한다).

### 랭킹 공식

```
final_score = normalize(platform_score) × source_multiplier × avg(keyword_multipliers) × recency_multiplier
```

| 요소 | 설명 | 범위 |
|------|------|------|
| `platform_score` | 모든 프로듀서가 0~100 밴드로 emit → 0~1 정규화 | 0.0 ~ 1.0 |
| `source_multiplier` | 👍 +0.15 / 👎 -0.15 | 0.1 ~ 5.0 |
| `keyword_multiplier` | 기사 키워드 배율의 평균. 👍 +0.05 / 👎 -0.05 | 0.1 ~ 5.0 |
| `recency_multiplier` | 1일 이내 1.6 / 3일 1.35 / 7일 1.15 / 발행일 미상 0.85 / 30일 초과 0.5 | 0.5 ~ 1.6 |

### 주요 튜닝 상수 (`config.py`)

| 상수 | 기본값 | 의미 |
|------|--------|------|
| `RECENCY_MAX_AGE_DAYS` | 7 | 신선도 컷오프 |
| `OVERFETCH_MULTIPLIER` | 4 | 목표 대비 요청 배수 |
| `TOPUP_MAX_ROUNDS` | 2 | 목표 미달 시 추가 검색 횟수 |
| `SEARCH_MAX_TOKENS` | 4096 | 서버사이드 검색 블록이 출력 예산을 잠식하므로 여유 필요 |
| `EXCLUDE_URL_LOOKBACK_DAYS` | 45 | 중복 회피용 게시 이력 조회 기간 |
| `HN_MIN_POINTS` | 30 | HN 후보 최소 점수 |
| `FEED_MAX_PER_SOURCE` | 2 | 후보풀 소스별 상한 (발행량 많은 피드의 독식 방지) |

---

## 설치 및 실행

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 환경변수 설정

프로젝트 루트에 `.env` 파일을 생성한다.

```env
# 필수
DISCORD_BOT_TOKEN=봇_토큰
DISCORD_CHANNEL_ID=채널_ID

# 권장 (없거나 크레딧이 소진되면 HN/RSS 후보풀로 자동 폴백)
ANTHROPIC_API_KEY=클로드_API_키

# 선택
CLAUDE_MODEL=claude-sonnet-4-6
ALLOWED_USER_IDS=123456789,987654321   # 관리자 명령어 허용 유저 ID
```

| 변수 | 필수 여부 | 설명 |
|------|-----------|------|
| `DISCORD_BOT_TOKEN` | 필수 | Discord Developer Portal에서 발급 |
| `DISCORD_CHANNEL_ID` | 필수 | 뉴스를 게시할 채널의 ID |
| `ANTHROPIC_API_KEY` | 권장 | Claude 웹 리서치 활성화. 없으면 HN/RSS 후보풀만으로 동작 |
| `CLAUDE_MODEL` | 선택 | 기본값 `claude-sonnet-4-6` |
| `ALLOWED_USER_IDS` | 선택 | 관리자 명령어를 허용할 유저 ID (쉼표 구분) |

> `config.py`의 `YOUTUBE_API_KEY`, `REDDIT_CLIENT_ID/SECRET`, `THREADS_ACCESS_TOKEN`은
> 현재 어떤 코드도 참조하지 않습니다. 설정하지 않아도 됩니다.

### 3. 실행

```bash
python main.py                          # 봇 실행
python dry_run.py --count 3 --verbose   # Discord 없이 파이프라인만 실행
```

`dry_run.py`는 기본적으로 `data/bot.db`에 씁니다. 실험할 때는 `--db data/tmp.db`로 임시 경로를 쓰세요.

---

## Discord 명령어

| 명령어 | 권한 | 설명 |
|--------|------|------|
| `!more [n]` | 전체 | 추가 기사 n개 요청 (기본 3, 최대 10) |
| `!stats` | 전체 | 봇 통계 및 학습된 선호도 현황 |
| `!tokens` | 전체 | Claude 토큰 사용량 (오늘/5시간 윈도우/전체 평균) |
| `!help_ai` | 전체 | 명령어 목록 |
| `!crawl` | 관리자 | 즉시 브리핑 실행 |
| `!analyze` | 관리자 | 선호도 심층 분석 즉시 실행 |
| `!reset` | 관리자 | 학습된 선호도 초기화 |

각 기사 임베드에 달린 👍/👎 반응을 누르면 Claude의 리서치 방향이 취향에 맞게 조정된다.

> **참고**: 큐레이션 의도를 Discord 명령어로 변경하는 기능(`!intent` 등)은 현재 구현되지 않았습니다. 큐레이션 우선순위를 변경하려면 `data/curation_intent.json` 파일을 직접 수정하세요.

---

## 데이터베이스

`data/bot.db` (SQLite)에 세 테이블이 저장된다.

| 테이블 | 설명 |
|--------|------|
| `articles` | 수집된 기사 (URL 기준 중복 방지, 게시 상태·반응 수 포함) |
| `source_preferences` | 소스별 선호도 배율 (👍/👎 누적) |
| `keyword_preferences` | 키워드별 선호도 배율 (👍/👎 누적) |

---

## 런타임 설정 파일

`data/` 디렉토리에 큐레이션 동작을 제어하는 JSON 파일이 위치한다.

### `data/curation_intent.json` — 사용자 편집 가능

**임시 편집 방향(editorial intent)**을 지정하는 런타임 설정 파일.
코드 수정 없이 크롤링·검색 우선순위를 일시적으로 조정할 수 있다.
예: "이번 주는 게임 UI 관련 기사 위주로" 또는 "한국어 실무 아티클에 집중".

#### 활성화 방법

```bash
cp data/curation_intent.example.json data/curation_intent.json
# curation_intent.json에서 "active": true로 설정
```

`active`를 `false`로 두거나 파일을 삭제하면 즉시 비활성화된다.

#### 스키마

| 필드 | 타입 | 설명 |
|------|------|------|
| `active` | bool | `true`면 의도 적용, `false`면 무시 |
| `summary` | string | 의도 요약 (프롬프트에 삽입됨) |
| `focus_areas` | string[] | 집중할 주제 영역 설명 |
| `boost_topics` | string[] | 우선 탐색할 토픽 키 ([에이전트 토픽 목록](#큐레이션-파이프라인) 참조) |
| `avoid_topics` | string[] | 제외할 토픽 키 |
| `focus_keywords` | string[] | 검색 시 가중할 키워드 |
| `avoid_keywords` | string[] | 검색 시 제외할 키워드 |
| `search_hints` | string | 검색 전략에 대한 자유 텍스트 힌트 |
| `recency_hours` | int | 최근 N시간 이내 기사만 수집 (기본값: 48) |
| `expires_at` | string\|null | 만료 시각 (ISO 8601). null이면 무기한 |

#### 예시

```json
{
  "active": true,
  "summary": "AI를 활용한 게임 개발 사례, 특히 UI/UX/HUD/메뉴 디자인을 우선 수집",
  "focus_areas": [
    "AI-powered game UI/UX development case studies",
    "HUD design",
    "game menu design",
    "AI-assisted UI prototyping"
  ],
  "boost_topics": ["ai_game_ui_sound", "ai_game_workflow"],
  "avoid_topics": [],
  "focus_keywords": ["game UI", "game UX", "HUD", "Unity UI", "Unreal UMG", "Figma AI"],
  "avoid_keywords": ["generic AI news", "model release", "press release"],
  "search_hints": "Prioritize practical tutorials, workflows, and case studies showing AI-assisted game UI/UX creation.",
  "recency_hours": 48,
  "expires_at": null
}
```

> 전체 예시는 [`data/curation_intent.example.json`](data/curation_intent.example.json)을 참조.

### `data/preference_profile.json` — 자동 생성 (수동 편집 금지)

사용자의 👍/👎 반응 데이터를 **자동 분석**하여 생성되는 장기 선호도 프로파일.

- **생성 시점**: 매일 02:00 KST 자동 분석, 또는 `!analyze` 관리자 명령어
- **내용**: 신뢰도 필터링된 소스/키워드 티어, 큐레이션 힌트
- **주의**: 이 파일은 분석 작업이 덮어씁니다. **임시로 큐레이션 방향을 바꾸려면 이 파일이 아닌 `curation_intent.json`을 사용하세요.**

### 두 파일의 관계

```
curation_intent.json   = 일시적·사용자 제어 편집 방향 (수동 편집)
preference_profile.json = 영구적·피드백 학습 선호도 (자동 생성)

        ↓ 병합 ↓
  큐레이션 검색 프롬프트에 함께 주입
  (정적 안전 규칙 > 사용자 의도 > 학습된 선호도 순 우선순위)
```

---

## Discord 봇 설정

Discord Developer Portal에서 봇을 생성하고 다음 Privileged Gateway Intents를 활성화한다.

- **Message Content Intent** — 커맨드 처리
- (선택) **Server Members Intent**

필요한 봇 권한: `Send Messages`, `Embed Links`, `Read Message History`, `Add Reactions`, `View Channels`

자세한 설정 절차는 [`docs/discord-setup.md`](docs/discord-setup.md)를 참고한다.

---

## 에이전트 단독 실행

큐레이션 에이전트를 Discord 봇 없이 CLI에서 직접 실행할 수 있다.

```bash
# 기본 3개 선별
python agents/news_curation_agent.py

# 개수·토픽 지정
python agents/news_curation_agent.py --count 10 --topics models,dev_tools,korean_news
```

---

## 기술 스택

- **Python 3.11+**
- **discord.py 2.x** — Discord 봇 프레임워크
- **Anthropic SDK** — Claude API (`web_search_20260209` tool-use)
- **SQLite** — 기사 저장 및 선호도 관리
- **feedparser / requests** — HackerNews·RSS 후보풀
- **PyYAML** — 에이전트 설정 파일 파싱

---

## 개발

```bash
python -m pytest -q                                # 테스트 (186개)
python -m ruff check --fix . && python -m ruff format .
```

테스트는 `tests/conftest.py`의 autouse fixture로 임시 SQLite에 격리되므로
`data/bot.db`나 `data/token_usage.db`를 건드리지 않습니다.
`tests/test_pipeline.py`는 `pipeline.feed_pool.collect`를 모킹해 네트워크를 타지 않습니다.
