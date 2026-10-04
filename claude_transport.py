"""
Claude Messages 호출 전송 계층 — 동기 스트리밍과 Message Batches를 같은 인터페이스로 감싼다.

배치는 토큰 단가가 50%지만(웹 검색 수수료는 같다) 결과가 비동기로 온다. 대부분 1시간 안에
끝나고 보장은 24시간이다. 그래서 사람이 기다리지 않는 브리핑 전 준비 실행만 배치를 쓰고,
`!more`·06:00 브리핑처럼 누군가 기다리는 경로는 동기로 부른다.

배치 모드는 `batch_until(deadline)` 블록 안에서만 켜진다. 탐색(필라별 스레드)과 심사(배치별
스레드)가 각자 스레드에서 호출하므로 contextvars가 아니라 모듈 상태를 쓴다. 큐레이션 실행은
봇의 락으로 한 번에 하나만 돌아 겹치지 않는다 (`database.set_db_path`와 같은 방식).
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from config import BATCH_POLL_SECONDS

# effort 파라미터를 받지 않는 모델. 보내면 400이다 (effort 문서의 지원 모델 목록, 2026-09 기준).
_NO_EFFORT_PREFIXES = ("claude-haiku",)

# time.monotonic() 기준 배치 마감. None이면 동기로 부른다.
_batch_deadline: float | None = None


class BatchDeadlineExceeded(RuntimeError):
    """배치가 마감까지 끝나지 않았다. 배치는 취소했다(처리 전 요청은 과금되지 않는다)."""


class BatchRequestError(RuntimeError):
    """배치 안의 요청이 실패했다. 메시지 앞에 API 오류 유형을 붙여 치명 오류 판정에 쓰게 한다."""


def effort_params(model: str, effort: str) -> dict:
    if model.startswith(_NO_EFFORT_PREFIXES):
        return {}
    return {"output_config": {"effort": effort}}


@contextmanager
def batch_until(deadline: float | None) -> Iterator[None]:
    """블록 안의 호출을 `deadline`(time.monotonic() 기준)까지 배치로 보낸다. None이면 동기 그대로."""
    global _batch_deadline
    previous, _batch_deadline = _batch_deadline, deadline
    try:
        yield
    finally:
        _batch_deadline = previous


def deadline_passed() -> bool:
    return _batch_deadline is not None and time.monotonic() >= _batch_deadline


def create_message(client, params: dict, *, allow_batch: bool = True):
    """메시지 1건을 만들어 `(message, batched)`를 돌려준다.

    `batched`가 참이면 배치 단가(토큰 50%)로 계측해야 한다. `allow_batch=False`는 배치 모드에서도
    동기로 부른다 — 마감을 넘긴 뒤 이미 값을 치른 결과를 살릴 때 쓴다.
    """
    deadline = _batch_deadline if allow_batch else None
    if deadline is None:
        with client.messages.stream(**params) as stream:
            return stream.get_final_message(), False
    return _via_batch(client, params, deadline), True


def _via_batch(client, params: dict, deadline: float):
    if time.monotonic() >= deadline:
        raise BatchDeadlineExceeded("배치 마감이 지나 새 배치를 만들지 않았습니다")
    batch = client.messages.batches.create(requests=[{"custom_id": "request", "params": params}])
    print(f"[Batch] {batch.id} 제출 — 마감까지 {(deadline - time.monotonic()) / 60:.0f}분")
    while batch.processing_status != "ended":
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            client.messages.batches.cancel(batch.id)
            raise BatchDeadlineExceeded(f"배치 {batch.id}가 마감까지 끝나지 않아 취소했습니다")
        time.sleep(min(BATCH_POLL_SECONDS, remaining))
        batch = client.messages.batches.retrieve(batch.id)

    for entry in client.messages.batches.results(batch.id):
        result = entry.result
        if result.type == "succeeded":
            return result.message
        # errored면 result.error.error에 {type, message}가 있다. expired·canceled에는 error가 없다.
        detail = getattr(getattr(result, "error", None), "error", None)
        kind = getattr(detail, "type", None) or result.type
        raise BatchRequestError(f"{kind}: {getattr(detail, 'message', None) or result.type}")
    raise BatchRequestError(f"배치 {batch.id}에 결과가 없습니다")
