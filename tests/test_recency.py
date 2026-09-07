"""recency.py 단위 테스트 — 최신 기사 강제의 핵심 로직"""

from datetime import date, timedelta

import recency

_REF = date(2026, 8, 21)


class TestParsePublishedDate:
    def test_iso_date(self):
        assert recency.parse_published_date("2026-08-20", ref=_REF) == date(2026, 8, 20)

    def test_iso_datetime_with_z(self):
        assert recency.parse_published_date("2026-08-19T10:30:00Z", ref=_REF) == date(2026, 8, 19)

    def test_iso_datetime_with_offset(self):
        assert recency.parse_published_date("2026-08-19T10:30:00+09:00", ref=_REF) == date(2026, 8, 19)

    def test_long_month_name(self):
        assert recency.parse_published_date("August 18, 2026", ref=_REF) == date(2026, 8, 18)

    def test_short_month_name(self):
        assert recency.parse_published_date("Aug 18, 2026", ref=_REF) == date(2026, 8, 18)

    def test_korean_format(self):
        assert recency.parse_published_date("2026년 08월 18일", ref=_REF) == date(2026, 8, 18)

    def test_date_with_trailing_text(self):
        assert recency.parse_published_date("2026-08-17 (updated)", ref=_REF) == date(2026, 8, 17)

    def test_empty_is_none(self):
        assert recency.parse_published_date("", ref=_REF) is None

    def test_placeholder_is_none(self):
        for placeholder in ("-", "unknown", "N/A", "null"):
            assert recency.parse_published_date(placeholder, ref=_REF) is None

    def test_garbage_is_none(self):
        assert recency.parse_published_date("sometime last week", ref=_REF) is None

    def test_non_string_is_none(self):
        assert recency.parse_published_date(None, ref=_REF) is None
        assert recency.parse_published_date(12345, ref=_REF) is None

    def test_future_date_rejected(self):
        """모델이 필터를 통과하려고 미래 날짜를 쓰는 것을 막는다."""
        assert recency.parse_published_date("2027-01-01", ref=_REF) is None

    def test_tomorrow_tolerated(self):
        """타임존 차이 흡수: +1일은 허용."""
        assert recency.parse_published_date("2026-08-22", ref=_REF) == date(2026, 8, 22)


class TestAgeDays:
    def test_today_is_zero(self):
        assert recency.age_days("2026-08-21", ref=_REF) == 0

    def test_week_old(self):
        assert recency.age_days("2026-08-14", ref=_REF) == 7

    def test_unknown_is_none(self):
        assert recency.age_days("", ref=_REF) is None

    def test_future_clamped_to_zero(self):
        assert recency.age_days("2026-08-22", ref=_REF) == 0


class TestIsStale:
    def test_within_window_kept(self):
        assert recency.is_stale("2026-08-16", max_age_days=7, ref=_REF) is False

    def test_boundary_kept(self):
        assert recency.is_stale("2026-08-14", max_age_days=7, ref=_REF) is False

    def test_just_outside_dropped(self):
        assert recency.is_stale("2026-08-13", max_age_days=7, ref=_REF) is True

    def test_real_world_stale_case_dropped(self):
        """실측: 2026-06-14 게시 기사의 발행일이 2025-08-01 (317일 전)."""
        assert recency.is_stale("2025-08-01", max_age_days=7, ref=_REF) is True

    def test_unknown_date_is_not_stale(self):
        """발행일 미상을 버리면 '0건' 문제가 재발하므로 통과시킨다."""
        assert recency.is_stale("", max_age_days=7, ref=_REF) is False
        assert recency.is_stale(None, max_age_days=7, ref=_REF) is False


class TestRecencyMultiplier:
    def test_fresher_scores_higher(self):
        today_mult = recency.recency_multiplier("2026-08-21", ref=_REF)
        week_mult = recency.recency_multiplier("2026-08-15", ref=_REF)
        month_mult = recency.recency_multiplier("2026-07-15", ref=_REF)
        assert today_mult > week_mult > month_mult

    def test_unknown_below_fresh(self):
        assert recency.recency_multiplier("", ref=_REF) < recency.recency_multiplier("2026-08-21", ref=_REF)


class TestMaxAgeFromIntent:
    def test_inactive_uses_default(self):
        from config import RECENCY_MAX_AGE_DAYS

        assert recency.max_age_from_intent({"active": False, "recency_hours": 24}) == RECENCY_MAX_AGE_DAYS
        assert recency.max_age_from_intent(None) == RECENCY_MAX_AGE_DAYS

    def test_active_intent_hours_to_days_ceil(self):
        assert recency.max_age_from_intent({"active": True, "recency_hours": 48}) == 2
        assert recency.max_age_from_intent({"active": True, "recency_hours": 25}) == 2
        assert recency.max_age_from_intent({"active": True, "recency_hours": 24}) == 1

    def test_active_intent_minimum_one_day(self):
        assert recency.max_age_from_intent({"active": True, "recency_hours": 1}) == 1


class TestPromptLines:
    def test_includes_today_and_cutoff(self):
        lines = "\n".join(recency.prompt_lines(7))
        assert recency.today().isoformat() in lines
        assert (recency.today() - timedelta(days=7)).isoformat() in lines
        assert "HARD REQUIREMENT" in lines

    def test_allows_articles_whose_date_cannot_be_determined(self):
        """web_search만으로는 발행일을 확정할 수 없다. 검증을 강제하면 모델이 []만 반환한다."""
        lines = "\n".join(recency.prompt_lines(7))
        assert "empty published_at" in lines
        assert "never drop an article only because its date is missing" in lines
