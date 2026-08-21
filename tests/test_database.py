"""database.py 단위 테스트 (임시 SQLite 사용)"""

import database as db


class TestInitDb:
    def test_creates_tables(self, tmp_db):
        import sqlite3

        conn = sqlite3.connect(str(tmp_db))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "articles" in tables
        assert "keywords" in tables
        assert "article_keywords" in tables
        assert "source_preferences" in tables


class TestUpsertArticle:
    def test_new_article(self, tmp_db):
        result = db.upsert_article(
            {
                "url": "https://example.com/1",
                "title": "Test Article",
                "source": "TestSource",
                "description": "desc",
                "author": "Author",
                "image_url": "",
                "published_at": "2026-04-01",
                "platform_score": 100.0,
                "keywords": ["llm", "gpt-4"],
            }
        )
        assert result is True

    def test_duplicate_url(self, tmp_db):
        article = {
            "url": "https://example.com/dup",
            "title": "Dup",
            "source": "Test",
            "description": "",
            "author": "",
            "image_url": "",
            "published_at": "",
            "platform_score": 0,
            "keywords": [],
        }
        db.upsert_article(article)
        result = db.upsert_article(article)
        assert result is False

    def test_keywords_linked(self, tmp_db):
        db.upsert_article(
            {
                "url": "https://example.com/kw",
                "title": "KW Test",
                "source": "Test",
                "description": "",
                "author": "",
                "image_url": "",
                "published_at": "",
                "platform_score": 0,
                "keywords": ["rag", "embedding"],
            }
        )
        prefs = db.get_all_preferences()
        kw_names = {k["keyword"] for k in prefs["keywords"]}
        assert "rag" in kw_names
        assert "embedding" in kw_names


class TestPendingArticles:
    def test_returns_pending(self, tmp_db):
        for i in range(3):
            db.upsert_article(
                {
                    "url": f"https://example.com/pending-{i}",
                    "title": f"Article {i}",
                    "source": "Test",
                    "description": "",
                    "author": "",
                    "image_url": "",
                    "published_at": "",
                    "platform_score": float(i * 100),
                    "keywords": [],
                }
            )
        pending = db.get_pending_articles(limit=10)
        assert len(pending) == 3
        assert all(a["status"] == "pending" for a in pending)

    def test_excludes_posted(self, tmp_db):
        db.upsert_article(
            {
                "url": "https://example.com/posted",
                "title": "Posted",
                "source": "Test",
                "description": "",
                "author": "",
                "image_url": "",
                "published_at": "",
                "platform_score": 100,
                "keywords": [],
            }
        )
        pending = db.get_pending_articles(limit=10)
        article = pending[0]
        db.mark_as_posted(article["id"], "msg123", "ch123")

        pending_after = db.get_pending_articles(limit=10)
        assert all(a["id"] != article["id"] for a in pending_after)


class TestMarkAsPosted:
    def test_status_and_message_id(self, tmp_db):
        db.upsert_article(
            {
                "url": "https://example.com/mark",
                "title": "Mark Test",
                "source": "Test",
                "description": "",
                "author": "",
                "image_url": "",
                "published_at": "",
                "platform_score": 50,
                "keywords": [],
            }
        )
        article = db.get_pending_articles(limit=1)[0]
        db.mark_as_posted(article["id"], "discord_msg_1", "channel_1")

        found = db.get_article_by_message_id("discord_msg_1")
        assert found is not None
        assert found["status"] == "posted"
        assert found["discord_message_id"] == "discord_msg_1"


class TestPreferences:
    def test_source_like_increases(self, tmp_db):
        db.update_source_preference("TestSource", liked=True)
        prefs = db.get_all_preferences()
        src = next(s for s in prefs["sources"] if s["source"] == "TestSource")
        assert src["multiplier"] > 1.0

    def test_source_dislike_decreases(self, tmp_db):
        db.update_source_preference("TestSource", liked=False)
        prefs = db.get_all_preferences()
        src = next(s for s in prefs["sources"] if s["source"] == "TestSource")
        assert src["multiplier"] < 1.0

    def test_source_clamp_max(self, tmp_db):
        for _ in range(100):
            db.update_source_preference("SpamSource", liked=True)
        prefs = db.get_all_preferences()
        src = next(s for s in prefs["sources"] if s["source"] == "SpamSource")
        assert src["multiplier"] <= 5.0

    def test_source_clamp_min(self, tmp_db):
        for _ in range(100):
            db.update_source_preference("HateSource", liked=False)
        prefs = db.get_all_preferences()
        src = next(s for s in prefs["sources"] if s["source"] == "HateSource")
        assert src["multiplier"] >= 0.1

    def test_keyword_preference(self, tmp_db):
        db.update_keyword_preference("llm", liked=True)
        prefs = db.get_all_preferences()
        kw = next(k for k in prefs["keywords"] if k["keyword"] == "llm")
        assert kw["multiplier"] > 1.0

    def test_reset_preferences(self, tmp_db):
        db.update_source_preference("ResetSource", liked=True)
        db.update_keyword_preference("reset_kw", liked=True)
        db.reset_preferences()

        prefs = db.get_all_preferences()
        for s in prefs["sources"]:
            assert s["multiplier"] == 1.0
        for k in prefs["keywords"]:
            assert k["multiplier"] == 1.0


