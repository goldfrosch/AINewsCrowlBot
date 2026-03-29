"""
YouTube 크롤러
- YouTube Data API v3 사용 (YOUTUBE_API_KEY 필요)
- 키가 없으면 스킵
- 전일 게시된 AI 관련 영상을 조회수 기준으로 수집
"""
import requests
from datetime import datetime, timedelta

from .base import Article
from config import YOUTUBE_API_KEY, YOUTUBE_SEARCH_QUERIES, MAX_PER_SOURCE

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_TIMEOUT = 10


def _fetch_view_counts(video_ids: list[str]) -> dict[str, int]:
    """video id → 조회수 맵 반환"""
    if not video_ids:
        return {}
    try:
        resp = requests.get(
            _VIDEOS_URL,
            params={
                "part": "statistics",
                "id": ",".join(video_ids),
                "key": YOUTUBE_API_KEY,
            },
            timeout=_TIMEOUT,
        )
        counts = {}
        for item in resp.json().get("items", []):
            vid_id = item["id"]
            counts[vid_id] = int(
                item.get("statistics", {}).get("viewCount", 0)
            )
        return counts
    except Exception:
        return {}


def crawl() -> list[Article]:
    if not YOUTUBE_API_KEY:
        print("[YouTube] YOUTUBE_API_KEY 없음 → 스킵")
        return []

    articles: list[Article] = []
    seen: set[str] = set()
    published_after = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    all_video_ids: list[str] = []
    items_by_id: dict[str, dict] = {}

    for query in YOUTUBE_SEARCH_QUERIES:
        try:
            resp = requests.get(
                _SEARCH_URL,
                params={
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "order": "viewCount",
                    "publishedAfter": published_after,
                    "maxResults": 5,
                    "key": YOUTUBE_API_KEY,
                },
                timeout=_TIMEOUT,
            )
            for item in resp.json().get("items", []):
                vid_id = item["id"]["videoId"]
                if vid_id not in seen:
                    seen.add(vid_id)
                    all_video_ids.append(vid_id)
                    items_by_id[vid_id] = item["snippet"]
        except Exception as e:
            print(f"[YouTube] 쿼리 '{query}' 실패: {e}")

    # 조회수 일괄 조회 (API 쿼터 절약)
    view_counts = _fetch_view_counts(all_video_ids)

    for vid_id, snippet in items_by_id.items():
        if len(articles) >= MAX_PER_SOURCE:
            break
        articles.append(Article(
            url=f"https://www.youtube.com/watch?v={vid_id}",
            title=snippet.get("title", ""),
            source="YouTube",
            description=snippet.get("description", "")[:300],
            author=snippet.get("channelTitle", ""),
            image_url=snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            published_at=snippet.get("publishedAt", ""),
            platform_score=float(view_counts.get(vid_id, 0)),
        ))

    print(f"[YouTube] {len(articles)}개 수집")
    return articles
