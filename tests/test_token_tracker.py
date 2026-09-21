"""토큰·비용 계측 테스트.

`input_tokens`만 기록하던 기존 구현은 캐시 히트와 web_search 과금을 통째로
놓쳤다. 여기서 검증하는 것은 "청구서에 찍히는 항목이 전부 DB에 남는가"다.
"""

from __future__ import annotations

import sqlite3

import pytest

import token_tracker


class _Usage:
    """SDK usage 객체 대역. 속성 유무를 테스트마다 다르게 만든다."""

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


def test_estimate_cost_includes_cache_and_web_search() -> None:
    """캐시 기록 1.25배·캐시 히트 0.1배·검색 $0.01/회가 모두 반영되어야 한다."""
    cost = token_tracker.estimate_cost(
        "claude-sonnet-4-6",
        input_tokens=100_000,
        output_tokens=10_000,
        cache_creation_tokens=20_000,
        cache_read_tokens=500_000,
        web_search_requests=3,
    )
    # (100000*3 + 20000*3*1.25 + 500000*3*0.1 + 10000*15) / 1e6 + 3*0.01
    assert cost == pytest.approx(0.705)


def test_model_pricing_matches_versioned_ids() -> None:
    """`claude-sonnet-4-6-20260101`처럼 날짜가 붙어도 같은 단가를 써야 한다."""
    assert token_tracker.model_pricing("claude-sonnet-4-6-20260101") == (3.0, 15.0)


def test_unknown_model_falls_back_to_most_expensive() -> None:
    """모르는 모델을 싸게 잡으면 비용을 과소보고한다. 비싼 쪽으로 가정한다."""
    assert token_tracker.model_pricing("claude-future-9") == (5.0, 25.0)
    assert token_tracker.estimate_cost("claude-future-9", 1_000_000, 0) == pytest.approx(5.0)


def test_log_api_usage_records_cache_and_search_fields() -> None:
    usage = _Usage(
        input_tokens=1_000,
        output_tokens=200,
        cache_creation_input_tokens=300,
        cache_read_input_tokens=5_000,
        server_tool_use=_Usage(web_search_requests=4),
    )

    token_tracker.log_api_usage(usage, caller="agent_find_articles", model="claude-sonnet-4-6", elapsed_seconds=12.5)

    stats = token_tracker.get_today_token_stats()
    assert stats["total_input"] == 1_000
    assert stats["total_output"] == 200
    assert stats["total_cache_write"] == 300
    assert stats["total_cache_read"] == 5_000
    assert stats["total_searches"] == 4
    # 캐시 히트도 청구 대상이므로 합계에 포함되어야 한다 (1000+200+300+5000)
    assert stats["total_tokens"] == 6_500
    assert stats["total_cost"] == pytest.approx(0.0486, abs=1e-4)


def test_log_api_usage_tolerates_missing_server_tool_use() -> None:
    """심사 호출에는 web_search가 없다. 속성 부재로 터지면 안 된다."""
    token_tracker.log_api_usage(
        _Usage(input_tokens=500, output_tokens=100),
        caller="editorial_review",
        model="claude-sonnet-4-6",
    )

    stats = token_tracker.get_today_token_stats()
    assert stats["total_searches"] == 0
    assert stats["total_cache_read"] == 0
    assert stats["total_tokens"] == 600


def test_log_api_usage_ignores_non_numeric_fields() -> None:
    """SDK 버전에 따라 None이 오거나 Mock이 섞여도 0으로 떨어져야 한다."""
    token_tracker.log_api_usage(
        _Usage(input_tokens=None, output_tokens="oops", cache_read_input_tokens=True),
        caller="weird",
        model="claude-sonnet-4-6",
    )

    stats = token_tracker.get_today_token_stats()
    assert stats["total_tokens"] == 0
    assert stats["total_cost"] == 0


def test_callers_are_ordered_by_cost() -> None:
    """어디에 돈이 나가는지 보려면 토큰이 아니라 비용 순이어야 한다."""
    token_tracker.log_token_usage(1_000, 100, caller="cheap", model="claude-sonnet-4-6")
    token_tracker.log_token_usage(1_000, 100, caller="pricey", model="claude-opus-5")

    callers = token_tracker.get_today_token_stats()["callers"]
    assert [c["caller"] for c in callers] == ["pricey", "cheap"]


def test_get_usage_since_isolates_one_run() -> None:
    """같은 날 여러 번 돌려도 '이번 실행 1회'의 비용만 떼어 볼 수 있어야 한다."""
    token_tracker.log_token_usage(9_999, 9_999, caller="previous_run", model="claude-opus-5")

    mark = token_tracker.latest_row_id()
    token_tracker.log_api_usage(
        _Usage(input_tokens=1_000, output_tokens=100, server_tool_use=_Usage(web_search_requests=6)),
        caller="agent_find_articles",
        model="claude-sonnet-4-6",
        elapsed_seconds=80.0,
    )
    token_tracker.log_api_usage(
        _Usage(input_tokens=2_000, output_tokens=500),
        caller="editorial_review",
        model="claude-sonnet-4-6",
        elapsed_seconds=15.0,
    )

    usage = token_tracker.get_usage_since(mark)
    assert usage["call_count"] == 2
    assert usage["total_input"] == 3_000
    assert usage["total_searches"] == 6
    assert usage["total_seconds"] == 95.0
    # 탐색 (1000*3 + 100*15)/1e6 + 6*$0.01 = 0.0645 · 심사 (2000*3 + 500*15)/1e6 = 0.0135
    # 이전 실행(opus, 대량)이 섞이면 비용이 수십 배로 튄다
    assert usage["total_cost"] == pytest.approx(0.078)
    assert [c["caller"] for c in usage["callers"]] == ["agent_find_articles", "editorial_review"]


def test_get_usage_since_returns_zero_when_no_calls() -> None:
    """크레딧 소진으로 호출이 전부 실패하면 0건이어야 한다 (과금 없음)."""
    mark = token_tracker.latest_row_id()
    usage = token_tracker.get_usage_since(mark)
    assert usage["call_count"] == 0
    assert usage["total_cost"] == 0


def test_init_migrates_legacy_table(tmp_path) -> None:
    """기존 운영 DB에는 새 컬럼이 없다. ALTER로 살려야 기록이 끊기지 않는다."""
    legacy = tmp_path / "legacy_token.db"
    conn = sqlite3.connect(legacy)
    conn.executescript("""
        CREATE TABLE token_usage (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            called_at     TEXT    NOT NULL DEFAULT (datetime('now', '+9 hours')),
            caller        TEXT    NOT NULL DEFAULT 'unknown',
            input_tokens  INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens  INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO token_usage (caller, input_tokens, output_tokens, total_tokens)
        VALUES ('old_run', 700, 300, 1000);
    """)
    conn.commit()
    conn.close()

    token_tracker.set_token_db_path(legacy)
    token_tracker.init_token_db()
    token_tracker.log_api_usage(
        _Usage(input_tokens=10, output_tokens=5, server_tool_use=_Usage(web_search_requests=2)),
        caller="new_run",
        model="claude-sonnet-4-6",
    )

    stats = token_tracker.get_today_token_stats()
    # 옛 행은 비용 0으로 보존되고, 새 행만 검색 횟수를 가진다
    assert stats["call_count"] == 2
    assert stats["total_searches"] == 2
    assert stats["total_input"] == 710
