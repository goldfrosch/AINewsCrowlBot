"""
토큰 사용량 추적 모듈

- 별도 SQLite DB: data/token_usage.db
- API 호출마다 입력/출력/캐시/서버툴 사용량과 달러 추정치 기록
- 5시간 윈도우 기준 사용량 집계 (Anthropic 요금제 한도 윈도우)
- 일별 통계 및 전체 평균 제공

`input_tokens`만 기록하던 기존 구현은 실제 비용의 일부만 봤다:
  - 캐시 히트(`cache_read_input_tokens`)는 `input_tokens`에 포함되지 않는다.
    시스템 블록에 `cache_control`을 걸어두고도 집계에서 통째로 빠져 있었다.
  - `web_search` 결과는 검색 반복마다 input 토큰으로 재청구되고,
    검색 호출 자체도 1,000회당 $10로 별도 과금된다.
그래서 "어디에 돈이 나가는지"를 판단할 수 없었다. 이 모듈이 그 구멍을 메운다.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

TOKEN_DB_PATH = Path("data/token_usage.db")

# ── 단가표 (USD / 1M 토큰, 2026-09 기준) ────────────────────────────────────
# 출처: .claude/skills/claude-api.md 가격표.
MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
}
# 모르는 모델은 과소평가보다 과대평가가 안전하므로 가장 비싼 단가를 쓴다.
_FALLBACK_PRICING: tuple[float, float] = (5.0, 25.0)
# 캐시 단가는 Anthropic 공통 배율이라 입력 단가에서 유도한다
# (모델이 추가될 때마다 네 값을 손으로 맞추면 반드시 어긋난다).
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.1
# web_search: $10 / 1,000 searches
_WEB_SEARCH_USD_PER_CALL = 0.01


def set_token_db_path(path: Path | str) -> None:
    """TOKEN_DB_PATH를 override합니다 (테스트·dry-run용)."""
    global TOKEN_DB_PATH
    TOKEN_DB_PATH = Path(path)


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


# 기존 DB에 없을 수 있는 컬럼 → 타입 선언. ALTER TABLE은 IF NOT EXISTS를 지원하지 않는다.
_ADDED_COLUMNS: dict[str, str] = {
    "elapsed_seconds": "REAL DEFAULT NULL",
    "cache_creation_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cache_read_tokens": "INTEGER NOT NULL DEFAULT 0",
    "web_search_requests": "INTEGER NOT NULL DEFAULT 0",
    "model": "TEXT NOT NULL DEFAULT ''",
    "cost_usd": "REAL NOT NULL DEFAULT 0",
}


def init_token_db() -> None:
    """토큰 사용량 DB 초기화."""
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                called_at             TEXT    NOT NULL DEFAULT (datetime('now', '+9 hours')),
                caller                TEXT    NOT NULL DEFAULT 'unknown',
                input_tokens          INTEGER NOT NULL DEFAULT 0,
                output_tokens         INTEGER NOT NULL DEFAULT 0,
                total_tokens          INTEGER NOT NULL DEFAULT 0,
                elapsed_seconds       REAL    DEFAULT NULL,
                cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
                web_search_requests   INTEGER NOT NULL DEFAULT 0,
                model                 TEXT    NOT NULL DEFAULT '',
                cost_usd              REAL    NOT NULL DEFAULT 0
            );
        """)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(token_usage)")}
        for column, declaration in _ADDED_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE token_usage ADD COLUMN {column} {declaration}")


def model_pricing(model: str) -> tuple[float, float]:
    """모델의 (입력, 출력) 단가를 USD/1M 토큰으로 반환한다.

    `claude-sonnet-4-6-20260101`처럼 날짜가 붙은 ID도 접두사로 매칭한다.
    """
    if model in MODEL_PRICING_USD_PER_MTOK:
        return MODEL_PRICING_USD_PER_MTOK[model]
    for known, pricing in MODEL_PRICING_USD_PER_MTOK.items():
        if model.startswith(known):
            return pricing
    return _FALLBACK_PRICING


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    web_search_requests: int = 0,
) -> float:
    """호출 1회의 달러 비용 추정치."""
    input_price, output_price = model_pricing(model)
    token_cost = (
        input_tokens * input_price
        + cache_creation_tokens * input_price * _CACHE_WRITE_MULTIPLIER
        + cache_read_tokens * input_price * _CACHE_READ_MULTIPLIER
        + output_tokens * output_price
    ) / 1_000_000
    return round(token_cost + web_search_requests * _WEB_SEARCH_USD_PER_CALL, 6)


