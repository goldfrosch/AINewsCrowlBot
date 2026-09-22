"""연속 운영 시뮬레이션 — 매일 브리핑을 돌리고 0건이 나는지 측정한다.

dry_run.py를 N번 부르는 것과 다른 점: 매 회차 선정분을 게시 완료로 표시해
저수지를 실제로 소진시키고, 중복 배제 목록이 날마다 늘어나는 조건을 만든다.
이것이 프로덕션에서 0건이 나던 실제 조건이다.

    # 라이브 (Anthropic 크레딧 소모)
    python tools/loop_runner.py --days 5 --count 6 --db data/loop.db

    # 오프라인 시뮬레이션 (비용 0). 외부 경계 3곳만 대체하고 파이프라인은 진짜로 돈다.
    python tools/loop_runner.py --simulate --days 7 --count 6
    python tools/loop_runner.py --simulate --legacy --days 7 --count 2   # 변경 전 대조군
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import database as db  # noqa: E402  - .env 로드 후에 import해야 설정이 반영된다
import recency  # noqa: E402
import token_tracker  # noqa: E402
from pipeline import run_curation_pipeline  # noqa: E402
from tools.sim_world import SimWorld, installed  # noqa: E402

# 필라별 하루 신규 공급량. 라이브에서 필라 하나가 하루에 실제로 찾아낸 후보 수를
# 보수적으로 잡은 값이다(ai_practice 12건 요청 → 12건 수신).
DEFAULT_SUPPLY = {"ai_practice": 8, "ai_game": 4, "graphics_3d": 2}


def _summarize(day: int, result: dict, usage: dict, elapsed: float) -> dict:
    stages = result.get("stages") or {}
    return {
        "day": day,
        "posted": len(result["articles"]),
        "raw": result["raw_count"],
        "verify": f"{stages.get('verify_passed', 0)}/{stages.get('verify_attempted', 0)}",
        "review": f"{stages.get('review_kept', 0)}/{stages.get('review_candidates', 0)}",
        "new": result["new_count"],
        "feed_topup": result["feed_topup"],
        "passes": len(stages.get("passes") or []),
        "searches": usage["total_searches"],
        "cost": round(usage["total_cost"], 4),
        "seconds": round(elapsed, 1),
        "verify_reasons": stages.get("verify_reasons") or {},
        "review_reasons": {k[:60]: v for k, v in (stages.get("reason_counts") or {}).items()},
        "sources": [a["source"] for a in result["articles"]],
        "titles": [a["title"][:70] for a in result["articles"]],
    }


def run_days(days: int, count: int, world: SimWorld | None = None, *, legacy: bool = False, quiet: bool = True):
    """N일치 브리핑을 실제 파이프라인으로 돌리고 일별 요약을 돌려준다.

    CLI와 회귀 테스트가 같은 경로를 쓰도록 여기로 뺐다.
    """
    rows: list[dict] = []
    context = installed(world, legacy=legacy) if world else nullcontext()
    with context:
        for day in range(1, days + 1):
            if world:
                world.advance(day - 1)
            if not quiet:
                header = f"[LOOP] DAY {day}/{days}" + (f" (sim {world.today})" if world else "")
                print(f"\n{'=' * 78}\n{header}\n{'=' * 78}", flush=True)
            mark = token_tracker.latest_row_id()
            started = time.perf_counter()
            try:
                result = run_curation_pipeline(count=count)
            except Exception as e:
                print(f"[LOOP] DAY {day} 실패: {type(e).__name__}: {e}", flush=True)
                rows.append({"day": day, "posted": 0, "error": f"{type(e).__name__}: {e}"})
                continue
            elapsed = time.perf_counter() - started
            usage = token_tracker.get_usage_since(mark)

            for article in result["articles"]:
                db.mark_as_posted(article["id"], f"loop-d{day}-{article['id']}", "loop")

            row = _summarize(day, result, usage, elapsed)
            if world:
                row["searches"] = world.stats["searches"]
                world.stats["searches"] = 0
            rows.append(row)
            if not quiet:
                print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)
    return rows


def build_world(days: int, supply: int = 0) -> SimWorld:
    """마지막 날이 실제 오늘이 되도록 과거에 앵커한 세계를 만든다.

    미래 날짜는 recency가 위조로 간주해 파싱 불가 처리하므로 앞으로 진행시키면 안 된다.
    """
    levels = dict.fromkeys(DEFAULT_SUPPLY, supply) if supply else dict(DEFAULT_SUPPLY)
    return SimWorld(start=recency.today() - timedelta(days=days - 1), daily_supply=levels)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="연속 운영 시뮬레이션")
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--db", type=str, default="data/loop.db")
    parser.add_argument("--out", type=str, default="data/loop_report.json")
    parser.add_argument("--simulate", action="store_true", help="외부 API 없이 오프라인 시뮬레이션")
    parser.add_argument("--legacy", action="store_true", help="시뮬레이션에서 변경 전 상수로 대조군 실행")
    parser.add_argument("--supply", type=int, default=0, help="필라별 하루 신규 공급량 일괄 지정")
    parser.add_argument("--quiet", action="store_true", help="일별 상세 JSON 생략")
    args = parser.parse_args()

    db.set_db_path(args.db)
    db.init_db()

    world = None
    if args.simulate:
        world = build_world(args.days, args.supply)
        mode = "LEGACY(변경 전)" if args.legacy else "CURRENT(변경 후)"
        print(f"[LOOP] 시뮬레이션 {mode} · 목표 {args.count}건/일 · 공급 {world.daily_supply}")

    rows = run_days(args.days, args.count, world, legacy=args.legacy, quiet=args.quiet)

    print(f"\n{'=' * 78}\n[LOOP] 요약\n{'=' * 78}")
    print(
        f"{'DAY':>4} {'게시':>4} {'수집':>4} {'본문검증':>9} {'심사':>8} {'패스':>4} {'검색':>4} {'비용':>8} {'초':>6}"
    )
    for row in rows:
        if "error" in row:
            print(f"{row['day']:>4} {'ERR':>4}  {row['error'][:60]}")
            continue
        print(
            f"{row['day']:>4} {row['posted']:>4} {row['raw']:>4} {row['verify']:>9} {row['review']:>8} "
            f"{row['passes']:>4} {row['searches']:>4} ${row['cost']:>7.4f} {row['seconds']:>6.1f}"
        )

    posted = [r.get("posted", 0) for r in rows]
    zero_days = sum(1 for p in posted if p == 0)
    short_days = sum(1 for p in posted if p < args.count)
    total_cost = sum(r.get("cost", 0) for r in rows)
    print(
        f"\n0건 발생 {zero_days}/{len(rows)}일 · 목표미달 {short_days}/{len(rows)}일 · "
        f"평균 게시 {sum(posted) / max(len(posted), 1):.1f}건 · "
        f"총 비용 ${total_cost:.2f} (일평균 ${total_cost / max(len(rows), 1):.2f})"
    )

    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"상세 리포트: {args.out}")


if __name__ == "__main__":
    main()
