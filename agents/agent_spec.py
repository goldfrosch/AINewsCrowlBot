"""
에이전트 문서(.claude/agents, .claude/skills) 로더

토픽 목록과 스킬 본문은 마크다운 문서가 단일 출처(single source of truth)다.
모듈 로드 시 1회만 읽는다.
"""

import re
from pathlib import Path

import yaml

_CLAUDE_DIR = Path(__file__).resolve().parent.parent / ".claude"
_AGENT_DOC_PATH = _CLAUDE_DIR / "agents" / "news-curation-agent.md"
_SKILLS_DIR = _CLAUDE_DIR / "skills"


def _strip_frontmatter(text: str) -> str:
    """YAML 프론트매터(--- ... ---)를 제거하고 본문만 반환한다."""
    match = re.match(r"^---\n.*?\n---\n(.*)$", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def _load_agent_spec() -> dict:
    """news-curation-agent.md 프론트매터에서 토픽·필라 설정을 로드한다."""
    text = _AGENT_DOC_PATH.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValueError(f"에이전트 문서 형식 오류: {_AGENT_DOC_PATH}")
    frontmatter = yaml.safe_load(match.group(1))
    return {
        "topics": frontmatter.get("topics", {}),
        "default_topics": frontmatter.get("default_topics", []),
        "pillars": frontmatter.get("pillars", {}),
    }


def load_skill(name: str) -> str:
    """.claude/skills/{name}.md 본문을 반환한다. 파일이 없으면 빈 문자열."""
    path = _SKILLS_DIR / f"{name}.md"
    if not path.exists():
        return ""
    return _strip_frontmatter(path.read_text(encoding="utf-8"))


_SPEC = _load_agent_spec()

TOPIC_DESC: dict[str, str] = _SPEC["topics"]
DEFAULT_TOPICS: list[str] = _SPEC["default_topics"]
PILLARS: dict[str, dict] = _SPEC["pillars"]
SKILL_FINDER: str = load_skill("article-finder")
SKILL_REVIEWER: str = load_skill("article-reviewer")


def get_topic_keys() -> set[str]:
    """큐레이션 에이전트가 지원하는 토픽 키 집합."""
    return set(TOPIC_DESC.keys())


def pillar_keys() -> list[str]:
    """정의된 필라 키를 문서 순서대로 반환한다."""
    return list(PILLARS.keys())


def pillar_topics(pillar: str) -> list[str]:
    """필라에 속한 토픽 중 실제로 정의된 것만 반환한다."""
    spec = PILLARS.get(pillar) or {}
    return [topic for topic in (spec.get("topics") or []) if topic in TOPIC_DESC]


def pillar_of(topic: str) -> str | None:
    """토픽이 속한 필라 키. 어디에도 없으면 None."""
    for key, spec in PILLARS.items():
        if topic in (spec.get("topics") or []):
            return key
    return None


def pillar_max_age_days(pillar: str, default: int) -> int:
    """필라별 신선도 컷오프. 값이 없거나 이상하면 기본값을 쓴다."""
    value = (PILLARS.get(pillar) or {}).get("max_age_days")
    return value if isinstance(value, int) and value > 0 else default


def pillar_weight(pillar: str) -> int:
    """필라별 목표 수량 배분 가중치."""
    value = (PILLARS.get(pillar) or {}).get("weight")
    return value if isinstance(value, int) and value > 0 else 1


def pillar_label(pillar: str) -> str:
    return str((PILLARS.get(pillar) or {}).get("label") or pillar)


def topics_for_round(topics: list[str], round_index: int) -> list[str]:
    """재시도 라운드마다 토픽 순서를 회전시켜 검색 각도를 바꾼다.

    같은 프롬프트로 재검색하면 같은 결과가 돌아오므로, 톱업 라운드에서는
    토픽 우선순위를 바꿔 다른 쿼리가 생성되도록 유도한다.
    """
    if round_index <= 0 or len(topics) <= 1:
        return list(topics)
    shift = round_index % len(topics)
    return list(topics[shift:]) + list(topics[:shift])
