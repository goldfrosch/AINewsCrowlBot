# AINewsCrawlBot

매일 오전 6시(KST)에 실용적인 AI 개발 실무·게임 에셋·저비용 3D 자료를 Claude로 자동 큐레이션해
Discord에 게시하는 봇. 사용자의 👍/👎 반응을 학습해 다음 날 브리핑의 소스·키워드 가중치를 자동으로 조정한다.

---

## 큐레이션 3개 필라

| 필라 | 무엇을 찾는가 | 신선도 | 배분 |
|------|--------------|--------|------|
| **AI 개발 실무** | 에이전틱 코딩 워크플로우, 컨텍스트·프롬프트 엔지니어링, 평가 하네스, 멀티 에이전트 — 그리고 **계속 구독할 만한 뉴스레터·블로그·토론 스레드** | 14일 | 5 |
| **AI × 게임 개발** | AI로 3D·텍스처·UI·사운드·애니메이션 만들기 + **AI로 실제 만든 게임의 데브로그·포스트모템** | 45일 | 3 |
| **그래픽스 · 저비용 3D** | **무료·오픈소스 AI 3D 생성**(Hunyuan3D·TRELLIS·TripoSR, 무료 티어 비교, Blender 애드온)과 프로그래머가 독학할 그래픽스 기초 | 120일 | 2 |

필라마다 검색 호출이 분리되어 각자 검색 예산을 온전히 쓴다. 한 프롬프트에 합치면
모델이 가장 검색하기 쉬운 주제로 결과가 쏠린다(실측: 8건 중 5건 편중).

---

## 주요 기능

- **자동 브리핑** — 매일 06:00 KST에 품질 기준을 통과한 자료를 최대 6개 Discord 채널에 게시
- **필라 병렬 검색** — 주제군마다 호출·신선도 정책·목표 배분을 분리해 커버리지 확보
- **본문·편집 검증** — 페이지를 병렬로 받아 언어·발행일·본문을 확인하고, 별도 Claude 심사를 8건 배치로 돌려 실용성과 품질을 평가
- **0건 방지 완화 루프** — 목표 미달 시 신선도 창과 품질컷을 단계적으로 넓혀(14→30→90일, 62/70→55/60) 재검색
- **저수지** — 전날 잉여분을 pending으로 쌓아 검색이 부진한 날 비용 $0로 브리핑을 낸다
- **이중 수집 경로** — Claude 웹 검색이 실패하거나 목표 수량에 미달하면 HackerNews·RSS 후보풀로 자동 보충
- **서킷 브레이커** — 크레딧 소진·인증 실패는 즉시 중단하고 조치 방법을 Discord에 알린다 (조용한 0건 방지)
- **다양성 제약** — 브리핑 1회당 같은 소스 2건·같은 주제 2건까지
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
    0. 저수지 확인 — 전날 잉여(pending)가 목표를 채우면 검색 없이 종료 (비용 $0)
    1. data/preference_profile.json + data/curation_intent.json 로드
    2. 최근 45일 게시 URL을 제외 목록으로 전달 (중복 재추천 차단)
    3. 완화 패스 1~3 — 목표에 미달할 때마다 창·품질컷을 한 단계씩 넓혀 재검색
         └ 필라 3개를 동시 호출 (각자 검색 예산 8회)
            └ 후보가 목표×2.5에 못 미칠 때만 톱업 1라운드 추가
    4. 페이지를 병렬로 받아 본문·언어·발행일 검증 + 근중복 제거 (탈락 사유별 계측)
    5. 8건씩 배치로 병렬 편집 심사 — 한 배치가 잘려도 나머지는 살아남는다
    6. 목표 미달이면 HN(30점 이상)·RSS 1차 소스도 같은 검증을 거쳐 보충
    7. 분류별 신선도 창 + 소스/주제 다양성 상한 → 최대 6개 Discord 게시
