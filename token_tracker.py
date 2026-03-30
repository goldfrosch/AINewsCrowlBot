"""
토큰 사용량 추적 모듈

- 별도 SQLite DB: data/token_usage.db
- API 호출마다 입력/출력 토큰 기록
- 5시간 윈도우 기준 사용량 집계 (Anthropic 요금제 한도 윈도우)
- 일별 통계 및 전체 평균 제공
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

TOKEN_DB_PATH = Path("data/token_usage.db")


@contextmanager
def _db():
    TOKEN_DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(TOKEN_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_token_db() -> None:
    """토큰 사용량 DB 초기화."""
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                called_at       TEXT    NOT NULL DEFAULT (datetime('now')),
                caller          TEXT    NOT NULL DEFAULT 'unknown',
                input_tokens    INTEGER NOT NULL DEFAULT 0,
                output_tokens   INTEGER NOT NULL DEFAULT 0,
                total_tokens    INTEGER NOT NULL DEFAULT 0,
                elapsed_seconds REAL    DEFAULT NULL
            );
        """)
        # 기존 DB에 컬럼이 없으면 추가
        # TODO: 마이그레이션 코드라 한번 사용되었으면 이제 없애도 됨.
        existing = {row[1] for row in conn.execute("PRAGMA table_info(token_usage)")}
        if "elapsed_seconds" not in existing:
            conn.execute("ALTER TABLE token_usage ADD COLUMN elapsed_seconds REAL DEFAULT NULL")


def log_token_usage(
    input_tokens: int,
    output_tokens: int,
    caller: str = "unknown",
    elapsed_seconds: float | None = None,
) -> None:
    """API 호출 토큰 사용량을 DB에 기록한다."""
    init_token_db()
    total = input_tokens + output_tokens
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO token_usage (caller, input_tokens, output_tokens, total_tokens, elapsed_seconds)
            VALUES (?, ?, ?, ?, ?)
            """,
            (caller, input_tokens, output_tokens, total, elapsed_seconds),
        )


def get_today_token_stats() -> dict:
    """오늘(로컬 시간 기준) 토큰 사용 통계를 반환한다."""
    with _db() as conn:
        summary = conn.execute(
            """
            SELECT
                COUNT(*)                         AS call_count,
                COALESCE(SUM(input_tokens),  0)  AS total_input,
                COALESCE(SUM(output_tokens), 0)  AS total_output,
                COALESCE(SUM(total_tokens),  0)  AS total_tokens,
                COALESCE(AVG(total_tokens),  0)  AS avg_per_call
            FROM token_usage
            WHERE date(called_at, 'localtime') = date('now', 'localtime')
            """
        ).fetchone()

        callers = conn.execute(
            """
            SELECT
                caller,
                COUNT(*)          AS calls,
                SUM(total_tokens) AS tokens
            FROM token_usage
            WHERE date(called_at, 'localtime') = date('now', 'localtime')
            GROUP BY caller
            ORDER BY tokens DESC
            """
        ).fetchall()

    return {
        "call_count":   summary["call_count"],
        "total_input":  summary["total_input"],
        "total_output": summary["total_output"],
        "total_tokens": summary["total_tokens"],
        "avg_per_call": round(summary["avg_per_call"]),
        "callers":      [dict(c) for c in callers],
    }


def get_average_daily_stats() -> dict:
    """전체 기간에 걸친 일별 평균 토큰 통계를 반환한다."""
    with _db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(DISTINCT date(called_at, 'localtime'))      AS total_days,
                COALESCE(SUM(total_tokens), 0)                    AS grand_total,
                COALESCE(AVG(total_tokens), 0)                    AS avg_per_call,
                COALESCE(
                    SUM(total_tokens) * 1.0 /
                    NULLIF(COUNT(DISTINCT date(called_at, 'localtime')), 0),
                    0
                )                                                  AS avg_per_day
            FROM token_usage
            """
        ).fetchone()

        # 최근 7일 일별 토큰
        daily = conn.execute(
            """
            SELECT
                date(called_at, 'localtime') AS day,
                SUM(total_tokens)            AS tokens,
                COUNT(*)                     AS calls
            FROM token_usage
            WHERE called_at >= datetime('now', '-7 days')
            GROUP BY day
            ORDER BY day DESC
            """
        ).fetchall()

    return {
        "total_days":    row["total_days"],
        "grand_total":   row["grand_total"],
        "avg_per_call":  round(row["avg_per_call"]),
        "avg_per_day":   round(row["avg_per_day"]),
        "recent_daily":  [dict(d) for d in daily],
    }


def get_window_stats() -> dict:
    """
    5시간 윈도우 기준 토큰 사용량을 반환한다.

    현재 윈도우: 최근 5시간
    이전 윈도우: 5~10시간 전
    pct_change: (현재 - 이전) / 이전 × 100  (이전 데이터 없으면 None)
    """
    with _db() as conn:
        current = conn.execute(
            """
            SELECT
                COUNT(*)                        AS calls,
                COALESCE(SUM(total_tokens), 0)  AS tokens
            FROM token_usage
            WHERE called_at >= datetime('now', '-5 hours')
            """
        ).fetchone()

        previous = conn.execute(
            """
            SELECT
                COUNT(*)                        AS calls,
                COALESCE(SUM(total_tokens), 0)  AS tokens
            FROM token_usage
            WHERE called_at >= datetime('now', '-10 hours')
              AND called_at <  datetime('now', '-5 hours')
            """
        ).fetchone()

    cur_tok  = current["tokens"]
    prev_tok = previous["tokens"]

    if prev_tok > 0:
        pct_change = round((cur_tok - prev_tok) / prev_tok * 100, 1)
    else:
        pct_change = None

    return {
        "current_calls":  current["calls"],
        "current_tokens": cur_tok,
        "prev_calls":     previous["calls"],
        "prev_tokens":    prev_tok,
        "pct_change":     pct_change,
    }
