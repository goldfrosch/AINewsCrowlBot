"""연속 운영 0건 회귀 테스트.

0건은 단일 함수의 버그가 아니라 **여러 게이트의 상호작용**에서 나온다:
오버페치량, 신선도 창, 품질 컷, 근중복 제거, 저수지, 중복 배제 목록이 날마다
서로를 잠식한다. 단위 테스트로는 잡히지 않고 실제로 며칠 굴려봐야 드러난다.

`tools/sim_world`는 외부 경계 3곳(웹 검색·HTTP 전송·심사 API)만 대체하고
나머지 파이프라인은 진짜 코드를 돌린다. 따라서 이 테스트는 상수 회귀
(예: 오버페치 축소, 완화 패스 제거, 다양성 상한 오설정)에 실제로 반응한다.
"""

from __future__ import annotations

import article_fetch
from config import MIN_ACCEPTABLE_ARTICLES
from tools.loop_runner import build_world, run_days

DAYS = 4
SUPPLY = 3  # 필라별 하루 신규 공급량. 실측(필라당 12건 요청 → 12건 수신)보다 빡빡하게 잡았다.


def _posted(rows: list[dict]) -> list[int]:
    return [row.get("posted", 0) for row in rows]


def test_no_zero_day_over_consecutive_runs(tmp_db) -> None:
    """연속 운영에서 하루도 0건이 나오면 안 된다 — 이 프로젝트의 핵심 실패 모드."""
    rows = run_days(DAYS, 6, build_world(DAYS, SUPPLY))

    posted = _posted(rows)
    assert len(posted) == DAYS
    assert min(posted) > 0, f"0건 발생: {posted}"
    assert min(posted) >= MIN_ACCEPTABLE_ARTICLES, f"최소 기준 미달: {posted}"


def test_reservoir_serves_a_day_without_any_search(tmp_db) -> None:
    """전날 잉여가 남으면 검색 없이도 브리핑이 나가야 한다 (비용 방어의 핵심)."""
    rows = run_days(DAYS, 6, build_world(DAYS, SUPPLY))

    searchless = [row for row in rows if row.get("searches") == 0]
    assert searchless, f"저수지만으로 처리된 날이 없다: {[r.get('searches') for r in rows]}"
    assert all(row["posted"] > 0 for row in searchless)


def test_simulation_detects_the_redirect_normalization_bug(tmp_db, mocker) -> None:
    """시뮬레이터에 이빨이 있는지 확인한다.

    요청 URL에서 후행 슬래시를 떼면(= 수정 전 `fetch_html` 동작) 슬래시 정본
    사이트가 무한 301에 빠진다. 그 손실이 본문검증 통과율에 드러나야 한다.
    이 테스트가 실패한다면 시뮬레이터가 버그를 못 잡는다는 뜻이다.
    """
    healthy = run_days(DAYS, 6, build_world(DAYS, SUPPLY))
    healthy_rate = _verify_rate(healthy)

    mocker.patch.object(article_fetch, "request_url", article_fetch.canonicalize_url)
    broken = run_days(DAYS, 6, build_world(DAYS, SUPPLY))
    broken_rate = _verify_rate(broken)

    assert broken_rate < healthy_rate * 0.8, (
        f"리다이렉트 버그가 통과율에 반영되지 않았다 (정상 {healthy_rate:.2f} / 버그 {broken_rate:.2f})"
    )


def _verify_rate(rows: list[dict]) -> float:
    passed = attempted = 0
    for row in rows:
        if "/" not in str(row.get("verify", "")):
            continue
        ok, total = row["verify"].split("/")
        passed += int(ok)
        attempted += int(total)
    return passed / attempted if attempted else 0.0
