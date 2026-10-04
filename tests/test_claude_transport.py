"""claude_transport.py — 동기 스트리밍과 Message Batches를 같은 인터페이스로

배치는 토큰 단가가 절반이지만 결과가 비동기로 온다. 마감을 넘기면 취소해야
06:00 브리핑이 배치를 기다리다 늦지 않는다.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import claude_transport

_PARAMS = {"model": "claude-sonnet-5", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]}


def _stream_client(mocker, message):
    stream = mocker.MagicMock()
    stream.__enter__ = mocker.MagicMock(return_value=stream)
    stream.get_final_message.return_value = message
    client = mocker.MagicMock()
    client.messages.stream.return_value = stream
    return client


def _batch_client(mocker, *statuses, result):
    """create/retrieve가 statuses 순서대로 상태를 돌려주고, 끝나면 result 하나를 내는 클라이언트."""
    client = mocker.MagicMock()
    batches = [SimpleNamespace(id="msgbatch_1", processing_status=status) for status in statuses]
    client.messages.batches.create.return_value = batches[0]
    client.messages.batches.retrieve.side_effect = batches[1:]
    client.messages.batches.results.return_value = iter([SimpleNamespace(custom_id="request", result=result)])
    return client


def _succeeded(message):
    return SimpleNamespace(type="succeeded", message=message)


def test_sync_by_default(mocker) -> None:
    message = object()
    client = _stream_client(mocker, message)

    assert claude_transport.create_message(client, _PARAMS) == (message, False)
    client.messages.stream.assert_called_once_with(**_PARAMS)
    client.messages.batches.create.assert_not_called()


def test_batch_mode_submits_one_request_and_returns_its_message(mocker) -> None:
    message = object()
    client = _batch_client(mocker, "ended", result=_succeeded(message))

    with claude_transport.batch_until(time.monotonic() + 3600):
        assert claude_transport.create_message(client, _PARAMS) == (message, True)

    client.messages.batches.create.assert_called_once_with(requests=[{"custom_id": "request", "params": _PARAMS}])
    client.messages.stream.assert_not_called()


def test_batch_is_polled_until_it_ends(mocker) -> None:
    sleep = mocker.patch("claude_transport.time.sleep")
    message = object()
    client = _batch_client(mocker, "in_progress", "in_progress", "ended", result=_succeeded(message))

    with claude_transport.batch_until(time.monotonic() + 3600):
        assert claude_transport.create_message(client, _PARAMS)[0] is message

    assert client.messages.batches.retrieve.call_count == 2
    assert sleep.call_count == 2


def test_errored_request_raises_with_api_error_type(mocker) -> None:
    """크레딧 소진·인증 실패 같은 치명 오류 판정이 메시지의 오류 유형에 기댄다."""
    error = SimpleNamespace(error=SimpleNamespace(type="authentication_error", message="invalid x-api-key"))
    client = _batch_client(mocker, "ended", result=SimpleNamespace(type="errored", error=error))

    with (
        claude_transport.batch_until(time.monotonic() + 3600),
        pytest.raises(claude_transport.BatchRequestError) as caught,
    ):
        claude_transport.create_message(client, _PARAMS)

    assert "authentication_error" in str(caught.value)


def test_expired_request_raises(mocker) -> None:
    client = _batch_client(mocker, "ended", result=SimpleNamespace(type="expired"))

    with claude_transport.batch_until(time.monotonic() + 3600), pytest.raises(claude_transport.BatchRequestError):
        claude_transport.create_message(client, _PARAMS)


def test_no_new_batch_after_deadline(mocker) -> None:
    client = mocker.MagicMock()

    with claude_transport.batch_until(time.monotonic() - 1), pytest.raises(claude_transport.BatchDeadlineExceeded):
        claude_transport.create_message(client, _PARAMS)

    client.messages.batches.create.assert_not_called()


def test_batch_running_past_deadline_is_canceled(mocker) -> None:
    """마감까지 안 끝난 배치를 계속 기다리면 06:00 브리핑이 늦는다. 취소하고 동기 경로에 맡긴다."""
    clock = iter([0.0, 0.0, 100.0])
    mocker.patch("claude_transport.time.monotonic", side_effect=lambda: next(clock, 100.0))
    client = _batch_client(mocker, "in_progress", result=_succeeded(object()))

    with claude_transport.batch_until(50.0), pytest.raises(claude_transport.BatchDeadlineExceeded):
        claude_transport.create_message(client, _PARAMS)

    client.messages.batches.cancel.assert_called_once_with("msgbatch_1")


def test_allow_batch_false_forces_sync_inside_batch_mode(mocker) -> None:
    message = object()
    client = _stream_client(mocker, message)

    with claude_transport.batch_until(time.monotonic() - 1):
        assert claude_transport.create_message(client, _PARAMS, allow_batch=False) == (message, False)


def test_batch_mode_ends_with_the_block_even_on_error() -> None:
    with pytest.raises(RuntimeError), claude_transport.batch_until(time.monotonic() - 1):
        raise RuntimeError("boom")

    assert claude_transport.deadline_passed() is False


def test_effort_is_omitted_for_models_that_reject_it() -> None:
    """Haiku 4.5에 effort를 보내면 400이다."""
    assert claude_transport.effort_params("claude-haiku-4-5", "medium") == {}
    assert claude_transport.effort_params("claude-sonnet-5", "medium") == {"output_config": {"effort": "medium"}}
