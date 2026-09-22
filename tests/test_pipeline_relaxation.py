"""pipeline.py — 0건 방지용 점진 완화 루프와 선정 다양성

단일 패스 구조에서는 신선도·품질 게이트 중 하나만 어긋나도 그날 브리핑이
통째로 0건이 됐다(실측: 2026-06-15 이후 100일 연속 0건). 목표에 미달하면
신선도 창과 품질 컷을 한 단계씩 넓혀 다시 검색한다.
"""

from __future__ import annotations

import pytest

import pipeline
from article_quality import VerifiedArticle
from config import CONTENT_TYPE_MAX_AGE_DAYS, MAX_PER_SOURCE_IN_POST, RECENCY_RELAXATION_DAYS
from crawlers.base import Article
from tests.conftest import days_ago

_INACTIVE_INTENT = {"active": False, "recency_hours": None}


@pytest.fixture(autouse=True)
def no_network_feeds(mocker):
    return mocker.patch("pipeline.feed_pool.collect", return_value=[])


@pytest.fixture(autouse=True)
def stub_gates(mocker):
    def verify(articles, max_age_days, report=None):
        window = max_age_days if callable(max_age_days) else (lambda _a: max_age_days)
        kept = [a for a in articles if not pipeline.recency.is_stale(a.published_at, window(a))]
        if report is not None:
            report.update({"attempted": len(articles), "passed": len(kept), "reasons": {}})
        return [
            VerifiedArticle(
                article=a,
                canonical_url=a.url,
                language="en",
                published_at=a.published_at,
                excerpt="details " * 200,
                trusted_source=True,
            )
            for a in kept
        ]

    def review(candidates, report=None, relax_level=0):
        # platform_score를 품질 점수로 간주하고 완화 단계의 임계값을 적용한다.
        trusted_cut, _unknown = pipeline.editorial_review.thresholds(relax_level)
        kept = [c for c in candidates if c.article.platform_score >= trusted_cut]
        if report is not None:
            report.update({"candidates": len(candidates), "kept": len(kept), "reasons": {}})
        return [
            Article(
                url=c.canonical_url,
                title=c.article.title,
                source=c.article.source,
                description=c.article.description,
                published_at=c.published_at,
                platform_score=c.article.platform_score,
                keywords=[*c.article.keywords, "ai_programming"],
            )
            for c in kept
        ]

    mocker.patch("pipeline.article_quality.verify_articles", side_effect=verify)
    mocker.patch("pipeline.editorial_review.review_articles", side_effect=review)
    mocker.patch("pipeline.load_preference_profile", return_value=None)
    mocker.patch("pipeline.load_curation_intent", return_value=_INACTIVE_INTENT)


def _article(url, *, age_days=1, score=100.0, source="TestSource", keywords=None, pillar=""):
    return Article(
        url=url,
        title=f"Title {url}",
        source=source,
        description="d",
        published_at=days_ago(age_days),
        platform_score=score,
        keywords=keywords or [],
        pillar=pillar,
    )