```

검색 경로는 Claude 웹 검색과 HN/RSS 두 개다. 웹 검색만 실패하면 feed로 보충하지만,
공통 품질 심사에 필요한 Anthropic API를 사용할 수 없으면 검수되지 않은 기사를 게시하지 않는다.

**서킷 브레이커** — 크레딧 소진·인증 실패는 완화해도 결과가 같다. `FatalSearchError`로
즉시 전파해 남은 패스를 중단하고 Discord에 조치 방법을 알린다. 이 장치가 없으면 실행 1회에
같은 실패를 18번 반복하고 "게시 0건"만 남아 원인이 로그에 묻힌다(실측).

### 신선도 정책

`web_search` 도구에는 날짜/기간 필터 파라미터가 **존재하지 않는다**. 그래서 2단으로 강제한다.

1. **프롬프트에 오늘 날짜와 컷오프 날짜를 명시** — 모델은 오늘이 며칠인지 모르므로
   `"within 48 hours"` 같은 지시만으로는 아무 효과가 없다.
2. **반환된 `published_at`을 코드에서 재검증** — 미래 날짜는 파싱 불가로 취급해 위조를 막는다.
   발행일 미상은 폐기하지 않고 통과시키되 랭킹에서 감점한다(전부 버리면 0건 문제가 재발한다).

창은 **필라별로 다르다**(14/45/120일). 실무 아티클은 최신성이 곧 가치지만, 셰이더 기초나
리토폴로지 워크플로우 같은 학습자료는 오래돼도 유효하다. 저장 후에는 `content_type` 키워드로
필라 정책을 복원한다. 단, 활성 `curation_intent`가 창을 좁혔다면 완화 루프도 넓히지 않는다 —
"최근 24시간" 요청에 90일 전 기사를 끼워 넣는 것은 0건보다 나쁜 배신이다.

### URL 정규화 (함정)

`canonicalize_url()`은 **저장·중복 판정용 키**라서 후행 슬래시를 뗀다.
HTTP 요청에는 반드시 `request_url()`을 쓴다. 슬래시를 유지하지 않으면 Django·WordPress·Ghost
계열이 301을 돌려주고, 리다이렉트 홉마다 다시 정규화되며 **무한 루프**에 빠진다.
실측에서 simonwillison.net을 포함한 최고 품질 실무자 블로그가 이 버그로 전부 fetch 실패했다.

### 랭킹 공식

```
final_score = quality × bounded_preference × recency_nudge × game_client_nudge
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
| `ARTICLES_PER_POST` | 6 | 하루 최대 게시 수 |
| `MIN_ACCEPTABLE_ARTICLES` | 2 | 이 아래면 경고를 남긴다 |
| `RECENCY_MAX_AGE_DAYS` | 14 | 기본 신선도 컷오프 (필라별로 재정의) |
| `RECENCY_RELAXATION_DAYS` | (14, 30, 90) | 목표 미달 시 단계적으로 넓히는 창 |
| `OVERFETCH_MULTIPLIER` | 4 | 목표 대비 요청 배수 (최소 12 / 최대 36) |
| `CANDIDATES_PER_PUBLISHED` | 2.5 | 이 배수를 넘기면 톱업 라운드를 생략 (비용 방어) |
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
| `HN_MIN_POINTS` | 30 | HN 후보 최소 점수 |
| `FEED_MAX_PER_SOURCE` | 2 | 후보풀 소스별 상한 (발행량 많은 피드의 독식 방지) |

실측 수율(2026-09-23 라이브, 목표 6개): 검색 18 → 본문검증 9 → 심사통과 7 → 게시 6, 실행당 $1.14.

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

# 필수 (웹 검색과 별도 품질 심사에 사용)
ANTHROPIC_API_KEY=클로드_API_키

# 선택
CLAUDE_MODEL=claude-opus-5
ALLOWED_USER_IDS=123456789,987654321   # 관리자 명령어 허용 유저 ID
```

| 변수 | 필수 여부 | 설명 |
|------|-----------|------|
| `DISCORD_BOT_TOKEN` | 필수 | Discord Developer Portal에서 발급 |
| `DISCORD_CHANNEL_ID` | 필수 | 뉴스를 게시할 채널의 ID |
| `ANTHROPIC_API_KEY` | 필수 | Claude 웹 리서치와 별도 품질 심사 활성화 |
| `CLAUDE_MODEL` | 선택 | 기본값 `claude-opus-5` |
| `ALLOWED_USER_IDS` | 선택 | 관리자 명령어를 허용할 유저 ID (쉼표 구분) |

> `config.py`의 `YOUTUBE_API_KEY`, `REDDIT_CLIENT_ID/SECRET`, `THREADS_ACCESS_TOKEN`은
> 현재 어떤 코드도 참조하지 않습니다. 설정하지 않아도 됩니다.

### 3. 실행

```bash
python main.py                          # 봇 실행
python dry_run.py --count 6 --verbose   # Discord 없이 파이프라인만 실행
python tools/loop_runner.py --days 5 --db data/loop.db   # 연속 운영 시뮬레이션 (0건 발생률 측정)
```

`dry_run.py`는 기본적으로 `data/bot.db`에 씁니다. 실험할 때는 `--db data/tmp.db`로 임시 경로를 쓰세요.

---

## Discord 명령어

| 명령어 | 권한 | 설명 |
|--------|------|------|
| `!more [n]` | 전체 | 추가 기사 n개 요청 (기본 6, 최대 6) |
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
