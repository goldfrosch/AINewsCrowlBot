"""
모든 크롤러를 통합 실행하는 진입점.
각 크롤러는 독립적으로 실패해도 나머지는 계속 실행됩니다.
"""
from .base import Article
from . import hackernews, reddit, youtube, rss, web_scrapers


def crawl_all() -> list[Article]:
    """모든 소스를 크롤링하고 Article 목록으로 반환."""
    results: list[Article] = []

    runners = [
        ("HackerNews",  hackernews.crawl),
        ("Reddit",      reddit.crawl),
        ("YouTube",     youtube.crawl),
        ("RSS",         rss.crawl),
        ("Threads",     web_scrapers.crawl_threads),
        ("LinkedIn",    web_scrapers.crawl_linkedin),
    ]

    for name, fn in runners:
        try:
            batch = fn()
            results.extend(batch)
        except Exception as e:
            print(f"[{name}] 크롤러 예외: {e}")

    print(f"[전체] 총 {len(results)}개 기사 수집 완료")
    return results
