"""
Discord 독립적 큐레이션 파이프라인

bot.py, dry_run.py, 테스트에서 공통으로 사용합니다.
"""

import curator
import database as db
from agents.preference_analysis import load_preference_profile
from config import ARTICLES_PER_POST
from ranker import rank_articles


def run_curation_pipeline(
    count: int = ARTICLES_PER_POST,
) -> dict:
    """
    큐레이션 파이프라인을 실행합니다 (Discord 의존 없음).

    Returns:
        {
            "articles":   list[dict],  # 랭킹 완료 기사
            "raw_count":  int,         # curator가 반환한 기사 수
            "new_count":  int,         # DB에 새로 저장된 기사 수
            "error":      str | None,  # 에러 메시지
        }
    """
    pref_profile = load_preference_profile()
    if pref_profile:
        print(f"[Pipeline] 선호도 프로파일 로드 — {pref_profile.get('summary', '')}")

    exclude_urls = db.get_todays_posted_urls()

    try:
        raw_articles = curator.research(
            count,
            exclude_urls,
            pref_profile or {},
        )
    except Exception as e:
        print(f"[Pipeline] curator.research() 실패: {e}")
        return {"articles": [], "raw_count": 0, "new_count": 0, "error": str(e)}

    if not raw_articles:
        return {"articles": [], "raw_count": 0, "new_count": 0, "error": None}

    new_count = 0
    for a in raw_articles:
        is_new = db.upsert_article(
            {
                "url": a.url,
                "title": a.title,
                "source": a.source,
                "description": a.description,
                "author": a.author,
                "image_url": "",
                "published_at": a.published_at,
                "platform_score": a.platform_score,
                "keywords": a.keywords if isinstance(a.keywords, list) else [],
            }
        )
        if is_new:
            new_count += 1

    print(f"[Pipeline] 큐레이션 완료 — {len(raw_articles)}개 수집 ({new_count}개 신규)")

    pending = db.get_pending_articles(limit=count + 10)
    ranked = rank_articles(pending)[:count]

    return {
        "articles": ranked,
        "raw_count": len(raw_articles),
        "new_count": new_count,
        "error": None,
    }
