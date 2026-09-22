"""Safe, bounded HTTP retrieval for article pages."""

from __future__ import annotations

import ipaddress
import socket
from typing import Final
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx2

_MAX_HTML_BYTES: Final = 250_000
_REDIRECT_LIMIT: Final = 3
# 봇 UA("AINewsCrawlBot/1.0")는 CDN·WAF에서 광범위하게 차단된다. 실측(URL 30개)에서
# 30%가 fetch 단계에서 탈락했고 InfoQ·simonwillison.net·Medium이 전부 여기 걸렸다.
# 하루 수십 페이지만 받는 리더이므로 일반 브라우저와 동일한 헤더를 보낸다.
_REQUEST_HEADERS: Final = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
}
_SOCKET_OPTIONS: Final = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]
_TRACKING_KEYS: Final = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
_PAPER_DOMAINS: Final = {
    "arxiv.org",
    "doi.org",
    "researchgate.net",
    "semanticscholar.org",
    "openreview.net",
}


def _normalize(url: str, *, strip_trailing_slash: bool) -> str:
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = host
    if port and not ((parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80)):
        netloc = f"{host}:{port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
    ]
    path = parsed.path
    if strip_trailing_slash:
        path = path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path or "/", urlencode(sorted(query)), ""))


def canonicalize_url(url: str) -> str:
    """Normalize an article URL into a stable identity key for dedup and storage."""
    return _normalize(url, strip_trailing_slash=True)


def request_url(url: str) -> str:
    """Normalize an article URL for an HTTP request, preserving the path verbatim.

    `canonicalize_url`은 후행 슬래시를 떼므로 요청 URL로 쓰면 안 된다. Django·WordPress·
    Ghost처럼 슬래시 있는 경로를 정본으로 삼는 사이트는 슬래시 없는 경로에 301을 주는데,
    리다이렉트마다 다시 canonicalize하면 슬래시가 또 떨어져 **같은 URL로 무한 반복**한다.
    실측: simonwillison.net이 4홉을 모두 301로 소진하고 fetch 실패로 폐기됐다.
    """
    return _normalize(url, strip_trailing_slash=False)


def _domain_matches(host: str, domains: set[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def supported_article_url(url: str) -> bool:
    """Return whether a URL can represent a non-paper public web article."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme in {"http", "https"}
        and bool(host)
        and not _domain_matches(host, _PAPER_DOMAINS)
        and not parsed.path.lower().endswith(".pdf")
    )


def _public_host(url: str) -> bool:
    host = urlsplit(url).hostname
    if not host:
        return False
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError):
        return False
    return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)


def create_http_client() -> httpx2.Client:
    """Create the tuned client used for manually validated redirects."""
    limits = httpx2.Limits(max_connections=200, max_keepalive_connections=40, keepalive_expiry=30.0)
    # 후보를 병렬로 받으므로 느린 한 페이지가 전체를 잡아먹지 않도록 read를 조인다.
    timeout = httpx2.Timeout(connect=5.0, read=15.0, write=10.0, pool=10.0)
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=3,
        limits=limits,
        socket_options=_SOCKET_OPTIONS,
    )
    return httpx2.Client(transport=transport, timeout=timeout, follow_redirects=False)


def fetch_page(client: httpx2.Client, url: str) -> tuple[str | None, str]:
    """Fetch one public HTML page and report why it failed when it does.

    Returns:
        `(html, reason)` — 성공하면 `(본문, "ok")`, 실패하면 `(None, 사유)`.
        사유를 돌려주는 이유: 기존 `fetch_html`은 None만 반환해서 차단·타임아웃·
        리다이렉트 루프를 구분할 수 없었고, 0건 원인 규명이 불가능했다.
    """
    try:
        current = request_url(url)
    except ValueError:
        return None, "bad_url"

    seen: set[str] = set()
    for _ in range(_REDIRECT_LIMIT + 1):
        if current in seen:
            return None, "redirect_loop"
        seen.add(current)
        try:
            if not supported_article_url(current):
                return None, "unsupported_url"
            if not _public_host(current):
                return None, "dns_failed"
            with client.stream("GET", current, headers=_REQUEST_HEADERS) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        return None, "redirect_no_location"
                    current = request_url(urljoin(current, location))
                    continue
                if response.status_code >= 400:
                    return None, f"http_{response.status_code}"
                content_type = response.headers.get("content-type", "").lower()
                if content_type and "html" not in content_type:
                    return None, "not_html"
                payload = bytearray()
                for chunk in response.iter_bytes():
                    remaining = _MAX_HTML_BYTES - len(payload)
                    if remaining <= 0:
                        break
                    payload.extend(chunk[:remaining])
                encoding = response.encoding or "utf-8"
                return bytes(payload).decode(encoding, errors="replace"), "ok"
        except httpx2.TimeoutException:
            return None, "timeout"
        except (httpx2.HTTPError, ValueError):
            return None, "connection_error"
    return None, "too_many_redirects"


def fetch_html(client: httpx2.Client, url: str) -> str | None:
    """Fetch one public HTML page with bounded redirects and response size."""
    return fetch_page(client, url)[0]
