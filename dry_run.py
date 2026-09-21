"""
CLI dry-run: Discord 없이 큐레이션 파이프라인을 실행합니다.

Usage:
    python dry_run.py                  # 기본 2개
    python dry_run.py --count 1        # 1개만
    python dry_run.py --verbose        # 상세 출력
    python dry_run.py --db data/bot.db # DB 경로 지정

ANTHROPIC_API_KEY가 .env에 설정되어야 합니다.
"""

import argparse
import json
import sys

from dotenv import load_dotenv

load_dotenv()

import database as db
import token_tracker
from config import ARTICLES_PER_POST, REVIEW_MODEL, SEARCH_MODEL
from pipeline import run_curation_pipeline


def _print_cost_report(usage_mark: int) -> None:
    """이번 실행에서 실제로 나간 비용을 호출자별로 보여준다.

    게이트 통과율만 봐서는 "싸게 많이 거르는지 비싸게 조금 거르는지"를 알 수 없다.
    수율과 비용을 한 화면에서 같이 봐야 어느 손잡이를 돌릴지 정할 수 있다.
    """
    usage = token_tracker.get_usage_since(usage_mark)
    if not usage["call_count"]:
        print("\n[Dry Run] API 호출 없음 (비용 $0)")
        return

    print(f"\n[Dry Run] 이번 실행 비용: ${usage['total_cost']:.4f} · {usage['total_seconds']:.0f}초")
    print(
        f"  토큰 입력 {usage['total_input']:,} / 출력 {usage['total_output']:,} / "
        f"캐시 기록 {usage['total_cache_write']:,} / 캐시 히트 {usage['total_cache_read']:,}"
    )
    print(f"  웹 검색 {usage['total_searches']}회 (${usage['total_searches'] * 0.01:.2f})")
    for caller in usage["callers"]:
        print(
            f"      · {caller['caller']:34} ${caller['cost']:7.4f}  "
            f"{caller['tokens']:>8,} tok  검색 {caller['searches']}회  "
            f"{caller['seconds']:5.1f}초  [{caller['model'] or '미기록'}]"
        )


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="AINewsCrowlBot dry-run (no Discord)")
    parser.add_argument(
        "--count", type=int, default=ARTICLES_PER_POST, help=f"수집할 기사 수 (기본 {ARTICLES_PER_POST})"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="상세 출력")
    parser.add_argument("--db", type=str, default="data/bot.db", help="DB 경로")
    args = parser.parse_args()

    db.set_db_path(args.db)
    db.init_db()

    print(f"[Dry Run] 큐레이션 파이프라인 시작 (count={args.count})...")
    print(f"[Dry Run] 모델 — 탐색 {SEARCH_MODEL} / 심사 {REVIEW_MODEL}")
    usage_mark = token_tracker.latest_row_id()
    result = run_curation_pipeline(count=args.count)

    print("\n[Dry Run] 결과 요약:")
    print(f"  - curator 반환: {result['raw_count']}개")
    print(f"  - 품질 기준 탈락: {result.get('quality_dropped', 0)}개")
    stages = result.get("stages") or {}
    print(
        f"  - 본문검증 {stages.get('verify_passed', 0)}/{stages.get('verify_attempted', 0)} · "
        f"심사통과 {stages.get('review_kept', 0)}/{stages.get('review_candidates', 0)}"
    )
    for reason, count in sorted((stages.get("reason_counts") or {}).items(), key=lambda item: item[1], reverse=True):
        print(f"      · {reason} ({count}건)")
    print(f"  - DB 신규 저장: {result['new_count']}개")
    print(f"  - 랭킹 후 게시 대상: {len(result['articles'])}개")

    # 진단 리포트를 에러보다 먼저 낸다. 이전 구현은 여기서 곧장 exit해서
    # 정작 원인을 알려줄 단계별 통과율과 비용을 한 줄도 못 보고 끝났다.
    _print_cost_report(usage_mark)

    if result["error"]:
        print(f"\n[Dry Run] 파이프라인 에러: {result['error']}")
        sys.exit(1)

    if not result["articles"]:
        print("\n[Dry Run] 게시할 기사가 없습니다.")

        if args.verbose and result["raw_count"] == 0:
            print("  원인: curator.research()가 빈 리스트를 반환했습니다.")
            print("  - ANTHROPIC_API_KEY가 올바른지 확인하세요.")
            print("  - Claude API 호출이 실패했을 수 있습니다.")
        elif args.verbose and result["new_count"] == 0:
            print("  원인: 모든 기사가 이미 DB에 존재합니다 (중복).")
        elif args.verbose:
            print("  원인: pending 기사가 없거나 랭킹에서 모두 제외되었습니다.")
        sys.exit(0)

    for i, article in enumerate(result["articles"], 1):
        print(f"\n{i}. {article['title']}")
        print(f"   출처: {article['source']}")
        print(f"   URL:  {article['url']}")
        print(f"   점수: {article.get('final_score', 0):.4f}")
        if args.verbose:
            desc = article.get("description", "")[:200]
            if desc:
                print(f"   설명: {desc}")
            kws = article.get("keywords", [])
            if kws:
                print(f"   키워드: {kws}")
            pub = article.get("published_at", "")[:10]
            if pub:
                print(f"   발행일: {pub}")

    if args.verbose:
        print("\n[전체 JSON]")
        print(json.dumps(result["articles"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
