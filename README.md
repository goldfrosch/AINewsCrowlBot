# AINewsCrawlBot

매일 새벽 3시(KST)에 AI 뉴스·논문·개발 도구를 Claude로 자동 큐레이션해 Discord에 게시하는 봇.
사용자의 👍/👎 반응을 학습해 다음 날 브리핑의 소스·키워드 가중치를 자동으로 조정한다.

---

## 주요 기능

- **자동 브리핑** — 매일 03:00 KST에 AI 뉴스 상위 5개를 Discord 채널에 게시
- **Claude 웹 리서치** — `web_search` tool-use로 최신 기사를 실시간 탐색
- **선호도 학습** — 👍/👎 반응 누적 → 소스·키워드 배율 자동 조정
- **새벽 선호도 분석** — 02:00 KST에 DB 데이터를 심층 분석해 큐레이션 힌트 생성
- **에이전트 큐레이션** — 3단계 agentic loop(선호도 분석 → 기사 탐색 → 품질 검토)
- **토큰 사용량 추적** — Anthropic API 호출 비용을 일별/윈도우별로 모니터링

---

## 아키텍처

```
main.py
└─ bot.py                      Discord 봇 (이벤트·스케줄·커맨드)
   ├─ agents/
   │  ├─ news_curation_agent.py  ★ tool-use 기반 agentic loop (메인 큐레이터)
   │  └─ preference_analysis.py  새벽 2시 선호도 심층 분석
   ├─ curator.py                 Ralph Loop 다중 라운드 리서치 엔진
   ├─ ranker.py                  기사 점수 계산 & 피드백 처리
   ├─ database.py                SQLite CRUD (articles, preferences)
   ├─ token_tracker.py           API 토큰 사용량 로깅
   └─ config.py                  환경변수 & 전역 상수
```

### 큐레이션 파이프라인

```
[02:00 KST] 선호도 분석 에이전트
    └─ DB 피드백 읽기 → 선호 소스/키워드 프로파일 생성 → data/preference_profile.json 저장

[03:00 KST] 뉴스 큐레이션 에이전트
    ├─ Step 1: analyze_preferences  — 저장된 선호도 프로파일 로드
    ├─ Step 2: find_ai_articles     — 토픽별 웹 검색 (다중 호출)
    └─ Step 3: review_articles      — 스팸 필터 + Claude 심층 검토 → 상위 N개 선별
         └─ Discord 게시 (임베드 + 👍/👎 반응 자동 추가)
```

### 랭킹 공식

```
final_score = normalize(platform_score) × source_multiplier × avg(keyword_multipliers)
```

| 요소 | 설명 | 범위 |
|------|------|------|
| `platform_score` | 소스별 원점수(조회수, 업보트 등)를 0~1 정규화 | 0.0 ~ 1.0 |
| `source_multiplier` | 👍 +0.15 / 👎 -0.15 | 0.1 ~ 5.0 |
| `keyword_multiplier` | 제목·설명 내 AI 키워드 배율의 평균. 👍 +0.05 / 👎 -0.05 | 0.1 ~ 5.0 |

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

# 권장 (없으면 크롤러 폴백)
ANTHROPIC_API_KEY=클로드_API_키

# 선택
CLAUDE_MODEL=claude-sonnet-4-6
ALLOWED_USER_IDS=123456789,987654321   # 관리자 명령어 허용 유저 ID

# 크롤러 폴백용 (ANTHROPIC_API_KEY 없을 때)
YOUTUBE_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
THREADS_ACCESS_TOKEN=
```

| 변수 | 필수 여부 | 설명 |
|------|-----------|------|
| `DISCORD_BOT_TOKEN` | 필수 | Discord Developer Portal에서 발급 |
| `DISCORD_CHANNEL_ID` | 필수 | 뉴스를 게시할 채널의 ID |
| `ANTHROPIC_API_KEY` | 권장 | Claude 웹 리서치 활성화. 없으면 RSS/Reddit 크롤러로 폴백 |
| `CLAUDE_MODEL` | 선택 | 기본값 `claude-sonnet-4-6` |
| `ALLOWED_USER_IDS` | 선택 | 관리자 명령어를 허용할 유저 ID (쉼표 구분) |

### 3. 실행

```bash
python main.py
```

---

## Discord 명령어

| 명령어 | 권한 | 설명 |
|--------|------|------|
| `!more [n]` | 전체 | 추가 기사 n개 요청 (기본 5, 최대 10) |
| `!stats` | 전체 | 봇 통계 및 학습된 선호도 현황 |
| `!tokens` | 전체 | Claude 토큰 사용량 (오늘/5시간 윈도우/전체 평균) |
| `!help_ai` | 전체 | 명령어 목록 |
| `!crawl` | 관리자 | 즉시 브리핑 실행 |
| `!reset` | 관리자 | 학습된 선호도 초기화 |

각 기사 임베드에 달린 👍/👎 반응을 누르면 Claude의 리서치 방향이 취향에 맞게 조정된다.

---

## 큐레이션 토픽

에이전트가 탐색하는 기본 토픽 목록:

| 토픽 | 설명 |
|------|------|
| `models` | 최신 LLM 모델 출시·벤치마크 |
| `company_news` | AI 기업 주요 뉴스 |
| `arxiv_papers` | 주목할 만한 논문 |
| `dev_tools` | AI 개발 도구·SDK |
| `korean_news` | 국내 AI 뉴스 (한국어) |

> 토픽 목록과 설명은 `.claude/agents/news-curation-agent.md`에서 관리한다.

---

## 데이터베이스

`data/bot.db` (SQLite)에 세 테이블이 저장된다.

| 테이블 | 설명 |
|--------|------|
| `articles` | 수집된 기사 (URL 기준 중복 방지, 게시 상태·반응 수 포함) |
| `source_preferences` | 소스별 선호도 배율 (👍/👎 누적) |
| `keyword_preferences` | 키워드별 선호도 배율 (👍/👎 누적) |

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
# 기본 5개 선별
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
- **feedparser / BeautifulSoup4** — RSS 크롤러 폴백
