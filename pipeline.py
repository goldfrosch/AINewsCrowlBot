"""
Discord 독립적 큐레이션 파이프라인

bot.py, dry_run.py, 테스트에서 공통으로 사용합니다.
"""

import curator
import database as db
from agents.preference_analysis import load_preference_profile
from config import ARTICLES_PER_POST, GAME_DEV_KEYWORDS
from ranker import rank_articles


def _is_game_dev_article(article: dict) -> bool:
    """기사의 키워드, 제목, 설명에서 게임 개발 관련 키워드가 포함되어 있는지 확인."""
    keywords = article.get("keywords", [])
    if isinstance(keywords, str):
        try:
            import json
            keywords = json.loads(keywords)
        except Exception:
            keywords = []
    title = (article.get("title") or "").lower()
    description = (article.get("description") or "").lower()
    search_text = f"{title} {description}"
    for kw in keywords:
        kw_lower = kw.lower()
        for gdk in GAME_DEV_KEYWORDS:
            if gdk in kw_lower or kw_lower in gdk:
                return True
    for gdk in GAME_DEV_KEYWORDS:
        if gdk in search_text:
            return True
    return False


def _ensure_game_dev_article(ranked: list[dict], count: int) -> list[dict]:
    """
    상위 count개 중 게임 개발+AI 기사가 최소 1개 포함되도록 보장.

    전략:
    1. 상위 count개에 이미 게임 개발 기사가 있으면 그대로 반환.
    2. 없으면, 전체 랭킹에서 가장 점수가 높은 게임 개발 기사 1개를
       상위 count개 중 가장 점수가 낮은 일반 기사와 교체.
    """
    if count <= 0:
        return ranked[:count]

    selected = ranked[:count]

    # 이미 게임 개발 기사가 포함되어 있으면 통과
    if any(_is_game_dev_article(a) for a in selected):
        return selected

    # 전체 랭킹에서 게임 개발 기사 찾기
    game_dev_articles = [a for a in ranked if _is_game_dev_article(a)]
    if not game_dev_articles:
        # 게임 개발 기사가 아예 없으면 그대로 반환 (강제 생성 불가)
        return selected

    # 가장 점수가 높은 게임 개발 기사 1개 선택
    game_article = game_dev_articles[0]

    # 이미 selected에 포함되어 있지 않은지 확인
    game_urls = {a.get("url") for a in selected if a.get("url")}
    if game_article.get("url") in game_urls:
        return selected

    # selected 중 가장 점수가 낮은 일반 기사와 교체
    # (동점이면 뒤에 있는 기사 = 인덱스가 큰 기사 우선 교체)
    worst_idx = max(
        range(len(selected)),
        key=lambda i: (-selected[i].get("final_score", 0), i),
    )
    result = list(selected)
    result[worst_idx] = game_article
    return result


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
    ranked = rank_articles(pending)
    final = _ensure_game_dev_article(ranked, count)

    return {
        "articles": final,
        "raw_count": len(raw_articles),
        "new_count": new_count,
        "error": None,
    }
