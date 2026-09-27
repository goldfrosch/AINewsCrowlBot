# agents/

**Why this file:** score 17 — package boundary, 58 symbols, 9 symbols used by 13 outside files. Distinct domain: its configuration lives in `.claude/` markdown, not `config.py`.

## OVERVIEW
Claude 호출 계층 — `.claude` 문서 로더, 필라별 검색 프롬프트, 필라 병렬 검색 + 톱업 에이전트, 02:00 선호도 분석.

## WHERE TO LOOK
| 작업 | 위치 |
|------|------|
| 필라·토픽·스킬 로드 | agent_spec.py — `PILLARS`, `TOPIC_DESC`, `DEFAULT_TOPICS`, `SKILL_FINDER`, `SKILL_REVIEWER`, `pillar_*()` |
| 검색 user 프롬프트 | search_prompt.py `build_search_prompt` — 날짜 줄, 필라 가이드, 토픽 설명, intent, 선호/비선호, 제외 URL(`EXCLUDE_URL_PROMPT_LIMIT`), 라운드 지시, JSON 스키마 |
| 검색 system 프롬프트 | news_curation_agent.py `_SYSTEM_RESEARCH` + article-finder 스킬 (`cache_control: ephemeral`) |
| 수량 보장 루프 | news_curation_agent.py `run()` |
| 선호도 프로파일 | preference_analysis.py — 피드백 창을 7→14→30일→전체로 넓혀 가며 `data/preference_profile.json` 저장 |

## CONVENTIONS
### `.claude` 문서 계약 (agent_spec.py)
- `.claude/agents/news-curation-agent.md` 프론트매터에서 `topics`(키 → 영문 설명), `default_topics`, `pillars`(`label`·`weight`·`max_age_days`·`topics`)만 읽는다.
- 같은 파일의 `name`·`description`·`model`과 본문은 Claude Code 서브에이전트 정의다. Python은 무시한다.
- 필라 토픽은 `topics:`에 설명이 있어야 쓰인다. `pillar_topics()`가 정의 없는 키를 조용히 버린다.
- 프론트매터가 없거나 YAML이 깨지면 **import 시점**에 예외 → `pipeline`·`editorial_review`·`bot`까지 import 실패.
- `.claude/skills/article-finder.md`·`article-reviewer.md` 본문(프론트매터 제외)이 운영 프롬프트에 그대로 들어간다. 파일이 없으면 빈 문자열로 조용히 빠진다.
- 모듈 로드 시 1회만 읽는다 → 문서 수정은 봇 재시작 후 반영.

### run() 계약 (news_curation_agent.py)
- 요청량 = `clamp(target × OVERFETCH_MULTIPLIER, OVERFETCH_MIN, OVERFETCH_MAX)`를 필라 `weight` 비율로 배분(필라당 최소 2).
- 라운드마다 필라 수만큼 동시 호출. 톱업은 후보가 `min(요청량, max(target, round(target × CANDIDATES_PER_PUBLISHED)))` 미만일 때만 돈다.
- 톱업 라운드는 `topics_for_round()`로 토픽 순서를 돌린다. 같은 프롬프트는 같은 결과를 돌려준다.
- 필라 창: 필라 `max_age_days` → 활성 intent가 더 좁으면 intent → 상위 완화 루프 값이 더 넓으면 그 값.
- 이미 저장된 URL(`db.get_all_article_urls()`)과 기한초과를 빼고 최신순 반환. target보다 많이 돌려주는 게 정상이다 — 잉여는 pipeline이 pending 저수지로 쌓는다.
- 한 필라라도 `fatal`이면 그 라운드 결과를 흡수한 뒤 `FatalSearchError`를 던진다. 여기 이름은 재노출이고 정의는 `claude_search`.
- token_tracker caller는 `agent_find_{pillar}` / `agent_find_{pillar}_topup{n}`. 비용 리포트(`!tokens`, dry_run)가 caller별로 묶는다.

### 기타
- 각 모듈은 `python agents/<module>.py`로 단독 실행된다: repo 루트를 `sys.path.insert` 한 뒤 로컬 import → ruff `E402` per-file ignore.
- `preference_analysis`는 자체 `DB_PATH`와 `set_db_path()`를 쓴다. `database.set_db_path()`로는 바뀌지 않는다.
- 02:00 프로파일의 `curation_hints`는 `_apply_external_preferences`에서 DB 요약의 선호/비선호 소스·키워드를 덮어쓴다.

## ANTI-PATTERNS
- 필라를 한 호출로 합치기 — 모델이 쉬운 토픽 2~3개만 검색하고 끝낸다(실측, config.py 주석).
- system 프롬프트의 "URL 날조 금지 / JSON 배열만 출력" 규칙 완화 — 하류가 모든 URL을 fetch하고 JSON만 파싱한다.
- 단독 실행을 오프라인 점검으로 착각 — `python agents/news_curation_agent.py`는 라이브 과금이고 CWD의 `data/bot.db`에 `init_db()`를 돈다.
