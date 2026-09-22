"""article_fetch.py — 요청 URL 정규화와 리다이렉트 처리

실측 회귀: `canonicalize_url`이 후행 슬래시를 떼는데 `fetch_html`이 리다이렉트
홉마다 다시 canonicalize해서, 슬래시 있는 경로를 정본으로 쓰는 사이트
(Django·WordPress·Ghost)가 **무한 301 루프**에 빠져 전부 fetch 실패했다.
simonwillison.net 같은 최고 품질 실무자 블로그가 구조적으로 전멸했다.
"""

from __future__ import annotations

import httpx2
import pytest

from article_fetch import canonicalize_url, fetch_page, request_url


class _Response:
    def __init__(self, status_code: int, headers: dict, body: bytes = b""):
        self.status_code = status_code
        self.headers = headers
        self._body = body
        self.encoding = "utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        yield self._body


class _Client:
    """요청 URL을 기록하고 미리 정한 응답을 돌려주는 스텁."""

    def __init__(self, routes: dict[str, _Response]):
        self.routes = routes
        self.requested: list[str] = []

    def stream(self, _method: str, url: str, **_kwargs):
        self.requested.append(url)
        if url not in self.routes:
            raise httpx2.HTTPError(f"no route for {url}")
        return self.routes[url]


_HTML = b"<html lang='en'><body><article>" + (b"content " * 200) + b"</article></body></html>"


def test_request_url_preserves_trailing_slash() -> None:
    assert request_url("https://example.com/post/") == "https://example.com/post/"
    assert canonicalize_url("https://example.com/post/") == "https://example.com/post"


def test_request_url_still_strips_tracking_params() -> None:
    assert request_url("https://example.com/post/?utm_source=x&id=1") == "https://example.com/post/?id=1"


def test_fetch_follows_trailing_slash_redirect_once(mocker) -> None:
    """정본이 슬래시 경로인 사이트를 한 홉 만에 받아와야 한다."""
    mocker.patch("article_fetch._public_host", return_value=True)
    client = _Client(
        {
            "https://example.com/post": _Response(301, {"location": "/post/"}),
            "https://example.com/post/": _Response(200, {"content-type": "text/html"}, _HTML),
        }
    )

    html, reason = fetch_page(client, "https://example.com/post")

    assert reason == "ok"
    assert html is not None
    assert client.requested == ["https://example.com/post", "https://example.com/post/"]


def test_fetch_detects_redirect_cycle(mocker) -> None:
    mocker.patch("article_fetch._public_host", return_value=True)
    client = _Client(
        {
            "https://example.com/a": _Response(302, {"location": "/b"}),
            "https://example.com/b": _Response(302, {"location": "/a"}),
        }
    )

    html, reason = fetch_page(client, "https://example.com/a")

    assert html is None
    assert reason == "redirect_loop"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(403, "http_403"), (404, "http_404"), (500, "http_500")],
)
def test_fetch_reports_http_status(mocker, status: int, expected: str) -> None:
    """차단(403)과 타임아웃을 구분해야 어느 게이트를 손볼지 판단할 수 있다."""
    mocker.patch("article_fetch._public_host", return_value=True)
    client = _Client({"https://example.com/x": _Response(status, {})})

    assert fetch_page(client, "https://example.com/x") == (None, expected)


def test_fetch_reports_non_html(mocker) -> None:
    mocker.patch("article_fetch._public_host", return_value=True)
    client = _Client({"https://example.com/x": _Response(200, {"content-type": "application/pdf"})})

    assert fetch_page(client, "https://example.com/x") == (None, "not_html")


def test_fetch_reports_dns_failure(mocker) -> None:
    mocker.patch("article_fetch._public_host", return_value=False)
    client = _Client({})

    assert fetch_page(client, "https://nonexistent.invalid/x") == (None, "dns_failed")
