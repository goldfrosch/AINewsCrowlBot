"""
Threads / LinkedIn 크롤러

[Threads]
Meta의 공식 Threads Graph API를 사용합니다.
- 사전 요구사항: Meta Developer 앱 생성 + threads_basic 스코프 OAuth 인증
- 가이드: https://developers.facebook.com/docs/threads/get-started
- .env에 THREADS_ACCESS_TOKEN을 설정하지 않으면 스킵됩니다.

[LinkedIn]
LinkedIn은 공개 게시물 검색 API를 외부에 제공하지 않습니다.
- 공식 Marketing API는 인가된 파트너사 전용입니다.
- 브라우저 쿠키(li_at)를 이용한 비공식 스크래핑을 시도하지만,
  JavaScript 렌더링 필요 및 Rate Limit으로 결과가 보장되지 않습니다.
- .env에 LINKEDIN_LI_AT를 설정하지 않으면 스킵됩니다.
"""
import requests
from bs4 import BeautifulSoup

from .base import Article
from config import AI_KEYWORDS, THREADS_ACCESS_TOKEN, LINKEDIN_LI_AT, MAX_PER_SOURCE

_TIMEOUT = 10
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _is_ai(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in AI_KEYWORDS)


# ─── Threads ─────────────────────────────────────────────────────────────────

def crawl_threads() -> list[Article]:
    if not THREADS_ACCESS_TOKEN:
        print("[Threads] THREADS_ACCESS_TOKEN 없음 → 스킵")
        return []

    articles: list[Article] = []
    try:
        # Threads Graph API: 인증된 사용자의 피드 조회
        # 주의: 현재 Threads API는 키워드 검색을 지원하지 않아
        #       팔로잉 피드를 가져온 뒤 AI 키워드로 클라이언트 측 필터링합니다.
        resp = requests.get(
            "https://graph.threads.net/v1.0/me/threads",
            params={
                "fields": "id,text,timestamp,permalink_url,media_type",
                "access_token": THREADS_ACCESS_TOKEN,
                "limit": 50,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()

        for post in resp.json().get("data", []):
            if len(articles) >= MAX_PER_SOURCE:
                break
            text = post.get("text", "") or ""
            if not _is_ai(text):
                continue

            post_id = post.get("id", "")
            permalink = post.get("permalink_url") or f"https://www.threads.net/t/{post_id}"

            articles.append(Article(
                url=permalink,
                title=(text[:97] + "...") if len(text) > 100 else text,
                source="Threads",
                description=text[:400],
                published_at=post.get("timestamp", ""),
                platform_score=0.0,
            ))
    except requests.HTTPError as e:
        print(f"[Threads] API 오류 {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        print(f"[Threads] 오류: {e}")

    print(f"[Threads] {len(articles)}개 수집")
    return articles


# ─── LinkedIn ─────────────────────────────────────────────────────────────────

def crawl_linkedin() -> list[Article]:
    if not LINKEDIN_LI_AT:
        print("[LinkedIn] LINKEDIN_LI_AT 없음 → 스킵")
        return []

    articles: list[Article] = []
    hashtags = ["artificialintelligence", "llm", "generativeai", "인공지능"]

    session = requests.Session()
    session.headers.update({
        "User-Agent": _BROWSER_UA,
        "Cookie": f"li_at={LINKEDIN_LI_AT}",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    })

    for tag in hashtags:
        if len(articles) >= MAX_PER_SOURCE:
            break
        try:
            url = f"https://www.linkedin.com/feed/hashtag/{tag}/"
            resp = session.get(url, timeout=_TIMEOUT)

            if resp.status_code in (401, 403, 999):
                # 999: LinkedIn의 봇 차단 코드
                print(f"[LinkedIn] 접근 차단 (HTTP {resp.status_code}) — 쿠키 만료 또는 봇 차단")
                break

            soup = BeautifulSoup(resp.text, "html.parser")

            # LinkedIn 피드는 CSR(Client-Side Rendering)이라 정적 파싱 한계 있음.
            # 서버 사이드 렌더링된 일부 요소만 파싱 시도.
            for card in soup.select("div.feed-shared-update-v2")[:5]:
                text_el = card.select_one("span[dir='ltr']")
                if not text_el:
                    continue
                text = text_el.get_text(" ", strip=True)
                if not _is_ai(text):
                    continue

                link_el = card.select_one("a[href*='/posts/']")
                post_url = (
                    "https://www.linkedin.com" + link_el["href"]
                    if link_el
                    else f"https://www.linkedin.com/feed/hashtag/{tag}/"
                )

                articles.append(Article(
                    url=post_url,
                    title=(text[:97] + "...") if len(text) > 100 else text,
                    source="LinkedIn",
                    description=text[:400],
                    platform_score=0.0,
                ))

        except Exception as e:
            print(f"[LinkedIn #{tag}] 오류: {e}")

    if not articles:
        print("[LinkedIn] 수집된 게시물 없음 (JavaScript 렌더링 한계 또는 접근 제한)")

    print(f"[LinkedIn] {len(articles)}개 수집")
    return articles
