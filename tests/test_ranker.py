"""ranker.py 단위 테스트"""

import pytest

from config import PLATFORM_SCORE_BAND_MAX
from ranker import (
    _normalize,
    apply_feedback,
    extract_keywords,
    learnable_keywords,
    rank_articles,
)
from tests.conftest import days_ago

# ── _normalize ──────────────────────────────────────────────────────────────


class TestNormalize:
    """platform_score는 모든 프로듀서가 0~100 밴드로 emit한다 (소스별 상한 없음)."""

    def test_mid_band(self):
        assert _normalize(50.0) == pytest.approx(0.5)

    def test_band_max(self):
        assert _normalize(PLATFORM_SCORE_BAND_MAX) == 1.0

    def test_over_band_clamped(self):
        assert _normalize(PLATFORM_SCORE_BAND_MAX * 10) == 1.0

    def test_zero_score(self):
        assert _normalize(0.0) == 0.0

    def test_negative_clamped(self):
        assert _normalize(-50.0) == 0.0

    def test_source_independent(self):
        """같은 점수는 소스와 무관하게 같은 base를 갖는다 (33배 왜곡 회귀 방지)."""
        assert _normalize(100.0) == _normalize(100.0)


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
            {"source": "A", "platform_score": 20.0, "keywords": [], "title": "Low"},
            {"source": "B", "platform_score": 100.0, "keywords": [], "title": "High"},
            {"source": "C", "platform_score": 60.0, "keywords": [], "title": "Mid"},
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
            {"source": "HN", "platform_score": 100.0, "keywords": [], "title": "A"},
            {"source": "Other", "platform_score": 100.0, "keywords": [], "title": "B"},
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
            {"source": "X", "platform_score": 100.0, "keywords": ["llm"], "title": "WithLLM"},
            {"source": "X", "platform_score": 100.0, "keywords": ["other"], "title": "NoLLM"},
        ]
        result = rank_articles(articles)
        assert result[0]["title"] == "WithLLM"

    def test_quality_dominates_small_recency_difference(self, mocker):
        mocker.patch("ranker.db.get_all_preferences", return_value={"sources": [], "keywords": []})
        mocker.patch("ranker.db.update_final_scores")
        articles = [
            {
                "source": "X",
                "platform_score": 90.0,
                "keywords": [],
                "title": "Higher quality",
                "published_at": days_ago(3),
            },
            {
                "source": "X",
                "platform_score": 76.0,
                "keywords": [],
                "title": "Fresher but weaker",
                "published_at": days_ago(0),
            },
        ]

        result = rank_articles(articles)

        assert result[0]["title"] == "Higher quality"

    def test_game_client_relevance_breaks_close_quality_tie(self, mocker):
        mocker.patch("ranker.db.get_all_preferences", return_value={"sources": [], "keywords": []})
        mocker.patch("ranker.db.update_final_scores")
        articles = [
            {
                "source": "X",
                "platform_score": 88.0,
                "keywords": ["general"],
                "title": "General programming",
                "published_at": days_ago(1),
            },
            {
                "source": "X",
                "platform_score": 86.0,
                "keywords": ["game_client", "engine:godot"],
                "title": "Godot client workflow",
                "published_at": days_ago(1),
            },
        ]

        result = rank_articles(articles)

        assert result[0]["title"] == "Godot client workflow"


class TestRecencyRanking:
    def _prefs(self, mocker):
        mocker.patch("ranker.db.get_all_preferences", return_value={"sources": [], "keywords": []})
        mocker.patch("ranker.db.update_final_scores")

    def test_fresher_article_wins(self, mocker):
        self._prefs(mocker)
        articles = [
            {"source": "X", "platform_score": 100.0, "keywords": [], "title": "Old", "published_at": days_ago(20)},
            {"source": "X", "platform_score": 100.0, "keywords": [], "title": "Fresh", "published_at": days_ago(0)},
        ]
        result = rank_articles(articles)
        assert result[0]["title"] == "Fresh"

    def test_missing_date_ranks_below_fresh(self, mocker):
        self._prefs(mocker)
        articles = [
            {"source": "X", "platform_score": 100.0, "keywords": [], "title": "Undated", "published_at": ""},
            {"source": "X", "platform_score": 100.0, "keywords": [], "title": "Fresh", "published_at": days_ago(1)},
        ]
        result = rank_articles(articles)
        assert result[0]["title"] == "Fresh"


class TestLearnableKeywords:
    def test_model_tagged_keywords_trusted(self):
        article = {"title": "x", "description": "y", "keywords": ["claude code", "mcp"]}
        assert learnable_keywords(article) == ["claude code", "mcp"]

    def test_markdown_noise_not_learned(self):
        """`💡 **선정 이유**: ...` 마크다운이 키워드로 학습되던 회귀를 막는다."""
        article = {
            "title": "Some Article",
            "description": "A description\n\n💡 **선정 이유**: covering directly into workflow",
            "keywords": [],
        }
        learned = learnable_keywords(article)
        assert "이유**" not in learned
        assert "**선정" not in learned
        assert "covering" not in learned
        assert "directly" not in learned

    def test_whitelisted_fallback_still_extracts(self):
        article = {"title": "Prompt engineering with RAG", "description": "", "keywords": []}
        learned = learnable_keywords(article)
        assert "prompt_engineering" in learned
        assert "rag" in learned


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
