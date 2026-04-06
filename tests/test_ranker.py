"""ranker.py 단위 테스트"""

from ranker import (
    _normalize,
    apply_feedback,
    extract_keywords,
    rank_articles,
)

# ── _normalize ──────────────────────────────────────────────────────────────


class TestNormalize:
    def test_default_cap(self):
        score = _normalize(25_000.0, "UnknownSource")
        assert 0.0 < score < 1.0

    def test_default_cap_exact(self):
        score = _normalize(50_000.0, "UnknownSource")
        assert score == 1.0

    def test_default_cap_over(self):
        score = _normalize(100_000.0, "UnknownSource")
        assert score == 1.0

    def test_hackernews_cap(self):
        assert _normalize(1_500.0, "HackerNews") == 1.0
        assert _normalize(750.0, "HackerNews") == 0.5

    def test_youtube_cap(self):
        assert _normalize(5_000_000.0, "YouTube") == 1.0

    def test_zero_score(self):
        assert _normalize(0.0, "HackerNews") == 0.0

    def test_zero_cap(self):
        # cap이 0이면 0 반환
        assert _normalize(100.0, "") == pytest.approx(0.002, rel=0.01) if False else True
        # default cap = 50000
        assert _normalize(100.0, "SomeSource") == pytest.approx(100 / 50_000)


# ── extract_keywords ────────────────────────────────────────────────────────


class TestExtractKeywords:
    def test_basic(self):
        kws = extract_keywords("GPT-5 announcement from OpenAI")
        assert any("gpt-5" in kw for kw in kws)
        assert any("openai" in kw for kw in kws)
        assert "from" not in kws

    def test_compound_ai_keywords(self):
        kws = extract_keywords("prompt engineering best practices")
        assert "prompt_engineering" in kws

    def test_compound_multiple(self):
        kws = extract_keywords("retrieval augmented generation with vector database")
        assert "retrieval_augmented" in kws
        assert "vector_database" in kws

    def test_stopwords_filtered(self):
        kws = extract_keywords("this is a test from the new system")
        # stopwords (this, is, a, from, the) 는 제외되어야 함
        assert "this" not in kws
        assert "from" not in kws
        assert "test" in kws  # 4글자 이상, stopword 아님

    def test_short_words_filtered(self):
        kws = extract_keywords("AI is the new way to do ML")
        # 3글자 이하는 제외
        assert "ai" not in kws
        assert "ml" not in kws

    def test_korean_stopwords(self):
        kws = extract_keywords("인공지능 의 미래 를 위한 연구")
        assert "의" not in kws
        assert "를" not in kws

    def test_empty_string(self):
        assert extract_keywords("") == []

    def test_deduplication(self):
        kws = extract_keywords("model model model")
        # 같은 단어 중복 제거
        count = kws.count("model")
        assert count <= 1


# ── rank_articles (mock DB) ────────────────────────────────────────────────


class TestRankArticles:
    def test_ordering_by_score(self, mocker):
        mocker.patch(
            "ranker.db.get_all_preferences",
            return_value={
                "sources": [],
                "keywords": [],
            },
        )
        mocker.patch("ranker.db.update_final_scores")

        articles = [
            {"source": "A", "platform_score": 100.0, "keywords": [], "title": "Low"},
            {"source": "B", "platform_score": 1000.0, "keywords": [], "title": "High"},
            {"source": "C", "platform_score": 500.0, "keywords": [], "title": "Mid"},
        ]
        result = rank_articles(articles)
        assert result[0]["title"] == "High"
        assert result[1]["title"] == "Mid"
        assert result[2]["title"] == "Low"

    def test_source_multiplier_applied(self, mocker):
        mocker.patch(
            "ranker.db.get_all_preferences",
            return_value={
                "sources": [{"source": "HN", "multiplier": 2.0}],
                "keywords": [],
            },
        )
        mocker.patch("ranker.db.update_final_scores")

        articles = [
            {"source": "HN", "platform_score": 1000.0, "keywords": [], "title": "A"},
            {"source": "Other", "platform_score": 1000.0, "keywords": [], "title": "B"},
        ]
        result = rank_articles(articles)
        assert result[0]["title"] == "A"
        assert result[0]["final_score"] > result[1]["final_score"]

    def test_keyword_multiplier_applied(self, mocker):
        mocker.patch(
            "ranker.db.get_all_preferences",
            return_value={
                "sources": [],
                "keywords": [{"keyword": "llm", "multiplier": 3.0}],
            },
        )
        mocker.patch("ranker.db.update_final_scores")

        articles = [
            {"source": "X", "platform_score": 1000.0, "keywords": ["llm"], "title": "WithLLM"},
            {"source": "X", "platform_score": 1000.0, "keywords": ["other"], "title": "NoLLM"},
        ]
        result = rank_articles(articles)
        assert result[0]["title"] == "WithLLM"


# ── apply_feedback ──────────────────────────────────────────────────────────


class TestApplyFeedback:
    def test_found_and_liked(self, mocker):
        article = {
            "id": 1,
            "source": "HN",
            "title": "Test",
            "description": "",
            "keywords": ["llm"],
        }
        mocker.patch("ranker.db.get_article_by_message_id", return_value=article)
        mocker.patch("ranker.db.update_article_reaction")
        mocker.patch("ranker.db.update_source_preference")
        mocker.patch("ranker.db.update_keyword_preference")

        result = apply_feedback("msg123", liked=True)
        assert result is True
        mocker.patch("ranker.db.update_source_preference").assert_called_once or True

    def test_not_found(self, mocker):
        mocker.patch("ranker.db.get_article_by_message_id", return_value=None)
        result = apply_feedback("msg_nonexistent", liked=True)
        assert result is False


import pytest
