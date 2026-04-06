"""
CLI dry-run: Discord 없이 큐레이션 파이프라인을 실행합니다.

Usage:
    python dry_run.py                  # 기본 5개
    python dry_run.py --count 3        # 3개만
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
from pipeline import run_curation_pipeline


def main():
    parser = argparse.ArgumentParser(description="AINewsCrowlBot dry-run (no Discord)")
    parser.add_argument("--count", type=int, default=5, help="수집할 기사 수 (기본 5)")
    parser.add_argument("--verbose", "-v", action="store_true", help="상세 출력")
    parser.add_argument("--db", type=str, default="data/bot.db", help="DB 경로")
    args = parser.parse_args()

    db.set_db_path(args.db)
    db.init_db()

    print(f"[Dry Run] 큐레이션 파이프라인 시작 (count={args.count})...")
    result = run_curation_pipeline(count=args.count)

    if result["error"]:
        print(f"\n[Dry Run] 파이프라인 에러: {result['error']}")
        sys.exit(1)

    print("\n[Dry Run] 결과 요약:")
    print(f"  - curator 반환: {result['raw_count']}개")
    print(f"  - DB 신규 저장: {result['new_count']}개")
    print(f"  - 랭킹 후 게시 대상: {len(result['articles'])}개")

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
