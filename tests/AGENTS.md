# tests/

**Why this file:** score 20 — 21 files, own `conftest.py`, package boundary, 381 symbols. 284 tests collected, all offline.

## OVERVIEW
pytest 스위트 — 외부 경계(Anthropic·HTTP·DNS·HN/RSS)만 가짜로 바꾸고 파이프라인 로직은 진짜로 돌린다.

## WHERE TO LOOK
| 대상 | 테스트 파일 |
|------|-------------|
| 완화 패스·intent 잠금·다양성 상한 | test_pipeline_relaxation.py |
| 저장·랭킹·저수지 선택 | test_pipeline.py, test_pipeline_quality.py |
| 서킷 브레이커 (크레딧·인증) | test_fatal_api_error.py |
| 필라 팬아웃·톱업 라운드 | test_agent_topup.py, test_news_curation_agent.py |
| 잘림 재시도·pause_turn·텍스트 블록 합치기 | test_claude_search.py |
| curator 폴백·JSON 배열 추출 | test_curator.py |
| 리다이렉트·후행 슬래시 보존 | test_article_fetch.py |
| 본문 검증·근중복 | test_article_quality.py |
| 배치 심사·임계값·한국어 필드 | test_editorial_review.py |
| HN/RSS 후보풀 | test_feed_pool.py |
| 스키마·마이그레이션·키워드 정규화 | test_database.py |
| 랭킹·피드백 배율 | test_ranker.py |
| 날짜 파싱·신선도 창 | test_recency.py |
| 비용 계측 | test_token_tracker.py |
| intent 로더 | test_curation_intent.py |
| 임베드 포맷 | test_bot_embed.py |
| N일 연속 운영 (`tools/sim_world`) | test_zero_day_simulation.py |

## CONVENTIONS
### 픽스처
- `conftest.tmp_db`는 **autouse**: `database`·`token_tracker` DB를 `tmp_path`로 돌리고 끝나면 `data/` 기본값으로 되돌린다. 과거 테스트가 운영 DB에 행을 남겨서 생긴 규칙 — 우회 금지.
- `tmp_token_db`(토큰 DB 경로가 필요할 때), `sample_articles`(dict 3건), `days_ago(n)`(`from tests.conftest import days_ago`).
- 격리되지 **않는** 파일: `data/preference_profile.json`, `data/curation_intent.json`, `agents.preference_analysis.DB_PATH`. 파이프라인 테스트는 `pipeline.load_curation_intent`·`pipeline.load_preference_profile`를 patch한다.
- 파일 로컬 autouse: test_pipeline.py `no_network_feeds`·`pass_quality_gate`, test_pipeline_relaxation.py `no_network_feeds`·`stub_gates`, test_fatal_api_error.py `_isolate`. 게이트가 기본 통과라서 진짜 검증·심사를 보려면 이 픽스처를 덮어써야 한다.

### 가짜와 patch
- 자주 쓰는 대상: `pipeline.curator.research`, `pipeline.feed_pool.collect`, `ranker.db.*`, `editorial_review.token_tracker.log_token_usage`. 이름으로 import된 상수는 `mocker.patch.object(module, "NAME", value)`.
- HTTP: test_article_fetch.py `_Response`/`_Client`. `article_fetch._public_host`는 실제 DNS(`getaddrinfo`)를 부르므로 patch.
- Anthropic 스트림: test_claude_search.py — `__enter__`를 가진 `MagicMock` 스트림.
- 에이전트 검색: test_agent_topup.py `_run` — `claude_search.search_articles` side_effect 큐를 `threading.Lock`으로 감싼다(필라 호출이 스레드 풀에서 동시 실행).
- 세계 전체: test_zero_day_simulation.py — `tools.loop_runner.run_days` + `SimWorld`.
- 키 없이 curator 경로를 돌리려면 `agents.news_curation_agent.run`이나 `anthropic.Anthropic`을 mock한다. `curator._is_mocked()`가 이를 보고 키 검사를 건너뛴다.

### 날짜·실행
- 날짜는 `days_ago(n)` 또는 recency 함수의 `ref=`. 시계 고정 픽스처가 없어서 절대 날짜는 신선도 창이 지나면 깨진다.
- 부분 실행: `python -m pytest -q tests/test_agent_topup.py::TestPillarFanout`, `python -m pytest -q -k "fatal or topup"`.

## ANTI-PATTERNS
- 실제 네트워크·API 호출, `time.sleep`, 벽시계 비교 — 현재 0건, 유지할 것.
- 프롬프트 문구를 고정하는 assert. 파싱된 필드·카운트·호출 수만 검증한다.
- 락 없이 공유하는 side_effect 큐 — 스레드 풀 경쟁으로 순서가 흔들린다.
