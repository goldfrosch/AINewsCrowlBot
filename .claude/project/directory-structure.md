# AINewsCrawlBot — 디렉토리 구조

```
AINewsCrowlBot/
├── main.py                          # 진입점: 봇 시작 & 토큰 검증
├── bot.py                           # Discord 봇 본체 (이벤트·스케줄·명령어)
├── config.py                        # 환경변수·전역 상수·AI 키워드 목록
├── database.py                      # SQLite CRUD (기사·선호도·통계)
├── ranker.py                        # 기사 점수 계산 & 피드백 처리
├── curator.py                       # Claude 기반 뉴스 수집 엔진
├── pipeline.py                      # Discord 독립 큐레이션 파이프라인
├── token_tracker.py                 # Claude 토큰 사용량 추적
├── dry_run.py                       # CLI 테스트 모드 (Discord 없이 실행)
│
├── agents/
│   ├── __init__.py
│   ├── news_curation_agent.py       # ★ Tool-use agentic loop (3단계 자율 수행)
│   └── preference_analysis.py      # 선호도 심층 분석 & 프로파일 생성
│
├── crawlers/
│   ├── __init__.py
│   └── base.py                      # Article 데이터클래스 정의
│
├── tests/
│   ├── conftest.py                  # pytest 픽스처 (모의 DB·클라이언트)
│   ├── test_database.py
│   ├── test_ranker.py
│   ├── test_curator.py
│   ├── test_pipeline.py
│   └── __init__.py
│
├── data/                            # 런타임 데이터 (git 무시)
│   ├── bot.db                       # 기사·선호도 SQLite DB
│   ├── token_usage.db               # 토큰 추적 전용 DB
│   └── preference_profile.json      # 선호도 분석 결과 (새벽 2시 생성)
│
├── .claude/
│   ├── agents/
│   │   └── news-curation-agent.md  # 에이전트 스펙 (YAML 프론트매터·토픽)
│   ├── skills/                      # Claude에게 주입할 스킬 문서
│   │   ├── article-finder.md
│   │   ├── article-reviewer.md
│   │   └── ... (10개 스킬 문서)
│   └── project/                     # ← 현재 분석 문서 위치
│
├── requirements.txt                 # 의존성 패키지 목록
├── pyproject.toml                   # Ruff 린트 설정 (Python 3.11+, 120자)
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── CLAUDE.md                        # 프로젝트 지침 (Claude Code용)
```

## DB 스키마

### `data/bot.db`

| 테이블 | 주요 컬럼 | 역할 |
|--------|-----------|------|
| `articles` | url(UK), title, source, platform_score, final_score, likes, dislikes, posted, message_id | 수집된 기사 저장 |
| `keywords` | keyword(UK), multiplier, total_likes, total_dislikes | 키워드별 배율 |
| `article_keywords` | article_id, keyword_id | 기사-키워드 다대다 |
| `source_preferences` | source(UK), multiplier, total_likes, total_dislikes | 소스별 배율 |

### `data/token_usage.db`

| 테이블 | 주요 컬럼 | 역할 |
|--------|-----------|------|
| `token_usage` | called_at, caller, input_tokens, output_tokens, elapsed_seconds | Claude 호출 비용 추적 |

## 환경 변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `DISCORD_BOT_TOKEN` | ✅ | Discord 봇 토큰 |
| `DISCORD_CHANNEL_ID` | ✅ | 브리핑 게시 채널 ID |
| `ANTHROPIC_API_KEY` | 권장 | 없으면 폴백 크롤러 사용 |
| `ALLOWED_USER_IDS` | 선택 | 관리자 명령어 허용 ID 목록 |
| `CLAUDE_MODEL` | 선택 | 기본값: `claude-sonnet-4-6` |
| `YOUTUBE_API_KEY` | 선택 | 폴백용 (현재 미사용) |
| `REDDIT_CLIENT_ID/SECRET` | 선택 | 폴백용 (현재 미사용) |
