# PROJECT KNOWLEDGE BASE

**Generated:** 2026-09-27
**Commit:** cd3de95
**Branch:** main

## OVERVIEW
매일 06:00 KST Discord에 AI 학습 아티클을 게시하고 👍/👎로 소스·키워드 가중치를 학습하는 봇. Python 3.11 · discord.py · Anthropic SDK(`web_search_20260209`) · SQLite.
파이프라인 단계 설명·튜닝 상수 표·랭킹 공식은 CLAUDE.md에 있다 — 여기서는 반복하지 않는다.

## STRUCTURE
```
AINewsCrowlBot/
├── *.py          # 평면 모듈 — 역할 표는 CLAUDE.md File Map
├── agents/       # Claude 호출 계층 (agents/AGENTS.md)
├── crawlers/     # HN·RSS 2차 수집 경로 + Article (crawlers/AGENTS.md)
├── tests/        # 284 tests, 전부 오프라인 (tests/AGENTS.md)
├── tools/        # loop_runner(N일 연속 운영) + sim_world(외부 경계 3곳 대체)
├── .claude/      # 런타임 입력 — 필라·토픽 정의, 프롬프트 스킬 본문 (계약은 agents/AGENTS.md)
├── data/         # gitignored 런타임 상태 — bot.db, token_usage.db, preference_profile.json, curation_intent.json
├── docs/         # loop-report-2026-09-23.md = 상수 실측 근거 / token-optimization.md = 과거 구조
├── .sisyphus/    # 지난 에이전트 작업 기록(CI/CD 계획) — 현행 명세 아님
└── .github/workflows/deploy.yml   # deploy-* 태그 push → ruff + pytest → SSH 배포
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| 필라·토픽 추가/변경 | `.claude/agents/news-curation-agent.md` 프론트매터 | `config.PILLAR_SETTINGS` 아님 |
| 검색 프롬프트 | `agents/search_prompt.py`, `agents/news_curation_agent.py` | + `.claude/skills/article-finder.md` 본문 |
| 심사 루브릭·한국어 브리핑 | `editorial_review.py`, `.claude/skills/article-reviewer.md` | 한글 필드 없는 KEEP은 폐기 |
| 완화 패스·저수지·다양성 | `pipeline.py` `run_curation_pipeline`, `_select`, `_diversify` | |
| 검색 호출·재시도·치명 오류 판정 | `claude_search.py` | |
| 에이전트 실패 시 폴백 | `curator.py` `research` | Fatal이면 폴백 생략 |
| 발행일 파싱·신선도 창 | `recency.py` | |
| 본문 검증·근중복·사설 IP 차단 | `article_quality.py`, `article_fetch.py` | `_public_host`가 비공개 주소 거부 |
| 점수·피드백 | `ranker.py`, `database.canonical_keyword` | |
| 스케줄·명령어·임베드 | `bot.py` | `!crawl`·`!analyze`·`!reset`은 `@is_admin_or_allowed()` |
| 비용 계측 | `token_tracker.py` | `!tokens`, dry_run 리포트 |
| 사용자 큐레이션 의도 | `data/curation_intent.json` ← `curation_intent.py` | 템플릿 `data/curation_intent.example.json` |
| 배포 | `.github/workflows/deploy.yml`, `docs/deployment.md` | |

## CODE MAP
Refs = 정의 파일 밖에서 import·속성 접근·mock 문자열로 참조하는 파일 수 (운영/테스트). LSP 데몬 불통으로 Python AST로 측정.

| Symbol | Type | Location | Refs | Role |
|--------|------|----------|------|------|
| `config` | module | config.py | 26 (16/10) | 전역 상수, import 시 `.env` 스냅샷 |
| `database` | module | database.py | 16 (8/8) | SQLite CRUD, `init_db()`가 마이그레이션까지 |
| `Article` | class | crawlers/base.py | 14 (7/7) | 모든 수집 경로의 후보 레코드 |
| `run_curation_pipeline` | func | pipeline.py | 7 (3/4) | bot·dry_run·loop_runner 공통 진입점 |
| `is_stale` | func | recency.py | 6 (5/1) | 신선도 하드 컷 |
| `max_age_from_intent` | func | recency.py | 5 (4/1) | intent `recency_hours` → 일수 |
| `upsert_article` / `mark_as_posted` | func | database.py | 8 / 5 | 저장 / 게시 완료 표시 |
| `FatalSearchError` | class | claude_search.py | 4 (3/1) | 서킷 브레이커 (`RuntimeError` 하위) |
| `search_articles` / `SearchOutcome` | func / class | claude_search.py | 4 / 3 | 검색 1회 → articles·stop_reason·error·fatal |
| `canonicalize_url` / `request_url` | func | article_fetch.py | 4 / 1 | 저장·중복 키 / HTTP 요청 URL |
| `extract_json_array` | func | text_utils.py | 3 (3/0) | 유일한 JSON 배열 파서 |
| `log_api_usage` | func | token_tracker.py | 3 (2/1) | Claude 호출 비용 기록 |
| `days_ago` | func | tests/conftest.py | 10 (0/10) | 테스트 상대 날짜 |

## CONVENTIONS
- 설정은 import 시점 스냅샷이고 소비자는 `from config import X`로 가져간다. 값을 바꾸려면 소비 모듈 속성을 patch한다 — `config.X` 변경은 무효.
- `data/` 경로는 전부 **CWD 기준 상대경로**(`database`, `token_tracker`, `curation_intent`, `agents/preference_analysis`). 항상 repo 루트에서 실행한다. `.claude`만 `__file__` 기준.
- 시간은 KST: Python은 `recency.today()`, SQLite는 `datetime('now', '+9 hours')`, 컨테이너는 `TZ=Asia/Seoul`.
- 로그는 `logging` 없이 `print("[Component] ...")` (ruff `T201` ignore). 주석·로그·Discord 문구는 한국어, 모델 프롬프트는 영어.
- 복구 가능한 API 오류는 raise하지 않고 `SearchOutcome.error`로 돌려준다. 복구 불가(크레딧·인증)만 `FatalSearchError`로 전파한다.
- 게이트를 추가하면 `stages` 리포트에 통과 수·탈락 사유를 넣는다 — 0건 원인 추적과 dry_run 출력이 여기에 기댄다.
- 튜닝 상수 옆에 "실측(YYYY-MM-DD)" 수치를 남긴다. 값을 바꾸면 같은 형식으로 근거를 갱신한다.
- 상한·다양성 때문에 목표를 못 채우면 밀어둔 후보로 채운다(`_diversify`, `_cap_per_source`). 0건 방지가 다양성보다 우선.
- 키워드 정규화는 `database.canonical_keyword()`, JSON 추출은 `text_utils.extract_json_array()` 하나뿐이다. 복제 구현 금지.

## ANTI-PATTERNS (THIS PROJECT)
- HTTP 요청에 `canonicalize_url()` 사용 — 후행 슬래시가 빠져 301이 무한 반복된다. 요청은 `request_url()`.
- 활성 `curation_intent`의 `recency_hours` 창을 완화 루프에서 넓히기.
- 발행일 없는 기사 폐기, 모델에게 날짜 추측시키기. 미래 날짜(+1일 초과)는 위조로 보고 "미상" 처리한다 — 신선으로 취급하지 않는다.
- `except RuntimeError`/`except Exception`을 `except FatalSearchError`보다 앞에 두기 — 서킷 브레이커가 삼켜져 한 실행에서 같은 실패가 18번 반복된다.
- 측정 없이 톱업 라운드·검색 예산·심사 배치 늘리기 — 톱업 1라운드 = 필라 3회 호출 ≈ $1.
- 배포가 서버의 `.env`·`data/`를 덮어쓰게 만들기, 02:00·06:00 KST 작업 시간대 배포, 키·서버 IP를 코드나 문서에 쓰기.
- `.claude/project/*.md`, `docs/token-optimization.md`, `.sisyphus/`를 현행 명세로 믿기 — 코드와 어긋난 곳이 있다.

## COMMANDS
```bash
pip install -r requirements.txt
python -m pytest -q                                          # CI: pytest tests/ -v
python -m ruff check --fix . && python -m ruff format .      # CI는 ruff check . 만 (포맷 미검사)
python tools/loop_runner.py --simulate --days 7 --count 6    # 오프라인, 비용 0
python dry_run.py --count 6 --verbose --db data/tmp.db       # 라이브 과금
python tools/loop_runner.py --days 5 --db data/loop.db       # 라이브 과금 (하루치 ≈ $1.14)
git tag deploy-$(date +%Y%m%d-%H%M)                          # 배포 트리거, 이어서 git push origin <그 태그>
```

## NOTES
- 실험이 운영 `data/`로 새는 경로: `dry_run --db`는 bot DB만 바꾼다 — 토큰 사용량은 여전히 `data/token_usage.db`에 쌓인다. `--mark-posted`는 지정한 DB를 수정한다. `loop_runner --out` 기본값은 `data/loop_report.json`.
- CLAUDE.md의 "기본 모델 `claude-opus-5`"는 낡은 정보다. `SEARCH_MODEL`·`REVIEW_MODEL` 기본값은 `claude-sonnet-4-6`이고, 환경변수 `CLAUDE_MODEL`을 주면 둘 다 그 값이 된다.
- 코드가 안 읽는 config 상수: `CLAUDE_MODEL`, `PILLAR_SETTINGS`, `MAX_PER_SOURCE`, YouTube·Reddit·Threads·LinkedIn 키와 쿼리. 바꿔도 효과 없다.
- `curation_intent.json`의 `expires_at`은 로드만 되고 만료 판정 코드가 없다. 끄려면 `"active": false`.
- `WEB_SEARCH_ALLOWED_CALLERS=["direct"]`를 빼면 programmatic tool calling 미지원 모델에서 400. `CLAUDE_EFFORT`의 `xhigh`/`max`는 thinking 비활성과 함께 쓸 수 없다.
- Claude Code는 `.claude/settings.json` Stop 훅으로 ruff를 자동 실행한다. 다른 에이전트는 직접 돌려야 한다.
- `tools/sim_world.py`는 검색·HTTP·편집심사 3곳만 대체하고 나머지 파이프라인은 진짜로 돈다. RNG 시드 고정, 미래 날짜가 위조로 처리되므로 세계를 과거에 앵커한다.
- 임베드 텍스트는 `bot._make_embed`에서 필드별로 자른다(제목 250·설명 400자) — Discord 한도(제목 256·필드 1024) 안쪽 유지.
