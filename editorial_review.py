"""LLM editorial review and Korean brief generation for verified articles."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Final

import anthropic

import token_tracker
from agents.agent_spec import SKILL_REVIEWER
from article_quality import VerifiedArticle
from config import ANTHROPIC_API_KEY, CLAUDE_EFFORT, CLAUDE_MODEL, REVIEW_MAX_TOKENS
from crawlers.base import Article
from text_utils import extract_json_array

# 임계값은 심사 프롬프트의 루브릭(_SCORING_RUBRIC)과 한 쌍으로 움직인다.
# 이전 값(75/82)은 루브릭 없이 정해져 모델의 실제 분포와 어긋나 있었다.
# 실측(2026-09-07, 후보 10건): min 15 / max 88 / 평균 60이고 모델이 KEEP으로
# 판정한 실용 아티클이 70~88에 몰려, 82 컷은 anthropic.com 급만 통과시켰다.
_QUALITY_THRESHOLD: Final = 62.0
_UNKNOWN_SOURCE_THRESHOLD: Final = 70.0
_CONTENT_TYPES: Final = {"ai_programming", "game_asset_workflow"}
_ENGINE_NAMES: Final = {
    "cross-engine": "Cross-engine",
    "godot": "Godot",
    "unity": "Unity",
    "unreal": "Unreal",
    "unreal engine": "Unreal",
}


@dataclass(frozen=True, slots=True)
class EditorialDecision:
    """Parsed, bounded decision returned by the editorial reviewer."""

    url: str
    verdict: str
    quality_score: float
    title_ko: str
    summary_ko: str
    why_it_matters_ko: str
    content_type: str
    engines: tuple[str, ...]
    game_client_relevance: float
    keywords: tuple[str, ...]
    rejection_reason: str


def _number(value) -> float:
    """0~100으로 클램프한 점수. 숫자 문자열도 허용한다.

    문자열을 0.0으로 떨어뜨리면 모델이 `"quality_score": "85"`로 응답하는 순간
    모든 후보가 '품질 점수 미달(0<70)'로 전멸한다.
    """
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return max(0.0, min(float(value), 100.0))
    if isinstance(value, str):
        try:
            return max(0.0, min(float(value.strip()), 100.0))
        except ValueError:
            return 0.0
    return 0.0


def _strings(value) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def parse_review_decisions(text: str) -> list[EditorialDecision]:
    """Parse reviewer JSON into typed editorial decisions."""
    decisions: list[EditorialDecision] = []
    for raw in extract_json_array(text):
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        if not url:
            continue
        decisions.append(
            EditorialDecision(
                url=url,
                verdict=str(raw.get("verdict") or "REJECT").upper(),
                quality_score=_number(raw.get("quality_score")),
                title_ko=str(raw.get("title_ko") or "").strip(),
                summary_ko=str(raw.get("summary_ko") or "").strip(),
                why_it_matters_ko=str(raw.get("why_it_matters_ko") or "").strip(),
                content_type=str(raw.get("content_type") or "").strip(),
                engines=_strings(raw.get("engines")),
                game_client_relevance=_number(raw.get("game_client_relevance")),
                keywords=_strings(raw.get("keywords")),
                rejection_reason=str(raw.get("rejection_reason") or "").strip(),
            )
        )
    return decisions


def _has_hangul(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text))


def _metadata_keywords(candidate: VerifiedArticle, decision: EditorialDecision) -> list[str]:
    keywords = [str(keyword).strip().lower().replace(" ", "_") for keyword in candidate.article.keywords]
    keywords.extend(str(keyword).strip().lower().replace(" ", "_") for keyword in decision.keywords)
    keywords.append(decision.content_type)
    for engine in decision.engines:
        normalized = _ENGINE_NAMES.get(engine.strip().lower())
        if normalized:
            keywords.append(f"engine:{normalized.lower()}")
    if decision.game_client_relevance >= 60 or decision.content_type == "game_asset_workflow":
        keywords.append("game_client")
    return list(dict.fromkeys(keyword for keyword in keywords if keyword))


def _reject_reason(decision: EditorialDecision | None, threshold: float) -> str:
    """게시되지 못한 이유를 사람이 읽는 한 문장으로 분류한다."""
    if decision is None:
        return "심사 결과 없음"
    if decision.verdict != "KEEP":
        return decision.rejection_reason[:80] or "사유 미제공"
    if decision.quality_score < threshold:
        return f"품질 점수 미달({decision.quality_score:.0f}<{threshold:.0f})"
    if decision.content_type not in _CONTENT_TYPES:
        return f"분류 부적합({decision.content_type or '없음'})"
    return "한국어 필드 누락"


def apply_decisions(
    candidates: list[VerifiedArticle],
    decisions: list[EditorialDecision],
    reasons: list[str] | None = None,
) -> list[Article]:
    """Apply validated decisions and produce publishable Korean article briefs.

    `reasons`를 넘기면 게시되지 못한 후보별 탈락 사유가 순서대로 기록된다.
    """
    by_url = {decision.url: decision for decision in decisions}
    approved: list[Article] = []
    for candidate in candidates:
        decision = by_url.get(candidate.canonical_url) or by_url.get(candidate.article.url)
        threshold = _QUALITY_THRESHOLD if candidate.trusted_source else _UNKNOWN_SOURCE_THRESHOLD
        if (
            decision is None
            or decision.verdict != "KEEP"
            or decision.quality_score < threshold
            or decision.content_type not in _CONTENT_TYPES
            or not all(
                _has_hangul(value) for value in (decision.title_ko, decision.summary_ko, decision.why_it_matters_ko)
            )
        ):
            if reasons is not None:
                reasons.append(_reject_reason(decision, threshold))
            continue
        description = (
            f"{decision.summary_ko[:240]}\n\n"
            f"**왜 유용한가**: {decision.why_it_matters_ko[:120]}\n\n"
            f"원문 제목: {candidate.article.title[:100]}"
        )
        approved.append(
            Article(
                url=candidate.canonical_url,
                title=decision.title_ko[:250],
                source=candidate.article.source,
                description=description[:500],
                author=candidate.article.author,
                image_url=candidate.article.image_url,
                published_at=candidate.published_at,
                platform_score=decision.quality_score,
                keywords=_metadata_keywords(candidate, decision),
            )
        )
    return approved


def _scoring_rubric() -> str:
    """quality_score의 의미를 못 박는 루브릭.

    이 블록이 없으면 모델은 자기 임의 스케일로 점수를 매기고, 코드의 임계값과
    체계적으로 어긋난다. 실측에서 모델이 KEEP으로 판정한 78점·70점 아티클이
    코드 컷(82)에 걸려 전부 폐기됐다. 임계값을 문자열로 직접 주입해
    상수와 프롬프트가 따로 노는 것을 막는다.
    """
    return (
        "SCORING — quality_score is 0-100 on THIS scale, not your own:\n"
        "- 85-100: reproducible end-to-end workflow with commands/code/settings AND measured results "
        "or a real project case study.\n"
        "- 70-84: solid practical guide a working programmer can act on — concrete steps, code, or "
        "tool configuration — even without measured results. Most good blog posts land here.\n"
        "- 50-69: accurate but shallow — concept overview, feature summary, or a list with no "
        "executable detail.\n"
        "- 0-49: news, marketing, paywalled stub, academic paper, or nothing actionable.\n"
        f"KEEP requires quality_score >= {_UNKNOWN_SOURCE_THRESHOLD:.0f}, or "
        f'>= {_QUALITY_THRESHOLD:.0f} when the candidate has "trusted_source": true. '
        "Below that, use REJECT and explain why in rejection_reason.\n"
        "Score every candidate INDEPENDENTLY on its own merits. Near-duplicates are removed before "
        "this step, so never lower a score or REJECT a candidate because another candidate covers a "
        "similar topic."
    )


def _review_prompt(candidates: list[VerifiedArticle]) -> str:
    payload = [
        {
            "url": candidate.canonical_url,
            "title": candidate.article.title,
            "source": candidate.article.source,
            "author": candidate.article.author,
            "language": candidate.language,
            "published_at": candidate.published_at,
            "trusted_source": candidate.trusted_source,
            "excerpt": candidate.excerpt,
        }
        for candidate in candidates
    ]
    schema = (
        '[{"url":"...","verdict":"KEEP|REJECT","quality_score":0-100,'
        '"title_ko":"...","summary_ko":"...","why_it_matters_ko":"...",'
        '"content_type":"ai_programming|game_asset_workflow",'
        '"engines":["Unreal|Unity|Godot|Cross-engine"],"game_client_relevance":0-100,'
        '"keywords":["..."],"rejection_reason":"..."}]'
    )
    return (
        "Review only the supplied, fetched article excerpts. The audience is ordinary game client programmers, "
        "not AI researchers. Keep practical AI programming methods and reproducible workflows for creating, "
        "finding, licensing, optimizing, and integrating game assets. Reject papers, generic AI news, press "
        "releases, marketing pages, shallow listicles, and content without concrete steps or evidence. Treat "
        "Unreal, Unity, and Godot equally. Produce natural Korean editorial fields for every KEEP decision. "
        "Do not invent URLs or facts. Return one decision per input URL as JSON only.\n\n"
        f"{_scoring_rubric()}\n\nOUTPUT: {schema}\n\nCANDIDATES:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _response_text(response) -> str:
    return "\n".join(
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text" and getattr(block, "text", "")
    )


def _request_review(client, prompt: str, max_tokens: int, caller: str):
    """검수 호출 1회. 실패하면 예외 대신 None을 반환한다.

    스트리밍을 쓰는 이유: thinking이 기본 활성이 되면서 출력이 길어졌는데,
    비스트리밍 호출은 SDK가 max_tokens 약 21,300을 넘기면 ValueError를 던진다.
    """
    started = time.perf_counter()
    try:
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            output_config={"effort": CLAUDE_EFFORT},
            system=[{"type": "text", "text": SKILL_REVIEWER, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()
    except (
        anthropic.APIConnectionError,
        anthropic.APIStatusError,
        anthropic.AuthenticationError,
        anthropic.RateLimitError,
    ) as error:
        print(f"[EditorialReview] 검수 실패로 후보를 게시하지 않습니다: {error}")
        return None
    usage = response.usage
    token_tracker.log_token_usage(
        usage.input_tokens,
        usage.output_tokens,
        caller=caller,
        elapsed_seconds=round(time.perf_counter() - started, 2),
    )
    return response


def review_articles(candidates: list[VerifiedArticle], report: dict | None = None) -> list[Article]:
    """Run one search-free editorial review call and return approved Korean briefs.

    `report`를 넘기면 심사 통계(후보 수·통과 수·탈락 사유별 건수)가 채워진다.
    """
    if not candidates or not ANTHROPIC_API_KEY:
        return []
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = _review_prompt(candidates)

    response = _request_review(client, prompt, REVIEW_MAX_TOKENS, "editorial_review")
    if response is not None and response.stop_reason == "max_tokens":
        # 검수에는 폴백 경로가 없어 여기서 폐기하면 그날 브리핑이 통째로 0건이 된다.
        print(f"[EditorialReview] 검수 응답이 잘렸습니다 (max_tokens={REVIEW_MAX_TOKENS}) — 2배로 재시도")
        response = _request_review(client, prompt, REVIEW_MAX_TOKENS * 2, "editorial_review_wide")

    if response is None:
        return []
    if response.stop_reason == "max_tokens":
        print("[EditorialReview] 재시도 후에도 잘려 전체 결과를 폐기합니다.")
        return []
    reasons: list[str] = []
    approved = apply_decisions(candidates, parse_review_decisions(_response_text(response)), reasons)
    if report is not None:
        report["candidates"] = len(candidates)
        report["kept"] = len(approved)
        report["reasons"] = dict(Counter(reasons).most_common(5))
    return approved
