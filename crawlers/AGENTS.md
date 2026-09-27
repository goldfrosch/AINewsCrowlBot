# crawlers/

**Why this file:** score 13 + distinct domain — the deterministic (non-LLM) second collection path, and home of `Article`, the most-referenced type (14 files).

## OVERVIEW
HN Algolia + RSS 후보풀 — Claude 경로가 목표에 못 미칠 때만 `pipeline._topup_from_feeds`가 부족분×4를 요청하고, 결과는 웹 검색 후보와 같은 본문검증·편집심사를 거친다.

## WHERE TO LOOK
| 작업 | 위치 |
|------|------|
| 후보 레코드 필드 | base.py `Article` |
| HN 쿼리·점수 컷 | hackernews.py + config `HN_SEARCH_QUERIES`, `HN_MIN_POINTS`, `HN_MAX_RESULTS` |
| 피드 추가·키워드 필터 면제 | config `RSS_FEEDS`, `RSS_NO_FILTER_SOURCES`, `RSS_MAX_PER_FEED` |
| 관련도·티어·소스 상한 | feed_pool.py `relevance`, `_sort_key`, `_cap_per_source` + config `FEED_*` |
| 테스트 | tests/test_feed_pool.py |

## CONVENTIONS
- 튜닝 값은 전부 `config.py`에 둔다. 이 디렉터리에 상수를 새로 만들지 않는다.

### Article 계약 (base.py)
- 필수 `url`·`title`·`source`, 나머지는 기본값. `keywords`는 list.
- `to_dict()`는 description 500자·author 100자로 자르고 `pillar`를 뺀다. 저장 후 창은 `content_type` 키워드로 복원된다(`config.CONTENT_TYPE_MAX_AGE_DAYS`).
- `platform_score`는 0~100 밴드. HN·RSS는 `_UNIFORM_SCORE = 100.0`(실제 HN 점수는 description에), Claude 경로도 100, 편집 심사를 통과하면 `quality_score`로 덮인다.

### 수집 규칙
- HN: Algolia `search_by_date` + `numericFilters`(`created_at_i`, `points`). 링크 없는 스토리는 HN 토론 URL로 대체. 쿼리별 실패는 로그 후 건너뛴다.
- RSS: `published_parsed`/`updated_parsed`가 없는 항목은 버린다 — 발행일 미상을 살리는 Claude 경로와 반대. `RSS_NO_FILTER_SOURCES`가 아니면 `AI_KEYWORDS` 매치 필수.
- feed_pool 선별: 제외·중복 URL → 신선도 → 관련도 `FEED_MIN_RELEVANCE` 이상 → **티어 → 관련도 → 최신** 정렬 → 소스당 `FEED_MAX_PER_SOURCE`, 모자라면 상한을 풀어 채운다.
- 관련도 = 제목 매치 × `FEED_TITLE_WEIGHT` + min(설명 매치, `FEED_DESC_MATCH_CAP`). 게임 개발 키워드 매치는 × `FEED_GAME_DEV_WEIGHT`.
- `feed_pool._producers()`는 hackernews·rss를 함수 안에서 import하고, rss는 feedparser를 함수 안에서 import한다. requests·feedparser 없이도 `feed_pool` import가 돼야 하므로 최상단으로 올리지 않는다.
- producer 예외는 각자 잡혀 로그만 남는다. 전부 실패해도 빈 리스트를 돌려줄 뿐 raise하지 않는다.

## ANTI-PATTERNS
- 날짜만으로 정렬 — 하루 60편씩 쏟아지는 arXiv 같은 피드가 상위를 독식했다(실측). 티어가 관련도보다 앞선 이유.
- 키워드 개수만으로 관련도 계산 — 긴 학술 초록이 항상 이긴다(HN 후보 16개가 전부 밀림).
- 점수 필터 없는 토론 피드 추가 — Reddit r/gamedev RSS 잡담이 게시 후보로 올라와서 뺐다.
- `FEED_HTTP_TIMEOUT`이 RSS에도 걸린다고 가정 — HN(`requests`)에만 적용된다. `feedparser.parse(url)`는 자체 fetch라 타임아웃이 없어서 응답 없는 피드가 실행을 붙잡을 수 있다(추정).