def _usage_int(source: object, name: str) -> int:
    """usage 객체에서 정수 필드를 안전하게 읽는다.

    SDK 버전에 따라 필드가 없거나 None이고, 테스트의 MagicMock은 아무 속성이나
    Mock을 돌려준다. 숫자가 아니면 0으로 떨어뜨려 전파를 막는다.
    """
    value = getattr(source, name, 0)
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def log_api_usage(usage: object, caller: str, model: str, elapsed_seconds: float | None = None) -> None:
    """SDK usage 객체 하나로 토큰·캐시·서버툴 사용량과 비용을 기록한다.

    호출부가 `usage.input_tokens`만 꺼내 넘기면 캐시와 web_search 과금이
    통째로 누락된다. 추출 책임을 여기로 모아 두 호출 경로가 같은 필드를 보게 한다.
    """
    log_token_usage(
        _usage_int(usage, "input_tokens"),
        _usage_int(usage, "output_tokens"),
        caller=caller,
        elapsed_seconds=elapsed_seconds,
        cache_creation_tokens=_usage_int(usage, "cache_creation_input_tokens"),
        cache_read_tokens=_usage_int(usage, "cache_read_input_tokens"),
        web_search_requests=_usage_int(getattr(usage, "server_tool_use", None), "web_search_requests"),
        model=model,
    )


def log_token_usage(
    input_tokens: int,
    output_tokens: int,
    caller: str = "unknown",
    elapsed_seconds: float | None = None,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    web_search_requests: int = 0,
    model: str = "",
) -> None:
    """API 호출 사용량을 DB에 기록한다.

    `total_tokens`는 청구 대상 토큰 전체(입력+캐시 기록+캐시 히트+출력)다.
    캐시 히트를 빼면 "토큰은 줄었는데 비용은 그대로"인 착시가 생긴다.
    """
    init_token_db()
    total = input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens
    cost = estimate_cost(
        model,
        input_tokens,
        output_tokens,
        cache_creation_tokens,
        cache_read_tokens,
        web_search_requests,
    )
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO token_usage (
                caller, input_tokens, output_tokens, total_tokens, elapsed_seconds,
                cache_creation_tokens, cache_read_tokens, web_search_requests, model, cost_usd
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                caller,
                input_tokens,
                output_tokens,
                total,
                elapsed_seconds,
                cache_creation_tokens,
                cache_read_tokens,
                web_search_requests,
                model,
                cost,
            ),
        )


def latest_row_id() -> int:
    """지금까지 기록된 마지막 행 id.

    실행 직전에 찍어두고 `get_usage_since()`에 넘기면 "이번 실행 1회"의 비용만
    떼어 볼 수 있다. 날짜 기준 집계는 같은 날 여러 번 돌리면 섞여서 못 쓴다.
    """
    init_token_db()
    with _db() as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS last_id FROM token_usage").fetchone()
    return row["last_id"]


def get_usage_since(row_id: int) -> dict:
    """`row_id` 이후에 기록된 호출들의 사용량·비용 집계."""
    with _db() as conn:
        summary = conn.execute(
            """
            SELECT
                COUNT(*)                                 AS call_count,
                COALESCE(SUM(input_tokens),          0)  AS total_input,
                COALESCE(SUM(output_tokens),         0)  AS total_output,
                COALESCE(SUM(total_tokens),          0)  AS total_tokens,
                COALESCE(SUM(cache_creation_tokens), 0)  AS total_cache_write,
                COALESCE(SUM(cache_read_tokens),     0)  AS total_cache_read,
                COALESCE(SUM(web_search_requests),   0)  AS total_searches,
                COALESCE(SUM(cost_usd),              0)  AS total_cost,
                COALESCE(SUM(elapsed_seconds),       0)  AS total_seconds
            FROM token_usage
            WHERE id > ?
            """,
            (row_id,),
        ).fetchone()

        callers = conn.execute(
            """
            SELECT
                caller,
                model,
                COUNT(*)                            AS calls,
                COALESCE(SUM(total_tokens),     0)  AS tokens,
                COALESCE(SUM(web_search_requests), 0) AS searches,
                COALESCE(SUM(cost_usd),         0)  AS cost,
                COALESCE(SUM(elapsed_seconds),  0)  AS seconds
            FROM token_usage
            WHERE id > ?
            GROUP BY caller, model
            ORDER BY cost DESC, tokens DESC
            """,
            (row_id,),
        ).fetchall()

    return {
        "call_count": summary["call_count"],
        "total_input": summary["total_input"],
        "total_output": summary["total_output"],
        "total_tokens": summary["total_tokens"],
        "total_cache_write": summary["total_cache_write"],
        "total_cache_read": summary["total_cache_read"],
        "total_searches": summary["total_searches"],
        "total_cost": round(summary["total_cost"], 6),
        "total_seconds": round(summary["total_seconds"], 1),
        "callers": [dict(c) for c in callers],
    }


