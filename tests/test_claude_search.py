"""claude_search.py — 공용 웹 검색 레이어의 안전장치"""

import json

import anthropic

import claude_search

_SYSTEM = [{"type": "text", "text": "sys"}]


def _response(mocker, text: str, stop_reason: str = "end_turn"):
    response = mocker.MagicMock()
    response.content = [mocker.MagicMock(type="text", text=text)]
    response.usage = mocker.MagicMock(input_tokens=10, output_tokens=20)
    response.stop_reason = stop_reason
    return response


def _client(mocker, *responses):
    stream = mocker.MagicMock()
    stream.__enter__ = mocker.MagicMock(return_value=stream)
    stream.get_final_message.side_effect = list(responses)
    client = mocker.MagicMock()
    client.messages.stream.return_value = stream
    return client


def _payload(*urls):
    return json.dumps([{"url": u, "title": f"T {u}"} for u in urls])


class TestWebSearchTool:
    def test_declares_direct_caller(self):
        """web_search_20260209는 allowed_callers 기본값이 code_execution이라 400이 난다."""
        tool = claude_search.web_search_tool()
        assert tool["allowed_callers"] == ["direct"]
        assert tool["name"] == "web_search"


class TestSearchArticles:
    def test_extracts_articles(self, mocker):
        client = _client(mocker, _response(mocker, f"preamble {_payload('https://a')}"))
        outcome = claude_search.search_articles(client, prompt="p", system_blocks=_SYSTEM, caller="t")
        assert [a["url"] for a in outcome.articles] == ["https://a"]
        assert outcome.truncated is False

    def test_joins_multiple_text_blocks(self, mocker):
        """JSON이 두 번째 text 블록에 있어도 찾아야 한다 (첫 블록만 보던 버그)."""
        response = mocker.MagicMock()
        response.content = [
            mocker.MagicMock(type="text", text="thinking out loud"),
            mocker.MagicMock(type="text", text=_payload("https://b")),
        ]
        response.usage = mocker.MagicMock(input_tokens=1, output_tokens=1)
        response.stop_reason = "end_turn"
        client = _client(mocker, response)

        outcome = claude_search.search_articles(client, prompt="p", system_blocks=_SYSTEM, caller="t")
        assert [a["url"] for a in outcome.articles] == ["https://b"]

    def test_max_tokens_truncation_retries_wider(self, mocker):
        """절단된 JSON은 추출 실패 → 조용한 0건. 더 큰 예산으로 재시도해야 한다."""
        client = _client(
            mocker,
            _response(mocker, '[{"url":"https://c","tit', stop_reason="max_tokens"),
            _response(mocker, _payload("https://c")),
        )
        outcome = claude_search.search_articles(client, prompt="p", system_blocks=_SYSTEM, caller="t", max_tokens=100)
        assert [a["url"] for a in outcome.articles] == ["https://c"]
        assert client.messages.stream.call_count == 2
        assert client.messages.stream.call_args_list[1].kwargs["max_tokens"] == 200

    def test_truncation_retry_does_not_recurse_forever(self, mocker):
        client = _client(
            mocker,
            _response(mocker, "[{broken", stop_reason="max_tokens"),
            _response(mocker, "[{broken", stop_reason="max_tokens"),
        )
        outcome = claude_search.search_articles(client, prompt="p", system_blocks=_SYSTEM, caller="t", max_tokens=100)
        assert outcome.articles == []
        assert client.messages.stream.call_count == 2

    def test_pause_turn_is_continued(self, mocker):
        client = _client(
            mocker,
            _response(mocker, "searching...", stop_reason="pause_turn"),
            _response(mocker, _payload("https://d")),
        )
        outcome = claude_search.search_articles(client, prompt="p", system_blocks=_SYSTEM, caller="t")
        assert [a["url"] for a in outcome.articles] == ["https://d"]
        assert client.messages.stream.call_count == 2
        # 이어받기 호출에는 assistant 메시지가 붙어 있어야 한다
        assert client.messages.stream.call_args_list[1].kwargs["messages"][-1]["role"] == "assistant"

    def test_api_error_returns_outcome_not_raise(self, mocker):
        stream = mocker.MagicMock()
        stream.__enter__ = mocker.MagicMock(return_value=stream)
        stream.get_final_message.side_effect = anthropic.APIStatusError(
            "boom", response=mocker.MagicMock(status_code=500), body=None
        )
        client = mocker.MagicMock()
        client.messages.stream.return_value = stream

        outcome = claude_search.search_articles(client, prompt="p", system_blocks=_SYSTEM, caller="t")
        assert outcome.articles == []
        assert outcome.error is not None

    def test_unexpected_error_returns_outcome_not_raise(self, mocker):
        client = mocker.MagicMock()
        client.messages.stream.side_effect = RuntimeError("nope")

        outcome = claude_search.search_articles(client, prompt="p", system_blocks=_SYSTEM, caller="t")
        assert outcome.articles == []
        assert "nope" in outcome.error
