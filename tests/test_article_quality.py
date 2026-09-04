from __future__ import annotations

import pytest

from article_quality import canonicalize_url, is_near_duplicate, verify_articles, verify_html
from crawlers.base import Article
from tests.conftest import days_ago


def _article(url: str, title: str = "Practical AI workflow") -> Article:
    return Article(
        url=url,
        title=title,
        source="Test Source",
        description="A practical workflow with code, steps, and measured results.",
        author="Engineer",
        published_at=days_ago(0),
        platform_score=100.0,
        keywords=["ai coding"],
    )


def _html(*, language: str, published_at: str, body: str) -> str:
    return f"""
    <html lang="{language}">
      <head>
        <meta property="article:published_time" content="{published_at}" />
        <title>Practical AI workflow</title>
      </head>
      <body><article>{body}</article></body>
    </html>
    """


def test_verify_html_rejects_chinese_content() -> None:
    article = _article("https://example.cn/guide", "使用人工智能开发游戏")
    body = "这是一个面向游戏开发者的完整实践教程，包含代码、工具设置和项目导入步骤。" * 40

    result = verify_html(article, _html(language="zh-CN", published_at=days_ago(0), body=body), 7)

    assert result is None


def test_verify_html_accepts_english_content_from_chinese_company() -> None:
    article = _article("https://www.alibabacloud.com/blog/practical-ai-coding-workflow")
    body = (
        "This hands-on guide shows ordinary programmers how to review, test, and refactor game client code "
        "with an AI coding assistant. It includes commands, code examples, failure cases, and engine integration. "
    ) * 12

    result = verify_html(article, _html(language="en", published_at=days_ago(0), body=body), 7)

    assert result is not None
    assert result.language == "en"


def test_verify_html_rejects_academic_paper_url() -> None:
    article = _article("https://arxiv.org/abs/2609.12345")
    body = "A long academic paper about a model architecture and benchmark results. " * 30

    result = verify_html(article, _html(language="en", published_at=days_ago(0), body=body), 7)

    assert result is None


def test_verify_html_rejects_stale_page_date_despite_fresh_claim() -> None:
    article = _article("https://example.com/2025/old-guide")
    body = "A practical programming tutorial with implementation details and code examples. " * 30

    result = verify_html(article, _html(language="en", published_at=days_ago(30), body=body), 7)

    assert result is None


def test_canonicalize_url_removes_tracking_and_fragment() -> None:
    url = "https://Example.com/guide/?utm_source=newsletter&ref=home&id=42#section"

    assert canonicalize_url(url) == "https://example.com/guide?id=42"


def test_near_duplicate_detects_repeated_methodology() -> None:
    title = "Claude Code hooks, skills and subagents for production workflows"
    previous = ["Production Claude Code workflow with hooks, subagents, and skills"]

    assert is_near_duplicate(title, previous)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:99999/post",
        "http://[::1",
        f"https://{'a' * 70}.com/post",
    ],
)
def test_verify_articles_skips_malformed_url(url: str) -> None:
    assert verify_articles([_article(url)], max_age_days=7) == []
