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

from config import AI_KEYWORDS, EXCLUDE_URL_LOOKBACK_DAYS

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

    merged = merge_keyword_variants()
    if merged:
        print(f"[DB] 표기가 갈린 키워드 {merged}개 병합 (예: 'ai agent' → 'ai_agent')")

    removed = cleanup_noise_keywords()
    if removed:
        print(f"[DB] 학습 신호를 오염시키던 노이즈 키워드 {removed}개 정리")


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


def canonical_keyword(keyword: object) -> str:
    """키워드 표기를 하나로 통일한다 (소문자, 공백→언더스코어).

    모델은 `"ai agent"`를, `ranker.extract_keywords()`는 `"ai_agent"`를 만들어
    같은 개념이 두 행으로 쪼개졌고 👍/👎 신호가 반으로 갈렸다.
    하이픈은 `fine-tuning`, `gpt-4`처럼 원래 표기의 일부라 유지한다.
    """
    if not isinstance(keyword, str):
        return ""
    return keyword.strip().lower().replace(" ", "_")


def _link_keywords(conn, article_id: int, keywords) -> None:
    """keywords(list 또는 JSON 문자열)를 keywords 테이블에 upsert하고 article_keywords에 연결."""
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords)
        except (json.JSONDecodeError, TypeError):
            keywords = []
    for raw in keywords or []:
        kw = canonical_keyword(raw)
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
    keyword = canonical_keyword(keyword)
    if not keyword:
        return
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


def get_recent_posted_urls(days: int = EXCLUDE_URL_LOOKBACK_DAYS) -> list[str]:
    """최근 N일간 게시된 기사 URL 목록 반환.

    `get_todays_posted_urls()`는 브리핑이 실행되는 06:00 시점에 항상 빈 배열이라
    큐레이터에게 중복 회피 정보를 전혀 주지 못했다. 그 결과 모델이 어제·그제
    기사를 재추천하고, UNIQUE 제약으로 조용히 폐기되어 "하루 1건/0건" 문제가
    발생했다. 이 함수는 실제 게시 이력을 넘겨 재추천 자체를 막는다.

    최신순으로 정렬해 반환하므로 프롬프트에 앞부분만 넣어도 최근 중복을 먼저 막는다.
    """
    lookback = max(int(days), 0)
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT url FROM articles
            WHERE status = 'posted'
              AND posted_at IS NOT NULL
              AND date(posted_at) >= date('now', '+9 hours', ?)
            ORDER BY posted_at DESC
            """,
            (f"-{lookback} days",),
        ).fetchall()
    return [r["url"] for r in rows]


def get_all_article_urls() -> set[str]:
    """DB에 이미 존재하는 모든 기사 URL (게시 여부 무관)."""
    with _db() as conn:
        rows = conn.execute("SELECT url FROM articles").fetchall()
    return {r["url"] for r in rows}


def _ai_keyword_whitelist() -> set[str]:
    """AI_KEYWORDS의 공백형·언더스코어형 정규화 집합."""
    allowed: set[str] = set()
    for kw in AI_KEYWORDS:
        normalized = kw.strip().lower()
        if not normalized:
            continue
        allowed.add(normalized)
        allowed.add(normalized.replace(" ", "_"))
    return allowed


def merge_keyword_variants() -> int:
    """공백 표기 키워드를 언더스코어 정규형으로 병합한다.

    같은 개념이 두 행으로 나뉘어 👍/👎가 절반씩 흩어져 있던 것을 합친다.
    multiplier는 누적 likes/dislikes로 재계산한다.

    Returns:
        병합되어 사라진 행 수
    """
    merged = 0
    with _db() as conn:
        rows = conn.execute("SELECT id, keyword, total_likes, total_dislikes FROM keywords").fetchall()
        by_canonical: dict[str, list] = {}
        for row in rows:
            by_canonical.setdefault(canonical_keyword(row["keyword"]), []).append(row)

        for canonical, group in by_canonical.items():
            if not canonical or len(group) < 2:
                continue
            likes = sum(r["total_likes"] for r in group)
            dislikes = sum(r["total_dislikes"] for r in group)
            keeper = next((r for r in group if r["keyword"] == canonical), group[0])

            for row in group:
                if row["id"] == keeper["id"]:
                    continue
                conn.execute(
                    "UPDATE OR IGNORE article_keywords SET keyword_id = ? WHERE keyword_id = ?",
                    (keeper["id"], row["id"]),
                )
                conn.execute("DELETE FROM article_keywords WHERE keyword_id = ?", (row["id"],))
                conn.execute("DELETE FROM keywords WHERE id = ?", (row["id"],))
                merged += 1

            conn.execute(
                "UPDATE keywords SET keyword = ?, total_likes = ?, total_dislikes = ?, multiplier = ? WHERE id = ?",
                (canonical, likes, dislikes, _clamp(1.0 + KEYWORD_DELTA * (likes - dislikes)), keeper["id"]),
            )
    return merged


def cleanup_noise_keywords() -> int:
    """어떤 기사에도 연결되지 않은 비-AI 키워드 행을 제거한다.

    `ranker.extract_keywords()` 폴백이 제목·설명을 통째로 토큰화했기 때문에
    `'이유**'`, `'**선정'`, `'into'`, `'covering'` 같은 마크다운·불용어 잔재가
    선호도 테이블 상위를 차지했다(실측 622행). 모델이 태깅한 키워드는
    article_keywords에 연결되므로, 미연결 + 화이트리스트 밖 행만 정리한다.

    Returns:
        삭제된 행 수
    """
    allowed = _ai_keyword_whitelist()
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT k.id, k.keyword
            FROM keywords k
            LEFT JOIN article_keywords ak ON ak.keyword_id = k.id
            WHERE ak.keyword_id IS NULL
            """
        ).fetchall()
        doomed = [r["id"] for r in rows if (r["keyword"] or "").strip().lower() not in allowed]
        if not doomed:
            return 0
        conn.executemany("DELETE FROM keywords WHERE id = ?", [(kid,) for kid in doomed])
    return len(doomed)


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
