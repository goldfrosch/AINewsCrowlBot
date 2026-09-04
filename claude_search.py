"""
Claude 웹 검색 호출 공용 레이어

curator.py(폴백)와 agents/news_curation_agent.py(메인)에 거의 동일한
스트리밍 호출 블록이 복제되어 있었고, 그 결과 한쪽만 수정되는 버그가
반복됐다. 두 경로가 동일한 안전장치를 공유하도록 여기에 통합한다.

통합된 안전장치:
  - `stop_reason == "max_tokens"`  → 잘린 JSON 감지 후 max_tokens 2배로 1회 재시도
  - `stop_reason == "pause_turn"`  → assistant 콘텐츠를 되돌려주며 루프 이어받기
  - `RateLimitError`               → 30초 대기 후 1회 재시도
  - 모든 text 블록을 합쳐 JSON 배열 추출 (첫 블록만 보던 버그 제거)
"""

import time
from dataclasses import dataclass, field

import anthropic

import token_tracker
from config import (
    CLAUDE_EFFORT,
    CLAUDE_MODEL,
    SEARCH_MAX_TOKENS,
    WEB_SEARCH_ALLOWED_CALLERS,
    WEB_SEARCH_MAX_USES,
    WEB_SEARCH_TOOL_TYPE,
)
from text_utils import extract_json_array

_PAUSE_TURN_MAX_CONTINUATIONS = 2
_RATE_LIMIT_WAIT_SECONDS = 30


@dataclass
class SearchOutcome:
    """웹 검색 1회분 결과와 진단 정보."""

    articles: list[dict] = field(default_factory=list)
    stop_reason: str | None = None
    truncated: bool = False
    error: str | None = None


def web_search_tool(max_uses: int = WEB_SEARCH_MAX_USES) -> dict:
    """web_search 도구 스펙.

    allowed_callers를 명시하는 이유: web_search_20260209는 기본값이
    code_execution이라 programmatic tool calling 미지원 모델에서 400이 난다.
    """
    return {
        "type": WEB_SEARCH_TOOL_TYPE,
        "name": "web_search",
        "max_uses": max_uses,
        "allowed_callers": list(WEB_SEARCH_ALLOWED_CALLERS),
    }


def _collect_text(response) -> str:
    """응답의 모든 text 블록을 합친다."""
    parts = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "")
            if text:
                parts.append(text)
    return "\n".join(parts)


def _invoke(client, *, prompt: str, system_blocks, caller: str, max_tokens: int, max_uses: int):
    """pause_turn을 이어받으며 최종 응답을 반환한다."""
    messages: list[dict] = [{"role": "user", "content": prompt}]
    response = None

    for attempt in range(_PAUSE_TURN_MAX_CONTINUATIONS + 1):
        started = time.perf_counter()
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            output_config={"effort": CLAUDE_EFFORT},
            tools=[web_search_tool(max_uses)],
            system=system_blocks,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        usage = getattr(response, "usage", None)
        if usage is not None:
            token_tracker.log_token_usage(
                getattr(usage, "input_tokens", 0) or 0,
                getattr(usage, "output_tokens", 0) or 0,
                caller=caller if attempt == 0 else f"{caller}_pause{attempt}",
                elapsed_seconds=round(time.perf_counter() - started, 2),
            )

        if getattr(response, "stop_reason", None) != "pause_turn":
            return response

        print(f"[ClaudeSearch] {caller}: pause_turn — 루프 이어받기 ({attempt + 1})")
        messages = [*messages, {"role": "assistant", "content": response.content}]

    return response


def search_articles(
    client,
    *,
    prompt: str,
    system_blocks,
    caller: str,
    max_tokens: int = SEARCH_MAX_TOKENS,
    max_uses: int = WEB_SEARCH_MAX_USES,
    retry_on_truncate: bool = True,
) -> SearchOutcome:
    """웹 검색으로 기사 JSON 배열을 수집한다. 예외를 던지지 않는다."""
    try:
        response = _invoke(
            client,
            prompt=prompt,
            system_blocks=system_blocks,
            caller=caller,
            max_tokens=max_tokens,
            max_uses=max_uses,
        )
    except anthropic.RateLimitError:
        print(f"[ClaudeSearch] {caller}: RateLimit — {_RATE_LIMIT_WAIT_SECONDS}초 대기 후 재시도")
        time.sleep(_RATE_LIMIT_WAIT_SECONDS)
        try:
            response = _invoke(
                client,
                prompt=prompt,
                system_blocks=system_blocks,
                caller=f"{caller}_retry",
                max_tokens=max_tokens,
                max_uses=max_uses,
            )
        except Exception as e:
            print(f"[ClaudeSearch] {caller}: 재시도 실패 ({e})")
            return SearchOutcome(error=str(e))
    except anthropic.APIStatusError as e:
        print(f"[ClaudeSearch] {caller}: API 오류 ({e.status_code}) {e}")
        return SearchOutcome(error=f"{e.status_code}: {e}")
    except Exception as e:
        print(f"[ClaudeSearch] {caller}: 예상치 못한 오류 ({e})")
        return SearchOutcome(error=str(e))

    stop_reason = getattr(response, "stop_reason", None)
    articles = extract_json_array(_collect_text(response))
    truncated = stop_reason == "max_tokens"

    if truncated and retry_on_truncate:
        # 잘린 JSON은 배열이 닫히지 않아 추출 자체가 실패한다 → 실측 0건의 주범.
        print(f"[ClaudeSearch] {caller}: max_tokens로 응답이 잘렸습니다 (max_tokens={max_tokens}) — 2배로 재시도")
        retry = search_articles(
            client,
            prompt=prompt,
            system_blocks=system_blocks,
            caller=f"{caller}_wide",
            max_tokens=max_tokens * 2,
            max_uses=max_uses,
            retry_on_truncate=False,
        )
        if retry.articles:
            return retry

    return SearchOutcome(articles=articles, stop_reason=stop_reason, truncated=truncated)
