"""연속 운영 시뮬레이션용 가짜 세계.

외부 경계 **3곳만** 대체하고 나머지는 실제 코드를 그대로 돌린다:

  - `claude_search.search_articles`   → 웹 검색 (유한한 기사 공급을 모델링)
  - `article_fetch.fetch_page`        → 페이지 HTTP 수신
  - `editorial_review._request_review` → 편집 심사 API 호출

따라서 필라 팬아웃, 톱업 트리거, 신선도 판정, 근중복/주제중복 제거, 완화 루프,
저수지, 랭킹, 다양성 상한, DB 중복 제약은 **전부 진짜 코드가 실행된다**.
0건은 이 로직들의 상호작용에서 나오므로, 검증해야 할 대상이 바로 여기다.

수율 파라미터는 2026-09-23 라이브 실행 실측값에 맞췄다:
  검색 24 요청 → 18 수집 → 본문검증 9 → 심사통과 7 → 게시 6
"""

from __future__ import annotations

import json
import random
import re
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx2

import claude_search

# 라이브 실측 기반 수율 (2026-09-23)
FETCH_FAILURE_RATE = 0.15  # 403·타임아웃·5xx
THIN_BODY_RATE = 0.10  # 본문 600자 미만
UNDATED_RATE = 0.30  # 모델이 발행일을 확정하지 못하는 비율
OUT_OF_WINDOW_RATE = 0.25  # 날짜를 모른 채 창 밖 기사를 끌어오는 비율
QUALITY_MEAN = 76.0
QUALITY_STDEV = 10.0
# 슬래시 경로를 정본으로 쓰는 사이트 비율 (WordPress·Ghost·Django).
# 실무자 블로그에 특히 흔하다. 이 값이 곧 리다이렉트 버그의 손실 규모가 된다.
SLASH_CANONICAL_RATE = 0.45

_FETCH_REASONS = ("http_403", "timeout", "http_500")
_PILLAR_DOMAINS = {
    "ai_practice": ["practice{n}.dev", "engblog{n}.io", "newsletter{n}.com"],
    "ai_game": ["gamedev{n}.blog", "indie{n}.itch.io", "studio{n}.net"],
    "graphics_3d": ["gfx{n}.art", "3dtools{n}.com", "shader{n}.dev"],
}
_PILLAR_TOPICS = {
    "ai_practice": ["agent harness", "context budget", "eval pipeline", "codemod", "prompt cache"],
    "ai_game": ["texture pipeline", "voxel rig", "sound layer", "jam postmortem", "ui atlas"],
    "graphics_3d": ["retopology", "pbr bake", "image to 3d", "shader intro", "lod budget"],
}
# 제목 변주. 합성 제목이 전부 같은 꼴이면 근중복 판정(Jaccard 0.55)이 과도하게
# 걸려 심사 후보가 인위적으로 줄어든다. 실제 기사 제목만큼 흩어 놓는다.
_TITLE_SHAPES = (
    "{topic} in production: {word} notes from {word2}",
    "how we cut {word} cost with {topic}",
    "{word} {topic} walkthrough for {word2} teams",
    "what {word2} taught us about {topic}",
    "{topic}: a {word} postmortem",
    "building {word2} tooling around {topic}",
)
_WORDS = ("latency", "budget", "rollout", "migration", "eval", "cache", "batch", "tracing", "pipeline", "review")
_WORDS2 = ("indie", "platform", "infra", "gameplay", "runtime", "editor", "shader", "asset")
_CONTENT_TYPE = {
    "ai_practice": "ai_programming",
    "ai_game": "game_asset_workflow",
    "graphics_3d": "graphics_3d_resource",
}


@dataclass
class SimArticle:
    url: str
    title: str
    source: str
    pillar: str
    published: date
    quality: float
    fetch_reason: str  # "ok" 또는 실패 사유
    body_chars: int
    slash_canonical: bool  # 슬래시 경로를 정본으로 쓰는 사이트 (WordPress·Ghost·Django)