class TestRelaxationLoop:
    def test_second_pass_widens_recency_window(self, mocker, tmp_db):
        """1패스(14일)에서 버려진 25일 전 기사를 2패스(30일)가 살린다."""
        mocker.patch(
            "pipeline.curator.research",
            side_effect=[[_article("https://a/old", age_days=25)], [_article("https://a/old", age_days=25)], []],
        )

        result = pipeline.run_curation_pipeline(count=1)

        assert [a["url"] for a in result["articles"]] == ["https://a/old"]
        assert result["max_age_days"] >= RECENCY_RELAXATION_DAYS[1]

    def test_second_pass_lowers_quality_threshold(self, mocker, tmp_db):
        """1패스 컷(62)에 걸린 60점 기사를 완화 단계가 통과시킨다."""
        candidate = [_article("https://a/mid", score=60.0)]
        mocker.patch("pipeline.curator.research", side_effect=[list(candidate), list(candidate), list(candidate)])

        result = pipeline.run_curation_pipeline(count=1)

        assert [a["url"] for a in result["articles"]] == ["https://a/mid"]

    def test_stops_as_soon_as_target_met(self, mocker, tmp_db):
        research = mocker.patch(
            "pipeline.curator.research",
            side_effect=[[_article("https://a/1"), _article("https://a/2")], [], []],
        )

        pipeline.run_curation_pipeline(count=2)

        assert research.call_count == 1

    def test_reservoir_is_used_before_searching(self, mocker, tmp_db):
        """전날 잉여가 남아 있으면 검색 없이 브리핑이 나가야 한다."""
        import database as db

        db.upsert_article(
            {
                "url": "https://a/leftover",
                "title": "Leftover",
                "source": "S",
                "description": "",
                "author": "",
                "image_url": "",
                "published_at": days_ago(1),
                "platform_score": 90.0,
                "keywords": ["ai_programming"],
            }
        )
        research = mocker.patch("pipeline.curator.research", return_value=[])

        result = pipeline.run_curation_pipeline(count=1)

        assert [a["url"] for a in result["articles"]] == ["https://a/leftover"]
        research.assert_not_called()

    def test_reports_each_pass(self, mocker, tmp_db):
        mocker.patch("pipeline.curator.research", side_effect=[[], [], []])

        result = pipeline.run_curation_pipeline(count=2)

        assert len(result["stages"]["passes"]) == len(RECENCY_RELAXATION_DAYS)

    def test_active_intent_window_is_not_widened(self, mocker, tmp_db):
        """사용자가 '최근 24시간'을 지정했으면 0건이 되더라도 넓히지 않는다."""
        mocker.patch(
            "pipeline.load_curation_intent",
            return_value={"active": True, "summary": "tight", "recency_hours": 24},
        )
        mocker.patch("pipeline.curator.research", side_effect=[[], [], []])

        result = pipeline.run_curation_pipeline(count=2)

        assert result["max_age_days"] == 1


class TestPerContentTypeWindow:
    def test_evergreen_graphics_resource_survives_practice_window(self, tmp_db):
        """그래픽스 학습자료는 2주 창에 걸리면 안 된다."""
        assert (
            pipeline._stored_window(["graphics_3d_resource"], 14) == CONTENT_TYPE_MAX_AGE_DAYS["graphics_3d_resource"]
        )

    def test_practice_article_keeps_tight_window(self, tmp_db):
        assert pipeline._stored_window(["ai_programming"], 14) == CONTENT_TYPE_MAX_AGE_DAYS["ai_programming"]

    def test_unclassified_falls_back_to_base(self, tmp_db):
        assert pipeline._stored_window(["llm"], 21) == 21

    def test_pillar_drives_collection_window(self, tmp_db):
        assert pipeline._article_window(_article("https://a/1", pillar="graphics_3d"), 14) > 14
        assert pipeline._article_window(_article("https://a/1"), 14) == 14


class TestDiversity:
    def test_caps_articles_per_source(self) -> None:
        ranked = [
            {"url": f"https://x/{i}", "source": "SameBlog", "title": f"Distinct subject {i}", "final_score": 1.0}
            for i in range(5)
        ]
        ranked += [{"url": "https://y/1", "source": "OtherBlog", "title": "Another matter", "final_score": 0.1}]

        picked = pipeline._diversify(ranked, 3)

        sources = [a["source"] for a in picked]
        assert sources.count("SameBlog") == MAX_PER_SOURCE_IN_POST
        assert "OtherBlog" in sources

    def test_caps_articles_per_topic_across_sources(self) -> None:
        """실측: 서로 다른 블로그 3곳의 'GPT-6 Astra' 글이 브리핑 6건 중 절반을 차지했다."""
        ranked = [
            {"url": "https://a/1", "source": "A", "title": "GPT-6 Astra + Codex로 3D 게임 만들기", "final_score": 0.9},
            {
                "url": "https://b/1",
                "source": "B",
                "title": "GPT-6 Astra로 비디오 게임 만들기 5단계",
                "final_score": 0.8,
            },
            {
                "url": "https://c/1",
                "source": "C",
                "title": "GPT-6 Astra 3D 게임 만들기 사례 총정리",
                "final_score": 0.7,
            },
            {"url": "https://d/1", "source": "D", "title": "Blender 리토폴로지 워크플로우", "final_score": 0.6},
        ]

        picked = pipeline._diversify(ranked, 3)

        assert [a["url"] for a in picked] == ["https://a/1", "https://b/1", "https://d/1"]

    def test_falls_back_to_capped_items_rather_than_returning_short(self) -> None:
        """다양성 때문에 브리핑이 비는 것은 본말전도다."""
        ranked = [{"url": f"https://x/{i}", "source": "SameBlog", "final_score": 1.0} for i in range(5)]

        assert len(pipeline._diversify(ranked, 4)) == 4
