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


def canonicalize_url(url: str) -> str:
    """Normalize an article URL and remove common tracking parameters."""
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
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, urlencode(sorted(query)), ""))


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
    timeout = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=3,
        limits=limits,
        socket_options=_SOCKET_OPTIONS,
    )
    return httpx2.Client(transport=transport, timeout=timeout, follow_redirects=False)


def fetch_html(client: httpx2.Client, url: str) -> str | None:
    """Fetch one public HTML page with bounded redirects and response size."""
    try:
        current = canonicalize_url(url)
    except ValueError:
        return None
    for _ in range(_REDIRECT_LIMIT + 1):
        try:
            if not supported_article_url(current) or not _public_host(current):
                return None
            with client.stream("GET", current, headers=_REQUEST_HEADERS) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        return None
                    current = canonicalize_url(urljoin(current, location))
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type and "html" not in content_type:
                    return None
                payload = bytearray()
                for chunk in response.iter_bytes():
                    remaining = _MAX_HTML_BYTES - len(payload)
                    if remaining <= 0:
                        break
                    payload.extend(chunk[:remaining])
                encoding = response.encoding or "utf-8"
                return bytes(payload).decode(encoding, errors="replace")
        except (httpx2.HTTPError, ValueError):
            return None
    return None
