"""
RSS/Atom 피드 크롤러
커버: VentureBeat AI, The Verge AI, ArXiv, Medium, ZDNet Korea, IT조선, Ars Technica
"""
import feedparser
from datetime import datetime, timedelta

from .base import Article
from config import RSS_FEEDS, RSS_NO_FILTER_SOURCES, AI_KEYWORDS, MAX_PER_SOURCE

_CUTOFF_DAYS = 2  # 최근 N일 이내 기사만 수집


def _is_ai(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in AI_KEYWORDS)


def _parse_pub_date(entry) -> tuple[datetime | None, str]:
    """feedparser entry에서 발행일 파싱. (datetime, iso_string) 반환."""
    raw = entry.get("published_parsed") or entry.get("updated_parsed")
    if not raw:
        return None, ""
    try:
        dt = datetime(*raw[:6])
        return dt, dt.isoformat()
    except Exception:
        return None, ""


def _get_thumbnail(entry) -> str:
    # media:thumbnail
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        return entry.media_thumbnail[0].get("url", "")
    # media:content (image)
    if hasattr(entry, "media_content"):
        for mc in entry.media_content:
            if mc.get("medium") == "image":
                return mc.get("url", "")
    # enclosure
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image"):
            return enc.get("url", "")
    return ""


def crawl() -> list[Article]:
    articles: list[Article] = []
    cutoff = datetime.now() - timedelta(days=_CUTOFF_DAYS)

    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            count = 0

            for entry in feed.entries:
                if count >= MAX_PER_SOURCE:
                    break

                title = entry.get("title", "").strip()
                url = entry.get("link", "").strip()
                if not title or not url:
                    continue

                description = entry.get("summary", "")
                # HTML 태그 간단 제거
                import re
                description = re.sub(r"<[^>]+>", " ", description).strip()
                description = " ".join(description.split())[:400]

                # AI 특화 소스가 아닌 경우 키워드 필터링
                if source_name not in RSS_NO_FILTER_SOURCES:
                    if not _is_ai(title + " " + description):
                        continue

                pub_dt, pub_str = _parse_pub_date(entry)
                if pub_dt and pub_dt < cutoff:
                    continue

                articles.append(Article(
                    url=url,
                    title=title,
                    source=source_name,
                    description=description,
                    author=entry.get("author", ""),
                    image_url=_get_thumbnail(entry),
                    published_at=pub_str,
                    platform_score=0.0,
                ))
                count += 1

        except Exception as e:
            print(f"[RSS {source_name}] 오류: {e}")

    print(f"[RSS] {len(articles)}개 수집")
    return articles
