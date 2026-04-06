"""
기사 순위 계산 및 피드백 처리

순위 공식:
  final_score = normalize(platform_score) × source_multiplier × avg(keyword_multipliers)

- platform_score: 소스별 플랫폼 점수(HN points, Reddit upvotes, YouTube views 등)를 0~1로 정규화
- source_multiplier: 👍/👎 누적 기반, 기본 1.0 (범위 0.1~5.0)
- keyword_multiplier: AI 태깅 키워드가 우선, 없으면 제목/설명에서 추출
"""

import json

import database as db
from config import AI_KEYWORDS

# 소스별 점수 정규화 상한선 (이 값을 100%로 봄)
_SCORE_CAP: dict[str, float] = {
    "HackerNews": 1_500.0,
    "YouTube": 5_000_000.0,
    "default": 50_000.0,
}

# 불용어 (키워드 추출 시 제외)
_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "this",
    "that",
    "it",
    "not",
    "use",
    "new",
    "can",
    "also",
    "how",
    "what",
    "why",
    "when",
    "where",
    "which",
    "who",
    "all",
    "just",
    # 한국어 빈출 조사/어미 (단순 토크나이저 보완)
    "의",
    "을",
    "를",
    "이",
    "가",
    "은",
    "는",
    "에",
    "에서",
    "로",
    "으로",
    "와",
    "과",
    "도",
    "만",
    "서",
    "한",
    "및",
    "등",
}


def _normalize(score: float, source: str) -> float:
    cap = _SCORE_CAP.get(source, _SCORE_CAP["default"])
    return min(score / cap, 1.0) if cap > 0 else 0.0


def extract_keywords(text: str) -> list[str]:
    """제목+설명에서 의미있는 단어를 추출. AI 복합 키워드는 언더스코어로 합쳐 보존."""
    result: list[str] = []

    # 복합 AI 키워드 먼저 체크 (공백 포함)
    text_lower = text.lower()
    for kw in AI_KEYWORDS:
        if " " in kw and kw in text_lower:
            result.append(kw.replace(" ", "_"))

    # 단어 단위 토크나이징
    for word in text_lower.split():
        word = word.strip(".,!?;:\"'()[]{}<>")
        if len(word) > 3 and word not in _STOPWORDS:
            result.append(word)

    return list(dict.fromkeys(result))  # 순서 유지하며 중복 제거


def _get_article_keywords(article: dict) -> list[str]:
    """DB에 AI 태깅된 keywords가 있으면 사용, 없으면 제목/설명에서 추출.
    DB에서 반환된 기사는 keywords가 list[str]이고,
    인메모리 Article 객체도 list이므로 JSON 파싱은 폴백으로만 사용."""
    stored = article.get("keywords", [])
    if isinstance(stored, list):
        return stored if stored else extract_keywords(article.get("title", "") + " " + article.get("description", ""))
    if isinstance(stored, str):
        try:
            parsed = json.loads(stored)
            return (
                parsed if parsed else extract_keywords(article.get("title", "") + " " + article.get("description", ""))
            )
        except (json.JSONDecodeError, TypeError):
            pass
    return extract_keywords(article.get("title", "") + " " + article.get("description", ""))


def rank_articles(articles: list[dict]) -> list[dict]:
    """
    기사 목록을 선호도 기반으로 정렬하고 final_score를 갱신한 뒤 반환.
    DB에도 점수를 저장해 !more 명령어가 올바른 순서를 유지하도록 함.
    """
    prefs = db.get_all_preferences()
    source_mult = {p["source"]: p["multiplier"] for p in prefs["sources"]}
    keyword_mult = {p["keyword"]: p["multiplier"] for p in prefs["keywords"]}

    for a in articles:
        base = _normalize(a.get("platform_score", 0.0), a.get("source", ""))
        src_m = source_mult.get(a.get("source", ""), 1.0)

        kws = _get_article_keywords(a)
        kw_m = sum(keyword_mult.get(kw, 1.0) for kw in kws) / len(kws) if kws else 1.0

        a["final_score"] = round(base * src_m * kw_m, 6)

    articles.sort(key=lambda a: a["final_score"], reverse=True)
    db.update_final_scores(articles)
    return articles


def apply_feedback(message_id: str, liked: bool) -> bool:
    """
    Discord 반응(👍/👎)을 받아 DB 선호도를 업데이트.
    해당 message_id의 기사가 없으면 False 반환.
    """
    article = db.get_article_by_message_id(message_id)
    if not article:
        return False

    db.update_article_reaction(article["id"], liked)
    db.update_source_preference(article["source"], liked)

    keywords = _get_article_keywords(article)
    for kw in set(keywords):
        db.update_keyword_preference(kw, liked)

    return True
