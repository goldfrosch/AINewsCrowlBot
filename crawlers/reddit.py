"""
Reddit 크롤러
- 인증 없이 공개 JSON API 사용 (기본)
- REDDIT_CLIENT_ID / SECRET이 있으면 OAuth API로 자동 전환
"""
import requests
from datetime import datetime

from .base import Article
from config import REDDIT_SUBREDDITS, REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT, MAX_PER_SOURCE

_TIMEOUT = 10
_HEADERS = {"User-Agent": REDDIT_USER_AGENT}


def _get_oauth_token() -> str:
    resp = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        headers=_HEADERS,
        timeout=_TIMEOUT,
    )
    return resp.json().get("access_token", "")


def crawl() -> list[Article]:
    articles: list[Article] = []

    # OAuth 사용 가능하면 헤더에 토큰 추가
    headers = dict(_HEADERS)
    base_url = "https://www.reddit.com"
    if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
        try:
            token = _get_oauth_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
                base_url = "https://oauth.reddit.com"
        except Exception as e:
            print(f"[Reddit] OAuth 실패, 비인증 API 사용: {e}")

    for sub in REDDIT_SUBREDDITS:
        try:
            url = f"{base_url}/r/{sub}/top.json?t=day&limit=25"
            resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            posts = resp.json().get("data", {}).get("children", [])
        except Exception as e:
            print(f"[Reddit r/{sub}] 실패: {e}")
            continue

        count = 0
        for post in posts:
            if count >= MAX_PER_SOURCE:
                break
            p = post.get("data", {})

            # 외부 링크 vs. 셀프 포스트 구분
            link = p.get("url", "")
            if not link or "reddit.com/r/" in link:
                link = f"https://reddit.com{p.get('permalink', '')}"

            description = ""
            if p.get("selftext") and len(p["selftext"]) > 10:
                description = p["selftext"][:300]

            articles.append(Article(
                url=link,
                title=p.get("title", ""),
                source=f"Reddit r/{sub}",
                author=p.get("author", ""),
                published_at=datetime.fromtimestamp(p.get("created_utc", 0)).isoformat(),
                platform_score=float(p.get("score", 0)),
                description=description,
            ))
            count += 1

    print(f"[Reddit] {len(articles)}개 수집")
    return articles