class TestGetTodaysPostedUrls:
    def test_returns_today_posted(self, tmp_db):
        db.upsert_article(
            {
                "url": "https://example.com/today",
                "title": "Today",
                "source": "Test",
                "description": "",
                "author": "",
                "image_url": "",
                "published_at": "",
                "platform_score": 100,
                "keywords": [],
            }
        )
        article = db.get_pending_articles(limit=1)[0]
        db.mark_as_posted(article["id"], "msg_today", "ch_today")

        urls = db.get_todays_posted_urls()
        assert "https://example.com/today" in urls

    def test_excludes_pending(self, tmp_db):
        db.upsert_article(
            {
                "url": "https://example.com/still-pending",
                "title": "Pending",
                "source": "Test",
                "description": "",
                "author": "",
                "image_url": "",
                "published_at": "",
                "platform_score": 100,
                "keywords": [],
            }
        )
        urls = db.get_todays_posted_urls()
        assert "https://example.com/still-pending" not in urls


class TestGetStats:
    def test_counts(self, tmp_db, sample_articles):
        for a in sample_articles:
            db.upsert_article(a)
        stats = db.get_stats()
        assert stats["total"] == 3
        assert stats["pending"] == 3
        assert stats["posted"] == 0


def _insert(url: str, title: str = "T", source: str = "Test", keywords=None) -> int:
    db.upsert_article(
        {
            "url": url,
            "title": title,
            "source": source,
            "description": "",
            "author": "",
            "image_url": "",
            "published_at": "",
            "platform_score": 100,
            "keywords": keywords or [],
        }
    )
    return next(a["id"] for a in db.get_pending_articles(limit=50) if a["url"] == url)


def _backdate_post(url: str, days: int) -> None:
    """posted_at을 과거로 옮겨 lookback 경계를 테스트한다."""
    with db._db() as conn:
        conn.execute(
            "UPDATE articles SET posted_at = datetime('now', '+9 hours', ?) WHERE url = ?",
            (f"-{days} days", url),
        )


class TestGetRecentPostedUrls:
    """오늘 게시분만 보던 로직이 '하루 1건/0건' 문제의 최대 원인이었다."""

    def test_includes_articles_posted_days_ago(self, tmp_db):
        article_id = _insert("https://example.com/older")
        db.mark_as_posted(article_id, "msg", "ch")
        _backdate_post("https://example.com/older", 5)

        assert "https://example.com/older" in db.get_recent_posted_urls(days=45)
        # 기존 함수는 같은 기사를 놓친다 → 재추천 → UNIQUE 제약으로 조용히 폐기
        assert "https://example.com/older" not in db.get_todays_posted_urls()

    def test_excludes_beyond_lookback(self, tmp_db):
        article_id = _insert("https://example.com/ancient")
        db.mark_as_posted(article_id, "msg", "ch")
        _backdate_post("https://example.com/ancient", 100)

        assert "https://example.com/ancient" not in db.get_recent_posted_urls(days=45)

    def test_excludes_pending(self, tmp_db):
        _insert("https://example.com/pending-only")
        assert "https://example.com/pending-only" not in db.get_recent_posted_urls()

    def test_empty_db(self, tmp_db):
        assert db.get_recent_posted_urls() == []


class TestGetAllArticleUrls:
    def test_includes_pending_and_posted(self, tmp_db):
        pending_id = _insert("https://example.com/p")
        posted_id = _insert("https://example.com/q")
        db.mark_as_posted(posted_id, "msg", "ch")
        assert pending_id != posted_id

        urls = db.get_all_article_urls()
        assert urls == {"https://example.com/p", "https://example.com/q"}


