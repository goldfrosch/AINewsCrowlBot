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
    모델 응답 끝부분의 최종 JSON 배열을 우선 사용한다.
    dict 원소가 아닌 배열(예: `["kw1","kw2"]`)은 후보에서 제외하므로
    중첩된 keywords 배열을 오인하지 않는다.

    Returns:
        dict 리스트. 유효한 배열이 없으면 빈 리스트.
    """
    pos = 0
    candidates: list[list[dict]] = []
    while True:
        start = text.find("[", pos)
        if start == -1:
            return candidates[-1] if candidates else []

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
                candidates.append(result)
        except json.JSONDecodeError:
            pass

        pos = start + 1
