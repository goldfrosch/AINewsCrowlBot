"""
SQLite 데이터베이스 관리
- articles: 크롤링된 기사 (URL 기준 중복 방지)
- keywords: 키워드 레지스트리 + 👍/👎 기반 선호도 배율
- article_keywords: 기사-키워드 다대다 연결 테이블
- source_preferences: 소스별 👍/👎 기반 선호도 배율
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("data/bot.db")


def set_db_path(path: Path | str) -> None:
    """DB_PATH를 override합니다 (테스트·dry-run용)."""
    global DB_PATH
    DB_PATH = Path(path)


PREFERENCE_MIN = 0.1
PREFERENCE_MAX = 5.0
SOURCE_DELTA = 0.15  # 👍/👎 시 소스 배율 변화량
KEYWORD_DELTA = 0.05  # 👍/👎 시 키워드 배율 변화량


@contextmanager
def _db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS articles (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                url                TEXT    UNIQUE NOT NULL,
                title              TEXT    NOT NULL,
                source             TEXT    NOT NULL,
                description        TEXT    DEFAULT '',
                author             TEXT    DEFAULT '',
                image_url          TEXT    DEFAULT '',
                published_at       TEXT    DEFAULT '',
                platform_score     REAL    DEFAULT 0,
                final_score        REAL    DEFAULT 0,
                likes              INTEGER DEFAULT 0,
                dislikes           INTEGER DEFAULT 0,
                status             TEXT    DEFAULT 'pending',
                posted_at          TEXT,
                discord_message_id TEXT,
                channel_id         TEXT,
                crawled_at         TEXT    DEFAULT (datetime('now', '+9 hours'))
            );

            CREATE TABLE IF NOT EXISTS keywords (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword        TEXT    UNIQUE NOT NULL,
                multiplier     REAL    DEFAULT 1.0,
                total_likes    INTEGER DEFAULT 0,
                total_dislikes INTEGER DEFAULT 0,
                last_updated   TEXT    DEFAULT (datetime('now', '+9 hours'))
            );

            CREATE TABLE IF NOT EXISTS article_keywords (
                article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                keyword_id INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
                PRIMARY KEY (article_id, keyword_id)
            );

            CREATE TABLE IF NOT EXISTS source_preferences (
                source         TEXT PRIMARY KEY,
                multiplier     REAL    DEFAULT 1.0,
                total_likes    INTEGER DEFAULT 0,
                total_dislikes INTEGER DEFAULT 0,
                last_updated   TEXT    DEFAULT (datetime('now', '+9 hours'))
            );
        """)
        _migrate(conn)


def _migrate(conn) -> None:
    """기존 DB 구조(keyword_preferences, articles.keywords)를 새 스키마로 마이그레이션."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()}

    # keyword_preferences → keywords 테이블로 이전
    if "keyword_preferences" in tables:
        conn.execute("""
            INSERT OR IGNORE INTO keywords (keyword, multiplier, total_likes, total_dislikes, last_updated)
            SELECT keyword, multiplier, total_likes, total_dislikes, last_updated
            FROM keyword_preferences
        """)
        conn.execute("DROP TABLE keyword_preferences")

    # articles.keywords JSON → article_keywords 중간 테이블로 이전
    if "keywords" in cols:
        rows = conn.execute(
            "SELECT id, keywords FROM articles WHERE keywords IS NOT NULL AND keywords != '[]'"
        ).fetchall()
        for row in rows:
            article_id, kw_json = row[0], row[1]
            try:
                kws = json.loads(kw_json or "[]")
            except (json.JSONDecodeError, TypeError):
                kws = []
            for kw in kws:
                if not kw:
                    continue
                conn.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (kw,))
                kw_row = conn.execute("SELECT id FROM keywords WHERE keyword = ?", (kw,)).fetchone()
                if kw_row:
                    conn.execute(
                        "INSERT OR IGNORE INTO article_keywords (article_id, keyword_id) VALUES (?, ?)",
                        (article_id, kw_row[0]),
                    )
        conn.execute("ALTER TABLE articles DROP COLUMN keywords")


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────


def _rows_with_keywords(rows) -> list[dict]:
    """SELECT 결과에서 _kw_list 컬럼을 keywords 리스트로 변환."""
    result = []
    for r in rows:
        d = dict(r)
        kw_csv = d.pop("_kw_list", "") or ""
        d["keywords"] = [kw for kw in kw_csv.split(",") if kw]
        result.append(d)
    return result


def _link_keywords(conn, article_id: int, keywords) -> None:
    """keywords(list 또는 JSON 문자열)를 keywords 테이블에 upsert하고 article_keywords에 연결."""
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords)
        except (json.JSONDecodeError, TypeError):
            keywords = []
    for kw in keywords or []:
        if not kw:
            continue
        conn.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (kw,))
        kw_row = conn.execute("SELECT id FROM keywords WHERE keyword = ?", (kw,)).fetchone()
        if kw_row:
            conn.execute(
                "INSERT OR IGNORE INTO article_keywords (article_id, keyword_id) VALUES (?, ?)", (article_id, kw_row[0])
            )


# ── 기사 관련 ─────────────────────────────────────────────────────────────────


def upsert_article(article: dict) -> bool:
    """새 기사를 저장. 중복 URL이면 False 반환.
    article 딕셔너리의 keywords(list 또는 JSON 문자열)를 keywords/article_keywords 테이블에 연결."""
    keywords = article.get("keywords", [])
    try:
        with _db() as conn:
            cur = conn.execute(
                """
                INSERT INTO articles
                    (url, title, source, description, author,
                     image_url, published_at, platform_score)
                VALUES
                    (:url, :title, :source, :description, :author,
                     :image_url, :published_at, :platform_score)
                """,
                article,
            )
            _link_keywords(conn, cur.lastrowid, keywords)
        return True
    except sqlite3.IntegrityError:
        return False


def get_pending_articles(limit: int = 50) -> list[dict]:
    """아직 게시 안 된 기사를 final_score 내림차순으로 반환. keywords는 list[str]."""
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT a.*, GROUP_CONCAT(k.keyword) AS _kw_list
            FROM articles a
            LEFT JOIN article_keywords ak ON ak.article_id = a.id
            LEFT JOIN keywords k ON ak.keyword_id = k.id
            WHERE a.status = 'pending'
            GROUP BY a.id
            ORDER BY a.final_score DESC, a.platform_score DESC, a.crawled_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return _rows_with_keywords(rows)


def mark_as_posted(article_id: int, message_id: str, channel_id: str) -> None:
    with _db() as conn:
        conn.execute(
            """
            UPDATE articles
            SET status = 'posted',
                discord_message_id = ?,
                channel_id = ?,
                posted_at = datetime('now', '+9 hours')
            WHERE id = ?
            """,
            (message_id, channel_id, article_id),
        )


def get_article_by_message_id(message_id: str) -> dict | None:
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT a.*, GROUP_CONCAT(k.keyword) AS _kw_list
            FROM articles a
            LEFT JOIN article_keywords ak ON ak.article_id = a.id
            LEFT JOIN keywords k ON ak.keyword_id = k.id
            WHERE a.discord_message_id = ?
            GROUP BY a.id
            """,
            (message_id,),
        ).fetchall()
    result = _rows_with_keywords(rows)
    return result[0] if result else None


