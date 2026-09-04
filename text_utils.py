"""
텍스트 파싱 유틸리티

`extract_json_array`는 curator.py와 agents/news_curation_agent.py에 각각
복제되어 있던 함수를 단일 구현으로 통합한 것이다.
에이전트 쪽 구버전은 `text.rfind("[")`로 *마지막* 대괄호를 찾았기 때문에
description에 `[`가 포함되거나 중첩 배열(`"keywords":[...]`)이 있으면
엉뚱한 배열을 파싱했다. 이 구현은 브래킷 매칭으로 바깥 배열만 채택한다.
"""

import json


def extract_json_array(text: str) -> list[dict]:
    """응답 텍스트에서 바깥 JSON 배열을 추출한다.

    '['부터 브래킷 매칭하여 유효한 바깥 배열 후보를 모두 찾고,
    그중 **텍스트 범위가 가장 넓은** 배열을 채택한다.
    dict 원소가 아닌 배열(예: `["kw1","kw2"]`)은 후보에서 제외되지만,
    빈 배열(`"keywords": []`)은 후보가 되므로 "마지막 후보"를 채택하면
    마지막 기사의 빈 keywords 배열을 응답 전체로 오인했다. 바깥 배열은
    항상 내부 배열보다 범위가 넓으므로 최대 범위 선택으로 이를 방지한다.

    Returns:
        dict 리스트. 유효한 배열이 없으면 빈 리스트.
    """
    pos = 0
    best: tuple[int, list[dict]] | None = None  # (텍스트 범위, 파싱 결과)
    while True:
        start = text.find("[", pos)
        if start == -1:
            return best[1] if best else []

        depth = 0
        end = -1
        for i, ch in enumerate(text[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        if end == -1:
            pos = start + 1
            continue

        try:
            result = json.loads(text[start:end])
            if isinstance(result, list) and (not result or isinstance(result[0], dict)):
                span = end - start
                # 동률이면 뒤 후보 승리: 프리앰블 예시 배열 뒤의 최종 배열을 채택하는 기존 계약.
                if best is None or span >= best[0]:
                    best = (span, result)
        except json.JSONDecodeError:
            pass

        pos = start + 1