class _SimResponse:
    """httpx 응답 흉내. `fetch_page`가 쓰는 표면만 구현한다."""

    def __init__(self, status_code: int, headers: dict, body: bytes = b""):
        self.status_code = status_code
        self.headers = headers
        self.encoding = "utf-8"
        self._body = body

    def __enter__(self) -> _SimResponse:
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def iter_bytes(self):
        yield self._body


class _SimClient:
    def __init__(self, world: SimWorld):
        self._world = world

    def __enter__(self) -> _SimClient:
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def close(self) -> None:
        return None

    def stream(self, _method: str, url: str, **_kwargs) -> _SimResponse:
        return self._world.respond(url)


@dataclass
class SimWorld:
    """하루에 유한한 수의 기사만 새로 생기는 세계."""

    start: date
    daily_supply: dict[str, int]
    seed: int = 20260923
    today: date = field(init=False)
    corpus: list[SimArticle] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=lambda: {"searches": 0, "reviews": 0, "fetches": 0})
    _counter: int = 0

    def __post_init__(self) -> None:
        self.today = self.start
        self._rng = random.Random(self.seed)
        self._index: dict[str, SimArticle] = {}
        # 시작 시점에 이미 존재하는 과거 기사 (에버그린·최근 발행분)
        for back in range(180, 0, -1):
            self._emit(self.start - timedelta(days=back))

    # ── 세계 진행 ────────────────────────────────────────────────────────────
    def advance(self, day_index: int) -> None:
        self.today = self.start + timedelta(days=day_index)
        self._emit(self.today)

    def _emit(self, when: date) -> None:
        for pillar, supply in self.daily_supply.items():
            for _ in range(supply):
                self._counter += 1
                index = self._counter
                domain = self._rng.choice(_PILLAR_DOMAINS[pillar]).format(n=index % 40)
                topic = self._rng.choice(_PILLAR_TOPICS[pillar])
                roll = self._rng.random()
                if roll < FETCH_FAILURE_RATE:
                    reason = self._rng.choice(_FETCH_REASONS)
                    body = 0
                elif roll < FETCH_FAILURE_RATE + THIN_BODY_RATE:
                    reason, body = "ok", 200
                else:
                    reason, body = "ok", self._rng.randint(1200, 30000)
                title = self._rng.choice(_TITLE_SHAPES).format(
                    topic=topic,
                    word=self._rng.choice(_WORDS),
                    word2=self._rng.choice(_WORDS2),
                )
                # WordPress·Ghost·Django 계열은 슬래시 경로를 정본으로 쓴다.
                # 실무자 블로그에 특히 흔해서 이 비율이 곧 손실 규모가 된다.
                slash = self._rng.random() < SLASH_CANONICAL_RATE
                path = f"https://{domain}/posts/{index}" + ("/" if slash else "")
                article = SimArticle(
                    url=path,
                    title=f"{title} ({index})",
                    source=domain,
                    pillar=pillar,
                    published=when,
                    quality=max(20.0, min(95.0, self._rng.gauss(QUALITY_MEAN, QUALITY_STDEV))),
                    fetch_reason=reason,
                    slash_canonical=slash,
                    body_chars=body,
                )
                self.corpus.append(article)
                self._index[article.url] = article

    # ── 경계 1: 웹 검색 ──────────────────────────────────────────────────────
    def search_articles(self, _client, *, prompt: str, system_blocks=None, caller: str = "", **_kw):
        self.stats["searches"] += 1
        pillar = self._pillar_of(caller)
        want = self._requested_count(prompt)
        cutoff = self._cutoff(prompt)
        skip = set(re.findall(r"^- (https://\S+)$", prompt, re.MULTILINE))

        in_window = [a for a in self.corpus if a.pillar == pillar and a.published >= cutoff and a.url not in skip]
        in_window.sort(key=lambda a: a.published, reverse=True)
        older = [a for a in self.corpus if a.pillar == pillar and a.published < cutoff and a.url not in skip]
        self._rng.shuffle(older)

        picked: list[SimArticle] = []
        for candidate in in_window:
            if len(picked) >= want:
                break
            picked.append(candidate)
        # 날짜를 확정 못 한 채 창 밖 기사를 끌어오는 실제 행태를 재현한다.
        stray = int(want * OUT_OF_WINDOW_RATE)
        picked.extend(older[:stray])

        articles = []
        for candidate in picked:
            undated = self._rng.random() < UNDATED_RATE or candidate.published < cutoff
            articles.append(
                {
                    "url": candidate.url,
                    "title": candidate.title,
                    "source": candidate.source,
                    "description": "simulated description",
                    "author": "",
                    "published_at": "" if undated else candidate.published.isoformat(),
                    "curator_reason": "simulated",
                    "keywords": ["llm"],
                }
            )
        return claude_search.SearchOutcome(articles=articles, stop_reason="end_turn")

    # ── 경계 2: HTTP 전송 ────────────────────────────────────────────────────
    # `fetch_page`를 통째로 스텁하면 URL 정규화·리다이렉트 추적 코드가 경로에서
    # 빠져 정작 검증해야 할 버그를 못 잡는다. 전송 계층만 가짜로 두고 진짜
    # `fetch_page`가 돌게 한다.
    def http_client(self):
        return _SimClient(self)

    def respond(self, url: str) -> _SimResponse:
        self.stats["fetches"] += 1
        found = self._by_url(url)
        if found is None:
            # 슬래시 정본 사이트는 슬래시 없는 경로에 301을 돌려준다.
            # 요청 URL에서 슬래시를 떼고 보내면 여기서 영원히 돌게 된다.
            canonical = self._index.get(url + "/") if not url.endswith("/") else None
            if canonical is not None and canonical.slash_canonical:
                return _SimResponse(301, {"location": canonical.url})
            return _SimResponse(404, {})
        if found.fetch_reason == "http_403":
            return _SimResponse(403, {})
        if found.fetch_reason == "http_500":
            return _SimResponse(500, {})
        if found.fetch_reason == "timeout":
            raise httpx2.TimeoutException("simulated timeout")
        body = "content word " * max(1, found.body_chars // 13)
        html = (
            f'<html lang="en"><head>'
            f'<meta property="article:published_time" content="{found.published.isoformat()}">'
            f"</head><body><article>{body}</article></body></html>"
        )
        return _SimResponse(200, {"content-type": "text/html; charset=utf-8"}, html.encode("utf-8"))

    # ── 경계 3: 편집 심사 ────────────────────────────────────────────────────
    def request_review(self, _client, prompt: str, _max_tokens: int, _caller: str):
        self.stats["reviews"] += 1
        payload = json.loads(prompt[prompt.index("CANDIDATES:\n") + len("CANDIDATES:\n") :])
        decisions = []
        for item in payload:
            # 심사 단계는 canonical_url(슬래시 제거형)을 넘겨준다.
            found = self._by_storage_key(item["url"])
            quality = found.quality if found else 30.0
            pillar = found.pillar if found else "ai_practice"
            decisions.append(
                {
                    "url": item["url"],
                    "verdict": "KEEP",
                    "quality_score": round(quality),
                    "title_ko": f"한국어 제목 {item['url'][-6:]}",
                    "summary_ko": "시뮬레이션 요약입니다.",
                    "why_it_matters_ko": "시뮬레이션 근거입니다.",
                    "content_type": _CONTENT_TYPE[pillar],
                    "engines": ["Cross-engine"],
                    "game_client_relevance": 70,
                    "keywords": ["llm"],
                    "rejection_reason": "",
                }
            )
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(decisions, ensure_ascii=False))],
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        )

    # ── 헬퍼 ────────────────────────────────────────────────────────────────
    def _by_url(self, url: str) -> SimArticle | None:
        """정확히 일치하는 URL만 찾는다. 슬래시 유무가 곧 검증 대상이므로 흡수하면 안 된다."""
        return self._index.get(url)

    def _by_storage_key(self, url: str) -> SimArticle | None:
        """저장 키(슬래시 제거형)로 찾는다. 심사 단계는 canonical_url을 넘겨준다."""
        return self._index.get(url) or self._index.get(url.rstrip("/") + "/")

    @staticmethod
    def _pillar_of(caller: str) -> str:
        for pillar in _PILLAR_DOMAINS:
            if pillar in caller:
                return pillar
        return "ai_practice"

    @staticmethod
    def _requested_count(prompt: str) -> int:
        match = re.search(r"^Find (\d+) ", prompt, re.MULTILINE)
        return int(match.group(1)) if match else 6

    @staticmethod
    def _cutoff(prompt: str) -> date:
        match = re.search(r"it is before (\d{4}-\d{2}-\d{2})", prompt)
        return date.fromisoformat(match.group(1)) if match else date(2000, 1, 1)