def get_today_token_stats() -> dict:
    """오늘(로컬 시간 기준) 토큰 사용 통계를 반환한다."""
    with _db() as conn:
        summary = conn.execute(
            """
            SELECT
                COUNT(*)                                 AS call_count,
                COALESCE(SUM(input_tokens),          0)  AS total_input,
                COALESCE(SUM(output_tokens),         0)  AS total_output,
                COALESCE(SUM(total_tokens),          0)  AS total_tokens,
                COALESCE(SUM(cache_creation_tokens), 0)  AS total_cache_write,
                COALESCE(SUM(cache_read_tokens),     0)  AS total_cache_read,
                COALESCE(SUM(web_search_requests),   0)  AS total_searches,
                COALESCE(SUM(cost_usd),              0)  AS total_cost,
                COALESCE(AVG(total_tokens),          0)  AS avg_per_call
            FROM token_usage
            WHERE date(called_at) = date('now', '+9 hours')
            """
        ).fetchone()

        callers = conn.execute(
            """
            SELECT
                caller,
                COUNT(*)                    AS calls,
                SUM(total_tokens)           AS tokens,
                COALESCE(SUM(cost_usd), 0)  AS cost
            FROM token_usage
            WHERE date(called_at) = date('now', '+9 hours')
            GROUP BY caller
            ORDER BY cost DESC, tokens DESC
            """
        ).fetchall()

    return {
        "call_count": summary["call_count"],
        "total_input": summary["total_input"],
        "total_output": summary["total_output"],
        "total_tokens": summary["total_tokens"],
        "total_cache_write": summary["total_cache_write"],
        "total_cache_read": summary["total_cache_read"],
        "total_searches": summary["total_searches"],
        "total_cost": round(summary["total_cost"], 4),
        "avg_per_call": round(summary["avg_per_call"]),
        "callers": [dict(c) for c in callers],
    }


def get_average_daily_stats() -> dict:
    """전체 기간에 걸친 일별 평균 토큰 통계를 반환한다."""
    with _db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(DISTINCT date(called_at))                   AS total_days,
                COALESCE(SUM(total_tokens), 0)                    AS grand_total,
                COALESCE(SUM(cost_usd), 0)                        AS grand_cost,
                COALESCE(AVG(total_tokens), 0)                    AS avg_per_call,
                COALESCE(
                    SUM(total_tokens) * 1.0 /
                    NULLIF(COUNT(DISTINCT date(called_at)), 0),
                    0
                )                                                  AS avg_per_day,
                COALESCE(
                    SUM(cost_usd) /
                    NULLIF(COUNT(DISTINCT date(called_at)), 0),
                    0
                )                                                  AS avg_cost_per_day
            FROM token_usage
            """
        ).fetchone()

        # 최근 7일 일별 토큰
        daily = conn.execute(
            """
            SELECT
                date(called_at) AS day,
                SUM(total_tokens)            AS tokens,
                COUNT(*)                     AS calls,
                COALESCE(SUM(cost_usd), 0)   AS cost
            FROM token_usage
            WHERE called_at >= datetime('now', '+9 hours', '-7 days')
            GROUP BY day
            ORDER BY day DESC
            """
        ).fetchall()

    return {
        "total_days": row["total_days"],
        "grand_total": row["grand_total"],
        "grand_cost": round(row["grand_cost"], 4),
        "avg_per_call": round(row["avg_per_call"]),
        "avg_per_day": round(row["avg_per_day"]),
        "avg_cost_per_day": round(row["avg_cost_per_day"], 4),
        "recent_daily": [dict(d) for d in daily],
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
                COALESCE(SUM(total_tokens), 0)  AS tokens,
                COALESCE(SUM(cost_usd), 0)      AS cost
            FROM token_usage
            WHERE called_at >= datetime('now', '+9 hours', '-5 hours')
            """
        ).fetchone()

        previous = conn.execute(
            """
            SELECT
                COUNT(*)                        AS calls,
                COALESCE(SUM(total_tokens), 0)  AS tokens,
                COALESCE(SUM(cost_usd), 0)      AS cost
            FROM token_usage
            WHERE called_at >= datetime('now', '+9 hours', '-10 hours')
              AND called_at <  datetime('now', '+9 hours', '-5 hours')
            """
        ).fetchone()

    cur_tok = current["tokens"]
    prev_tok = previous["tokens"]

    pct_change = round((cur_tok - prev_tok) / prev_tok * 100, 1) if prev_tok > 0 else None

    return {
        "current_calls": current["calls"],
        "current_tokens": cur_tok,
        "current_cost": round(current["cost"], 4),
        "prev_calls": previous["calls"],
        "prev_tokens": prev_tok,
        "prev_cost": round(previous["cost"], 4),
        "pct_change": pct_change,
    }