class TestCleanupNoiseKeywords:
    """실측: keywords 테이블 622행 중 상위가 `'이유**'`, `'**선정'` 같은 마크다운 잔재."""

    def test_removes_unlinked_non_ai_keywords(self, tmp_db):
        db.update_keyword_preference("이유**", liked=True)
        db.update_keyword_preference("covering", liked=True)

        removed = db.cleanup_noise_keywords()

        names = {k["keyword"] for k in db.get_all_preferences()["keywords"]}
        assert removed == 2
        assert "이유**" not in names
        assert "covering" not in names

    def test_keeps_whitelisted_ai_keywords(self, tmp_db):
        db.update_keyword_preference("claude", liked=True)
        db.update_keyword_preference("prompt_engineering", liked=True)

        db.cleanup_noise_keywords()

        names = {k["keyword"] for k in db.get_all_preferences()["keywords"]}
        assert "claude" in names
        assert "prompt_engineering" in names

    def test_keeps_keywords_linked_to_articles(self, tmp_db):
        """모델이 태깅한 키워드는 화이트리스트 밖이어도 보존한다."""
        _insert("https://example.com/tagged", keywords=["some-curated-topic"])

        db.cleanup_noise_keywords()

        names = {k["keyword"] for k in db.get_all_preferences()["keywords"]}
        assert "some-curated-topic" in names

    def test_idempotent(self, tmp_db):
        db.update_keyword_preference("into", liked=True)
        assert db.cleanup_noise_keywords() == 1
        assert db.cleanup_noise_keywords() == 0


class TestCanonicalKeyword:
    def test_spaces_become_underscores(self):
        assert db.canonical_keyword("ai agent") == "ai_agent"

    def test_lowercased_and_stripped(self):
        assert db.canonical_keyword("  Prompt Engineering ") == "prompt_engineering"

    def test_hyphens_preserved(self):
        """`fine-tuning`, `gpt-4`는 하이픈이 원래 표기의 일부다."""
        assert db.canonical_keyword("fine-tuning") == "fine-tuning"
        assert db.canonical_keyword("GPT-4") == "gpt-4"

    def test_non_string_is_empty(self):
        assert db.canonical_keyword(None) == ""
        assert db.canonical_keyword(42) == ""


class TestMergeKeywordVariants:
    """`ai agent`와 `ai_agent`가 따로 쌓여 👍/👎 신호가 반으로 갈렸다."""

    def test_variants_merged_with_summed_counts(self, tmp_db):
        with db._db() as conn:
            conn.execute(
                "INSERT INTO keywords (keyword, multiplier, total_likes, total_dislikes) VALUES ('ai agent', 1.05, 1, 0)"
            )
            conn.execute(
                "INSERT INTO keywords (keyword, multiplier, total_likes, total_dislikes) VALUES ('ai_agent', 1.05, 2, 1)"
            )

        merged = db.merge_keyword_variants()

        rows = {k["keyword"]: k for k in db.get_all_preferences()["keywords"]}
        assert merged == 1
        assert "ai agent" not in rows
        assert rows["ai_agent"]["total_likes"] == 3
        assert rows["ai_agent"]["total_dislikes"] == 1

    def test_preference_writes_are_canonical(self, tmp_db):
        db.update_keyword_preference("ai agent", liked=True)
        db.update_keyword_preference("ai_agent", liked=True)

        rows = {k["keyword"]: k for k in db.get_all_preferences()["keywords"]}
        assert set(rows) == {"ai_agent"}
        assert rows["ai_agent"]["total_likes"] == 2

    def test_article_keywords_are_canonical(self, tmp_db):
        _insert("https://example.com/kw", keywords=["Prompt Engineering", "MCP"])

        names = {k["keyword"] for k in db.get_all_preferences()["keywords"]}
        assert names == {"prompt_engineering", "mcp"}

    def test_no_variants_is_noop(self, tmp_db):
        db.update_keyword_preference("llm", liked=True)
        assert db.merge_keyword_variants() == 0

    def test_idempotent(self, tmp_db):
        with db._db() as conn:
            conn.execute("INSERT INTO keywords (keyword, total_likes) VALUES ('ai agent', 1)")
            conn.execute("INSERT INTO keywords (keyword, total_likes) VALUES ('ai_agent', 1)")
        assert db.merge_keyword_variants() == 1
        assert db.merge_keyword_variants() == 0
