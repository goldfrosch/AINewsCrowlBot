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
