# AINewsCrawlBot — 파일별 역할 및 주요 인터페이스

## 핵심 모듈

### `main.py` — 진입점

- `DISCORD_BOT_TOKEN` 유무 확인 후 `bot.run()` 호출
- 의존: `bot`, `config`

---

### `config.py` — 전역 상수

| 상수 | 값 | 설명 |
|------|----|------|
| `TIMEZONE` | Asia/Seoul | KST 기준 스케줄 |
| `PREFERENCE_ANALYSIS_HOUR` | 2 | 새벽 2시 선호도 분석 |
| `DAILY_POST_HOUR` | 6 | 오전 6시 브리핑 |
| `ARTICLES_PER_POST` | 5 | 1회 게시 기사 수 |
| `MORE_ARTICLES_MAX` | 10 | !more 최대 요청 수 |
| `AI_KEYWORDS` | 106개 | 영어·한국어 AI 키워드 |
| `SCORE_CAP` | HN:1500, YT:5M, 기본:50K | 정규화 상한선 |

---

### `database.py` — SQLite CRUD

```python
init_db() -> None                                     # 4개 테이블 생성·마이그레이션
upsert_article(article: dict) -> bool                 # URL 기준 중복 방지 저장
get_pending_articles(limit: int) -> list[dict]        # 미게시 기사 (final_score 내림차순)
mark_as_posted(article_id, message_id, channel_id)    # 게시 상태 업데이트
get_article_by_message_id(message_id) -> dict | None  # Discord 메시지 ID로 검색
update_article_reaction(article_id, liked: bool)      # 반응 카운트 증가
update_final_scores(articles: list[dict])             # 랭킹 점수 일괄 저장
update_source_preference(source, liked: bool)         # 소스 배율 ±0.15
update_keyword_preference(keyword, liked: bool)       # 키워드 배율 ±0.05
get_all_preferences() -> dict                         # 전체 선호도 반환
reset_preferences() -> None                           # 선호도 초기화
get_todays_posted_urls() -> list[str]                 # 오늘 게시된 URL (중복 방지)
get_stats() -> dict                                   # 총/게시/대기 수, 반응 합계
```

배율 범위: `PREFERENCE_MIN=0.1` ~ `PREFERENCE_MAX=5.0` (초기값 1.0)

---

### `crawlers/base.py` — 데이터 모델

```python
@dataclass
class Article:
    url: str
    title: str
    source: str
    description: str = ""
    author: str = ""
    image_url: str = ""
    published_at: str = ""
    platform_score: float = 0.0
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict
```

---

### `ranker.py` — 순위 계산 & 피드백

```python
rank_articles(articles: list[dict]) -> list[dict]   # 선호도 기반 정렬 + final_score 갱신
apply_feedback(message_id: str, liked: bool) -> bool # 반응 → DB 선호도 업데이트
extract_keywords(text: str) -> list[str]             # 제목+설명에서 AI 키워드 추출
```

**순위 공식:**
```
final_score = normalize(platform_score) × source_multiplier × avg(keyword_multipliers)
normalize(score, source) = min(score / SCORE_CAP[source], 1.0)
```

---

### `curator.py` — 뉴스 수집 엔진

```python
research(
    count: int,
    exclude_urls: list[str] | None = None,
    preferences: dict | None = None,
) -> list[Article]
```

- 에이전트 사용 가능 → `news_curation_agent.run()` 호출
- 실패 또는 API 오류 → `_fallback_research()` (단순 웹 검색 1회)
- `anthropic.RateLimitError` 시 30초 대기 후 재시도

---

### `pipeline.py` — 큐레이션 파이프라인

```python
run_curation_pipeline(count: int = ARTICLES_PER_POST) -> dict
# 반환: {"articles": list[dict], "raw_count": int, "new_count": int, "error": str | None}
```

내부 순서: 선호도 프로파일 로드 → 오늘 URL 조회 → `curator.research()` → DB 저장 → 랭킹 → 상위 N개 반환

---

### `bot.py` — Discord 봇

