---
name: sqlite-ops
description: SQLite database patterns for this project. Use when modifying database.py — schema, upsert, preference updates, score updates, and query patterns.
---

# SQLite 운영 패턴

`database.py`의 구조 및 수정 시 참고할 패턴.

## 스키마 요약

```sql
articles (
    id, url UNIQUE, title, source, description, author,
    image_url, published_at, platform_score, final_score,
    likes, dislikes,
    status TEXT DEFAULT 'pending',  -- 'pending' | 'posted'
    posted_at, discord_message_id, channel_id,
    keywords TEXT DEFAULT '[]',     -- JSON 배열
    crawled_at
)

source_preferences (source PK, multiplier, total_likes, total_dislikes, last_updated)

keyword_preferences (keyword PK, multiplier, total_likes, total_dislikes, last_updated)
```

## 컨텍스트 매니저

모든 DB 접근은 `_db()` 컨텍스트 매니저를 통해 자동 commit/rollback. `database.py` 참조.

## 중복 방지 Upsert

`upsert_article(article)` — URL UNIQUE 제약 위반 시 `False` 반환, 성공 시 `True`.

## 선호도 UPDATE — UPSERT + 클램핑 패턴

`ON CONFLICT(source) DO UPDATE`로 upsert하고 `MAX(0.1, MIN(5.0, multiplier + delta))`로 범위 클램핑.
구현: `database.py` `update_source_preference()`, `update_keyword_preference()` 참조.

## 주요 조회 함수

| 함수 | 용도 |
|------|------|
| `get_pending_articles(limit)` | 미게시 기사, `final_score DESC` 정렬 |
| `get_todays_posted_urls()` | 오늘 이미 게시된 URL (중복 방지) |
| `get_article_by_message_id(id)` | Discord 반응 처리용 기사 조회 |
| `get_all_preferences()` | 소스+키워드 배율 전체 (큐레이터 프롬프트 주입용) |
| `get_stats()` | 통계 (`!stats` 명령어용) |

## 점수 업데이트 순서

1. `curator.curate()` → 기사 리스트 반환
2. `upsert_article()` → DB 저장 (`platform_score=100`)
3. `rank_articles()` → `final_score` 계산
4. `update_final_scores()` → DB에 반영
5. Discord 게시 후 `mark_as_posted()`
6. 👍/👎 → `ranker.apply_feedback()` → `update_source/keyword_preference()`

## DB 경로

`DB_PATH = Path("data/bot.db")` — `.gitignore`에 포함됨. `data/` 없으면 자동 생성.

## 스키마 변경 시

`init_db()`의 `executescript`에 `CREATE TABLE IF NOT EXISTS` 추가.
기존 테이블 컬럼 추가는 별도 `ALTER TABLE` 필요 (IF NOT EXISTS 미지원).