def update_article_reaction(article_id: int, liked: bool) -> None:
    field = "likes" if liked else "dislikes"
    with _db() as conn:
        conn.execute(
            f"UPDATE articles SET {field} = {field} + 1 WHERE id = ?",
            (article_id,),
        )


def update_final_scores(articles: list[dict]) -> None:
    """랭킹 계산 결과를 DB에 반영."""
    with _db() as conn:
        for a in articles:
            conn.execute(
                "UPDATE articles SET final_score = ? WHERE id = ?",
                (a.get("final_score", 0), a["id"]),
            )


# ── 선호도 관련 ───────────────────────────────────────────────────────────────


def _clamp(value: float) -> float:
    return max(PREFERENCE_MIN, min(PREFERENCE_MAX, value))


def update_source_preference(source: str, liked: bool) -> None:
    delta = SOURCE_DELTA if liked else -SOURCE_DELTA
    likes_inc = 1 if liked else 0
    dislikes_inc = 0 if liked else 1
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO source_preferences (source, multiplier, total_likes, total_dislikes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                multiplier     = MAX(?, MIN(?, multiplier + ?)),
                total_likes    = total_likes + ?,
                total_dislikes = total_dislikes + ?,
                last_updated   = datetime('now', '+9 hours')
            """,
            (
                source,
                _clamp(1.0 + delta),
                likes_inc,
                dislikes_inc,
                PREFERENCE_MIN,
                PREFERENCE_MAX,
                delta,
                likes_inc,
                dislikes_inc,
            ),
        )


def update_keyword_preference(keyword: str, liked: bool) -> None:
    delta = KEYWORD_DELTA if liked else -KEYWORD_DELTA
    likes_inc = 1 if liked else 0
    dislikes_inc = 0 if liked else 1
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO keywords (keyword, multiplier, total_likes, total_dislikes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                multiplier     = MAX(?, MIN(?, multiplier + ?)),
                total_likes    = total_likes + ?,
                total_dislikes = total_dislikes + ?,
                last_updated   = datetime('now', '+9 hours')
            """,
            (
                keyword,
                _clamp(1.0 + delta),
                likes_inc,
                dislikes_inc,
                PREFERENCE_MIN,
                PREFERENCE_MAX,
                delta,
                likes_inc,
                dislikes_inc,
            ),
        )


def get_all_preferences() -> dict:
    with _db() as conn:
        sources = conn.execute(
            """
            SELECT source, multiplier, total_likes, total_dislikes
            FROM source_preferences
            ORDER BY multiplier DESC
            """
        ).fetchall()
        keywords = conn.execute(
            """
            SELECT keyword, multiplier, total_likes, total_dislikes
            FROM keywords
            ORDER BY multiplier DESC
            LIMIT 20
            """
        ).fetchall()
    return {
        "sources": [dict(r) for r in sources],
        "keywords": [dict(r) for r in keywords],
    }


def reset_preferences() -> None:
    with _db() as conn:
        conn.execute("DELETE FROM source_preferences")
        conn.execute(
            "UPDATE keywords SET multiplier = 1.0, total_likes = 0, total_dislikes = 0, "
            "last_updated = datetime('now', '+9 hours')"
        )


def get_todays_posted_urls() -> list[str]:
    """오늘 이미 게시된 기사 URL 목록 반환 (중복 방지용)."""
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT url FROM articles
            WHERE status = 'posted'
              AND date(posted_at) = date('now', '+9 hours')
            """
        ).fetchall()
    return [r["url"] for r in rows]


def get_stats() -> dict:
    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        posted = conn.execute("SELECT COUNT(*) FROM articles WHERE status = 'posted'").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM articles WHERE status = 'pending'").fetchone()[0]
        total_likes = conn.execute("SELECT COALESCE(SUM(likes), 0) FROM articles").fetchone()[0]
        total_dislikes = conn.execute("SELECT COALESCE(SUM(dislikes), 0) FROM articles").fetchone()[0]
    return {
        "total": total,
        "posted": posted,
        "pending": pending,
        "total_likes": total_likes,
        "total_dislikes": total_dislikes,
    }