@contextmanager
def installed(world: SimWorld, *, legacy: bool = False):
    """세 경계를 시뮬레이터로 교체한다. 나머지 코드는 진짜 그대로 돈다.

    `legacy=True`면 이번 작업 이전의 상수값으로 되돌려 대조군을 만든다.
    각 값은 git 이전 리비전의 실제 설정이며, 0건 발생률 비교의 기준선이 된다.
    """
    import agents.agent_spec as spec_mod
    import agents.news_curation_agent as agent_mod
    import article_fetch
    import article_quality
    import curator
    import editorial_review
    import pipeline
    import recency

    patches = [
        # 시뮬레이션 날짜가 곧 '오늘'이어야 신선도 판정이 날마다 제대로 움직인다.
        patch.object(recency, "today", lambda: world.today),
        patch.object(agent_mod.claude_search, "search_articles", world.search_articles),
        # HTTP 전송만 가짜다. `fetch_page`의 URL 정규화·리다이렉트 추적은 진짜가 돈다.
        patch.object(article_quality, "create_http_client", world.http_client),
        patch.object(article_fetch, "_public_host", lambda _url: True),
        patch.object(editorial_review, "_request_review", world.request_review),
        patch.object(editorial_review, "ANTHROPIC_API_KEY", "sim-key"),
        patch.object(agent_mod, "ANTHROPIC_API_KEY", "sim-key"),
        patch.object(curator, "ANTHROPIC_API_KEY", "sim-key"),
        # HN·RSS는 실제 네트워크를 타므로 시뮬레이션에서는 끈다.
        patch.object(pipeline.feed_pool, "collect", lambda *_a, **_k: []),
        patch.object(pipeline, "load_preference_profile", lambda *_a, **_k: None),
    ]

    if legacy:
        patches += [
            # 변경 전 `fetch_html`은 요청 URL도 canonicalize해 후행 슬래시를 뗐다.
            # 슬래시 정본 사이트가 301을 돌려주면 홉마다 다시 떼며 무한 루프에 빠진다.
            patch.object(article_fetch, "request_url", article_fetch.canonicalize_url),
            # 신선도 7일 단일 창, 완화 패스 없음
            patch.object(recency, "RECENCY_MAX_AGE_DAYS", 7),
            patch.object(pipeline, "RECENCY_RELAXATION_DAYS", (7,)),
            patch.object(pipeline, "CONTENT_TYPE_MAX_AGE_DAYS", dict.fromkeys(_CONTENT_TYPE.values(), 7)),
            # 품질 컷 완화 없음
            patch.object(editorial_review, "_RELAXATION_THRESHOLDS", ((62.0, 70.0),)),
            # 필라 없음 → 검색 호출 1회가 전 토픽을 담당
            patch.object(spec_mod, "PILLARS", {}),
            # 오버페치 8~24, 목표 수량만 채우면 톱업 종료
            patch.object(agent_mod, "OVERFETCH_MIN", 8),
            patch.object(agent_mod, "OVERFETCH_MAX", 24),
            patch.object(agent_mod, "CANDIDATES_PER_PUBLISHED", 1.0),
            patch.object(agent_mod, "TOPUP_MAX_ROUNDS", 2),
            # 소스·주제 다양성 상한 없음
            patch.object(pipeline, "MAX_PER_SOURCE_IN_POST", 99),
            patch.object(pipeline, "MAX_PER_TOPIC_IN_POST", 99),
        ]

    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        yield
