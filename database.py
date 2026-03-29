"""
SQLite 데이터베이스 관리
- articles: 크롤링된 기사 (URL 기준 중복 방지)
- source_preferences: 소스별 👍/👎 기반 선호도 배율
- keyword_preferences: 키워드별 선호도 배율
"""
import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

DB_PATH = Path("data/bot.db")

PREFERENCE_MIN = 0.1
PREFERENCE_MAX = 5.0
SOURCE_DELTA = 0.15    # 👍/👎 시 소스 배율 변화량
KEYWORD_DELTA = 0.05   # 👍/👎 시 키워드 배율 변화량


@contextmanager
def _db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
                keywords           TEXT    DEFAULT '[]',
                crawled_at         TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS source_preferences (
                source         TEXT PRIMARY KEY,
                multiplier     REAL    DEFAULT 1.0,
                total_likes    INTEGER DEFAULT 0,
                total_dislikes INTEGER DEFAULT 0,
                last_updated   TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS keyword_preferences (
                keyword        TEXT PRIMARY KEY,
                multiplier     REAL    DEFAULT 1.0,
                total_likes    INTEGER DEFAULT 0,
                total_dislikes INTEGER DEFAULT 0,
                last_updated   TEXT    DEFAULT (datetime('now'))
            );
        """)


# ── 기사 관련 ─────────────────────────────────────────────────────────────

def upsert_article(article: dict) -> bool:
    """새 기사를 저장. 중복 URL이면 False 반환."""
    try:
        with _db() as conn:
            conn.execute(
                """
                INSERT INTO articles
                    (url, title, source, description, author,
                     image_url, published_at, platform_score, keywords)
                VALUES
                    (:url, :title, :source, :description, :author,
                     :image_url, :published_at, :platform_score, :keywords)
                """,
                article,
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_pending_articles(limit: int = 50) -> list[dict]:
    """아직 게시 안 된 기사를 final_score 내림차순으로 반환."""
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM articles
            WHERE status = 'pending'
            ORDER BY final_score DESC, platform_score DESC, crawled_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_as_posted(article_id: int, message_id: str, channel_id: str) -> None:
    with _db() as conn:
        conn.execute(
            """
            UPDATE articles
            SET status = 'posted',
                discord_message_id = ?,
                channel_id = ?,
                posted_at = datetime('now')
            WHERE id = ?
            """,
            (message_id, channel_id, article_id),
        )


def get_article_by_message_id(message_id: str) -> Optional[dict]:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM articles WHERE discord_message_id = ?",
            (message_id,),
        ).fetchone()
    return dict(row) if row else None


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


# ── 선호도 관련 ───────────────────────────────────────────────────────────

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
                last_updated   = datetime('now')
            """,
            (
                source,
                _clamp(1.0 + delta),
                likes_inc,
                dislikes_inc,
                PREFERENCE_MIN, PREFERENCE_MAX, delta,
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
            INSERT INTO keyword_preferences (keyword, multiplier, total_likes, total_dislikes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                multiplier     = MAX(?, MIN(?, multiplier + ?)),
                total_likes    = total_likes + ?,
                total_dislikes = total_dislikes + ?,
                last_updated   = datetime('now')
            """,
            (
                keyword,
                _clamp(1.0 + delta),
                likes_inc,
                dislikes_inc,
                PREFERENCE_MIN, PREFERENCE_MAX, delta,
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
            FROM keyword_preferences
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
        conn.execute("DELETE FROM keyword_preferences")


def get_todays_posted_urls() -> list[str]:
    """오늘 이미 게시된 기사 URL 목록 반환 (중복 방지용)."""
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT url FROM articles
            WHERE status = 'posted'
              AND date(posted_at) = date('now', 'localtime')
            """
        ).fetchall()
    return [r["url"] for r in rows]


def get_stats() -> dict:
    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        posted = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE status = 'posted'"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE status = 'pending'"
        ).fetchone()[0]
        total_likes = conn.execute(
            "SELECT COALESCE(SUM(likes), 0) FROM articles"
        ).fetchone()[0]
        total_dislikes = conn.execute(
            "SELECT COALESCE(SUM(dislikes), 0) FROM articles"
        ).fetchone()[0]
    return {
        "total": total,
        "posted": posted,
        "pending": pending,
        "total_likes": total_likes,
        "total_dislikes": total_dislikes,
    }
