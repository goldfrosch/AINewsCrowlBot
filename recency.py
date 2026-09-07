"""
기사 신선도(recency) 판정

Anthropic `web_search` 도구에는 날짜/기간 필터 파라미터가 존재하지 않는다.
그래서 "최근 N일 이내"는 반드시 코드에서 강제해야 한다.

정책:
  - 발행일을 파싱할 수 있고 컷오프를 넘겼다면 → stale (폐기)
  - 발행일이 없거나 파싱 불가하면      → stale 아님 (보류하되 랭킹에서 감점)
  - 발행일이 미래(+1일 초과)이면       → 파싱 불가로 취급 (모델의 날짜 위조 방지)

발행일 미상을 폐기하지 않는 이유: 실측상 발행일이 비어 오는 정상 기사가 있고,
전부 버리면 "0건" 문제가 다시 발생한다. 대신 신선한 기사보다 뒤로 밀린다.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import RECENCY_MAX_AGE_DAYS, RECENCY_PREFERRED_AGE_DAYS, TIMEZONE

_KST = ZoneInfo(TIMEZONE)

# 미래 날짜 허용 오차 (타임존 차이 흡수용)
_FUTURE_TOLERANCE_DAYS = 1

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%Y년 %m월 %d일",
    "%Y-%m",
    "%Y",
)


def today() -> date:
    """KST 기준 오늘 날짜."""
    return datetime.now(tz=_KST).date()


def parse_published_date(value: object, ref: date | None = None) -> date | None:
    """발행일 문자열을 date로 변환한다. 불가하면 None.

    미래 날짜(허용 오차 초과)는 신뢰할 수 없으므로 None으로 취급한다.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        parsed = value
    elif isinstance(value, datetime):
        parsed = value.date()
    else:
        parsed = _parse_str(value)

    if parsed is None:
        return None

    ref = ref or today()
    if parsed > ref + timedelta(days=_FUTURE_TOLERANCE_DAYS):
        return None
    return parsed


def _parse_str(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or raw in {"-", "unknown", "Unknown", "N/A", "null", "None"}:
        return None

    iso_candidate = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_candidate).date()
    except ValueError:
        pass

    # 'YYYY-MM-DD ...' 처럼 앞부분만 날짜인 경우
    head = raw[:10]
    for fmt in _DATE_FORMATS:
        for candidate in (raw, head):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def age_days(value: object, ref: date | None = None) -> int | None:
    """발행일로부터 경과 일수. 파싱 불가하면 None."""
    ref = ref or today()
    parsed = parse_published_date(value, ref=ref)
    if parsed is None:
        return None
    return max((ref - parsed).days, 0)


def is_stale(value: object, max_age_days: int = RECENCY_MAX_AGE_DAYS, ref: date | None = None) -> bool:
    """컷오프를 넘긴 기사인지 판정. 발행일 미상은 stale로 보지 않는다."""
    age = age_days(value, ref=ref)
    return age is not None and age > max_age_days


def recency_multiplier(value: object, ref: date | None = None) -> float:
    """신선할수록 높은 랭킹 배율. 발행일 미상은 약한 감점."""
    age = age_days(value, ref=ref)
    if age is None:
        return 0.85
    if age <= 1:
        return 1.6
    if age <= 3:
        return 1.35
    if age <= 7:
        return 1.15
    if age <= 14:
        return 0.9
    if age <= 30:
        return 0.7
    return 0.5


def describe(value: object, ref: date | None = None) -> str:
    """로그용 신선도 표기."""
    age = age_days(value, ref=ref)
    return "발행일미상" if age is None else f"{age}일 전"


def prompt_lines(max_age_days: int = RECENCY_MAX_AGE_DAYS) -> list[str]:
    """프롬프트 상단에 넣을 날짜·컷오프 안내 문장.

    모델은 오늘 날짜를 모르므로 반드시 명시해야 한다. curator 폴백과
    에이전트가 같은 문구를 쓰도록 여기서 한 번만 정의한다.
    """
    ref = today()
    cutoff = ref - timedelta(days=max_age_days)
    preferred = ref - timedelta(days=min(RECENCY_PREFERRED_AGE_DAYS, max_age_days))
    return [
        f"Today is {ref.isoformat()} (Asia/Seoul).",
        f"HARD REQUIREMENT: if you can determine an article's publication date and it is before "
        f"{cutoff.isoformat()} (not within the last {max_age_days} days), drop it.",
        "If you CANNOT determine a date, still return the article with an empty published_at. "
        "Never guess a date, and never drop an article only because its date is missing — "
        "the pipeline fetches every page and verifies the date itself.",
        f"Strongly prefer articles published on or after {preferred.isoformat()}.",
        f'Include "{ref.year}" and the current month in your search queries to surface recent pages.',
        "",
    ]


def max_age_from_intent(intent: dict | None) -> int:
    """활성 큐레이션 의도의 recency_hours를 일수로 환산한다.

    의도가 비활성이거나 값이 없으면 config의 기본 컷오프를 쓴다.
    """
    if isinstance(intent, dict) and intent.get("active"):
        hours = intent.get("recency_hours")
        if isinstance(hours, int) and hours > 0:
            return max(1, -(-hours // 24))  # ceil division
    return RECENCY_MAX_AGE_DAYS
