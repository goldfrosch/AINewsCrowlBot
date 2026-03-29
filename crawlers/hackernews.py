"""
Hacker News 크롤러
- 공개 Firebase API 사용 (인증 불필요)
- 상위 200개 스토리 중 AI 관련 글만 필터링
"""
import requests
from datetime import datetime, timedelta

from .base import Article
from config import AI_KEYWORDS, MAX_PER_SOURCE

_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
_TIMEOUT = 8


def _is_ai(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in AI_KEYWORDS)


def crawl() -> list[Article]:
    articles: list[Article] = []
    cutoff = datetime.now() - timedelta(days=2)
    cutoff_ts = int(cutoff.timestamp())

    try:
        ids = requests.get(_TOP_URL, timeout=_TIMEOUT).json()[:200]
    except Exception as e:
        print(f"[HackerNews] 목록 로딩 실패: {e}")
        return articles

    for story_id in ids:
        if len(articles) >= MAX_PER_SOURCE:
            break
        try:
            item = requests.get(_ITEM_URL.format(story_id), timeout=_TIMEOUT).json()
        except Exception:
            continue

        if not item or item.get("type") != "story":
            continue
        if not item.get("url"):
            continue
        if item.get("time", 0) < cutoff_ts:
            continue
        if not _is_ai(item.get("title", "")):
            continue

        articles.append(Article(
            url=item["url"],
            title=item["title"],
            source="HackerNews",
            author=item.get("by", ""),
            published_at=datetime.fromtimestamp(item["time"]).isoformat(),
            platform_score=float(item.get("score", 0)),
        ))

    print(f"[HackerNews] {len(articles)}개 수집")
    return articles
