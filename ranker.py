"""
기사 순위 계산 및 피드백 처리

순위 공식:
  final_score = quality × bounded_preference × recency_nudge × game_client_nudge

- platform_score: 모든 프로듀서(Claude 웹 검색 / HN / RSS)가 0~100 밴드로 emit → 0~1 정규화
- source_multiplier: 👍/👎 누적 기반, 기본 1.0 (범위 0.1~5.0)
- keyword_multiplier: AI 태깅 키워드가 우선, 없으면 제목/설명에서 추출
- recency_multiplier: 발행일이 신선할수록 가점 (발행일 미상은 약한 감점)

소스별 정규화 상한(HackerNews 1500 / YouTube 5M)은 제거했다. 그 상한은
원시 플랫폼 지표(업보트·조회수)를 emit하는 크롤러를 전제했으나 그런 크롤러는
존재하지 않았고, 프로듀서가 여러 개로 늘어난 지금은 동일한 platform_score가
소스에 따라 33배까지 다르게 평가되는 왜곡만 남는다.
"""

import json

import database as db
import recency
from config import AI_KEYWORDS, PLATFORM_SCORE_BAND_MAX

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


def _normalize(score: float) -> float:
    """platform_score(0~100 밴드)를 0~1로 정규화한다."""
    if PLATFORM_SCORE_BAND_MAX <= 0:
        return 0.0
    return min(max(score, 0.0) / PLATFORM_SCORE_BAND_MAX, 1.0)


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
    기사 목록을 선호도·신선도 기반으로 정렬하고 final_score를 갱신한 뒤 반환.
    DB에도 점수를 저장해 !more 명령어가 올바른 순서를 유지하도록 함.
    """
    prefs = db.get_all_preferences()
    source_mult = {p["source"]: p["multiplier"] for p in prefs["sources"]}
    keyword_mult = {p["keyword"]: p["multiplier"] for p in prefs["keywords"]}

    for a in articles:
        base = _normalize(a.get("platform_score", 0.0))
        src_m = source_mult.get(a.get("source", ""), 1.0)

        # DB의 키워드는 정규형(언더스코어)이므로 조회 전에 표기를 통일한다.
        kws = [db.canonical_keyword(kw) for kw in _get_article_keywords(a)]
        kws = [kw for kw in kws if kw]
        kw_m = sum(keyword_mult.get(kw, 1.0) for kw in kws) / len(kws) if kws else 1.0
        preference = max(0.85, min(1.15, (src_m + kw_m) / 2))
        recency_nudge = 1 + (recency.recency_multiplier(a.get("published_at")) - 1) * 0.15
        game_client_nudge = 1.04 if "game_client" in kws else 1.0

        a["final_score"] = round(base * preference * recency_nudge * game_client_nudge, 6)

    articles.sort(key=lambda a: a["final_score"], reverse=True)
    db.update_final_scores(articles)
    return articles


def learnable_keywords(article: dict) -> list[str]:
    """선호도 학습에 반영할 키워드만 추린다.

    모델이 태깅한 키워드는 통제된 어휘이므로 그대로 신뢰한다.
    반면 태깅이 없어 제목·설명에서 추출해야 하는 경우에는 AI_KEYWORDS
    화이트리스트로 제한한다. 무제한 토큰화가 `'이유**'`, `'**선정'`,
    `'into'`, `'covering'` 같은 잔재를 선호도 상위로 밀어올렸기 때문이다.
    """
    stored = article.get("keywords", [])
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except (json.JSONDecodeError, TypeError):
            stored = []
    if isinstance(stored, list) and stored:
        return [str(kw).strip() for kw in stored if str(kw).strip()]

    text = f"{article.get('title', '')} {article.get('description', '')}".lower()
    return [kw.replace(" ", "_") for kw in AI_KEYWORDS if kw in text]


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

    for kw in set(learnable_keywords(article)):
        db.update_keyword_preference(kw, liked)

    return True