**스케줄 작업:**

| 시각 (KST) | 함수 | 동작 |
|-----------|------|------|
| 02:00 | `daily_preference_analysis()` | 선호도 심층 분석 → JSON 저장 |
| 06:00 | `daily_brief()` | `_research_and_post()` 호출 → Discord 게시 |

**명령어:**

| 명령어 | 설명 |
|--------|------|
| `!more [n]` | 추가 기사 요청 (기본 3, 최대 10) |
| `!crawl` | 즉시 브리핑 (ALLOWED_USER_IDS) |
| `!stats` | 봇 통계 및 상위 선호도 표시 |
| `!analyze` | 선호도 분석 즉시 실행 |
| `!reset` | 선호도 초기화 |
| `!tokens` | Claude 토큰 사용량 표시 |
| `!help_ai` | 명령어 도움말 |

**이벤트:**
- `on_ready()`: DB 초기화, 스케줄 등록
- `on_raw_reaction_add()`: 👍/👎 감지 → `ranker.apply_feedback()` 호출

---

## 에이전트 모듈

### `agents/news_curation_agent.py` — Agentic Loop ★

```python
def run(
    target_count: int = 5,
    topics: list[str] | None = None,
    external_preferences: dict | None = None,
) -> list[dict]
```

**3단계 Tool-use:**

| 단계 | 도구 | 동작 |
|------|------|------|
| 1 | `analyze_preferences` | DB 선호도 집계 |
| 2 | `find_ai_articles` | 토픽별 웹 검색 (다중 호출 가능) |
| 3 | `review_articles` | 규칙 필터 + Claude 품질 검토 |

**동적 로드:**
- `.claude/agents/news-curation-agent.md` → YAML 프론트매터에서 토픽·시스템 프롬프트 로드
- `.claude/skills/article-finder.md`, `.claude/skills/article-reviewer.md` → 시스템 프롬프트에 주입

**필터:**
- 스팸 정규식 (광고성 패턴)
- AI 키워드 포함 여부
- URL 중복 제거
- Claude 검토: 광고·중복 탐지, 선호도 반영

---

### `agents/preference_analysis.py` — 선호도 분석

```python
run_preference_analysis(min_articles: int = 3, min_feedback: int = 3) -> dict
save_preference_profile(analysis: dict) -> dict   # → data/preference_profile.json
load_preference_profile() -> dict | None          # 프로파일 읽기
```

**점진적 윈도우 확장:** 7일 → 14일 → 30일 → 전체 (최소 피드백 조건 만족 시 중단)

**티어 분류 (ratio = likes / total):**

| 티어 | 조건 |
|------|------|
| 강선호 | ratio ≥ 0.8 |
| 선호 | ratio ≥ 0.6 |
| 중립 | ratio ≥ 0.4 |
| 비선호 | ratio ≥ 0.2 |
| 강비선호 | ratio < 0.2 |

**큐레이션 힌트:**
- `cold_start`: True if total_feedback < 10 → 다양성 우선
- `confidence`: low (<10) / medium (<30) / high (≥30)
- `boost_sources`, `avoid_sources`, `focus_keywords`, `skip_keywords`

---

### `token_tracker.py` — 토큰 추적

```python
log_token_usage(caller, input_tokens, output_tokens, elapsed)
get_today_token_stats() -> dict
get_average_daily_stats() -> dict
get_window_stats() -> dict   # 5시간 윈도우 비교 (Anthropic 요금제 기준)
```

---

## 의존성 그래프

```
main.py
  └── bot.py
        ├── database.py          ← sqlite3, pathlib
        ├── token_tracker.py     ← sqlite3, pathlib
        ├── ranker.py            ← database, config
        ├── pipeline.py
        │     ├── curator.py
        │     │     ├── agents/news_curation_agent.py  ← anthropic, token_tracker, database
        │     │     └── crawlers/base.py
        │     ├── database.py
        │     ├── ranker.py
        │     └── agents/preference_analysis.py ← database
        └── config.py
```
