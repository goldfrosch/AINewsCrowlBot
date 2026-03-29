---
name: sqlite-ops
description: SQLite database patterns for this project. Use when modifying database.py — schema, upsert, preference updates, score updates, and query patterns.
---

# SQLite 운영 패턴

`database.py`의 구조 및 수정 시 참고할 패턴.

## 스키마 요약

```sql
-- 수집된 기사
articles (
    id, url UNIQUE, title, source, description, author,
    image_url, published_at, platform_score, final_score,
    likes, dislikes,
    status TEXT DEFAULT 'pending',  -- 'pending' | 'posted'
    posted_at, discord_message_id, channel_id,
    keywords TEXT DEFAULT '[]',     -- JSON 배열
    crawled_at
)

-- 소스별 선호도
source_preferences (source PK, multiplier, total_likes, total_dislikes, last_updated)

-- 키워드별 선호도
keyword_preferences (keyword PK, multiplier, total_likes, total_dislikes, last_updated)
```

## 컨텍스트 매니저 패턴

모든 DB 접근은 `_db()` 컨텍스트 매니저를 통해 자동 commit/rollback:
```python
@contextmanager
def _db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # dict-like 접근
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

## 중복 방지 Upsert

```python
def upsert_article(article: dict) -> bool:
    try:
        with _db() as conn:
            conn.execute("INSERT INTO articles (...) VALUES (...)", article)
        return True
    except sqlite3.IntegrityError:
        return False   # URL UNIQUE 제약 위반 → 중복
```

## 선호도 UPDATE — UPSERT 패턴

```python
conn.execute("""
    INSERT INTO source_preferences (source, multiplier, total_likes, total_dislikes)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(source) DO UPDATE SET
        multiplier     = MAX(0.1, MIN(5.0, multiplier + ?)),
        total_likes    = total_likes + ?,
        total_dislikes = total_dislikes + ?,
        last_updated   = datetime('now')
""", (...))
```

범위 클램핑: `MAX(PREFERENCE_MIN, MIN(PREFERENCE_MAX, value))`

## 주요 조회 함수

| 함수 | 용도 |
|------|------|
| `get_pending_articles(limit)` | 미게시 기사, `final_score DESC` 정렬 |
| `get_todays_posted_urls()` | 오늘 이미 게시된 URL (중복 방지) |
| `get_article_by_message_id(id)` | Discord 반응 처리용 기사 조회 |
| `get_all_preferences()` | 소스+키워드 배율 전체 (큐레이터 프롬프트 주입용) |
| `get_stats()` | 통계 (`!stats` 명령어용) |

## 점수 업데이트 순서

```
1. curator.curate() → Article 리스트 반환
2. upsert_article()  → DB에 저장 (platform_score=100)
3. rank_articles()   → final_score 계산
4. update_final_scores() → DB에 final_score 반영
5. Discord 게시 후 mark_as_posted()
6. 👍/👎 → apply_feedback() → update_source/keyword_preference()
```

## DB 경로

```python
DB_PATH = Path("data/bot.db")   # .gitignore에 포함됨
```
`data/` 디렉토리가 없으면 자동 생성 (`DB_PATH.parent.mkdir(exist_ok=True)`).

## 스키마 변경 시

`init_db()`의 `executescript`에 `CREATE TABLE IF NOT EXISTS` 추가.
기존 테이블 컬럼 추가는 별도 `ALTER TABLE` 필요 (IF NOT EXISTS 미지원).
