import os

from dotenv import load_dotenv

load_dotenv()

# ── Discord ───────────────────────────────────────
DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID: int = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

# 관리자 명령어를 허용할 특정 유저 ID 목록 (쉼표로 구분)
# 예: ALLOWED_USER_IDS=123456789,987654321
_raw_ids = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS: set[int] = {int(uid.strip()) for uid in _raw_ids.split(",") if uid.strip().isdigit()}

# ── Claude (주 리서치 엔진) ───────────────────────
# 설정 시: Claude 웹 리서치로 뉴스 큐레이션 (권장)
# 미설정 시: 기존 크롤러로 폴백
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-opus-5")

# ── 외부 API 키 (크롤러 폴백용) ───────────────────
YOUTUBE_API_KEY: str = os.getenv("YOUTUBE_API_KEY", "")
REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT: str = os.getenv("REDDIT_USER_AGENT", "AINewsCrawlBot/1.0")
THREADS_ACCESS_TOKEN: str = os.getenv("THREADS_ACCESS_TOKEN", "")
LINKEDIN_LI_AT: str = os.getenv("LINKEDIN_LI_AT", "")

# ── 스케줄 설정 ───────────────────────────────────
TIMEZONE = "Asia/Seoul"
PREFERENCE_ANALYSIS_HOUR = 2  # 새벽 2시 KST: 선호도 분석
DAILY_POST_HOUR = 6  # 오전 6시 KST: 뉴스 브리핑

# ── 게시 설정 ─────────────────────────────────────
ARTICLES_PER_POST = 2  # 품질 기준을 통과한 기사만 하루 최대 2개 게시
MORE_ARTICLES_MAX = 2  # 수동 요청도 한 번에 최대 2개
MAX_PER_SOURCE = 10  # 소스당 최대 수집 수

# ── 랭킹 정규화 ───────────────────────────────────
# 모든 프로듀서(Claude 웹 검색 / HackerNews / RSS)는 platform_score를
# 0~100 밴드로 emit한다. 소스별 상한을 두면 같은 점수가 소스에 따라
# 수십 배 다르게 평가되므로 단일 밴드로 통일한다.
PLATFORM_SCORE_BAND_MAX = 100.0

# ── 신선도(recency) 설정 ──────────────────────────
# web_search 도구에는 날짜 필터 파라미터가 존재하지 않는다.
# 따라서 신선도는 (1) 프롬프트에 오늘 날짜를 주입하고
#              (2) published_at을 코드에서 하드 필터링해 강제한다.
RECENCY_MAX_AGE_DAYS = 7  # 이 일수를 넘긴 기사는 폐기
RECENCY_PREFERRED_AGE_DAYS = 2  # 프롬프트에서 우선 요청할 기간

# ── 수집 신뢰성 설정 ──────────────────────────────
# 목표 수량을 채우기 위한 오버페치·재시도 파라미터.
# 중복(이미 게시)·신선도 미달로 버려지는 양을 흡수한다.
OVERFETCH_MULTIPLIER = 4  # 목표 N개 → N×4개 요청
OVERFETCH_MIN = 8  # 오버페치 최소 요청 수
OVERFETCH_MAX = 24  # 오버페치 최대 요청 수 (토큰 보호)
TOPUP_MAX_ROUNDS = 2  # 목표 미달 시 추가 검색 횟수
EXCLUDE_URL_LOOKBACK_DAYS = 45  # 중복 제외 대상 게시 이력 조회 기간
EXCLUDE_URL_PROMPT_LIMIT = 40  # 프롬프트에 나열할 제외 URL 최대 수

# ── Claude 호출 설정 ──────────────────────────────
# max_tokens는 thinking + 응답 텍스트의 합산 하드 캡이다. Opus 5부터 thinking이
# 기본 활성이라, 서버사이드 web_search 블록과 thinking이 같은 예산을 나눠 쓴다.
# 예산이 모자라면 JSON이 잘려 조용히 0건이 된다. (실측: 텍스트만 1,300~8,400)
SEARCH_MAX_TOKENS = 16000
# 검수는 후보 전체를 한 번에 판정하고 한국어 필드까지 생성하므로 탐색보다 출력이 길다.
REVIEW_MAX_TOKENS = 16000
# thinking 분량을 통제하는 레버. 두 호출 모두 구조화 JSON 추출이라 medium이면 충분하다.
# "xhigh"/"max"는 thinking 비활성화와 함께 쓸 수 없다 (400).
CLAUDE_EFFORT = "medium"
WEB_SEARCH_TOOL_TYPE = "web_search_20260209"
# 탐색 토픽이 8개인데 3회로는 토픽 대부분이 조회조차 되지 않는다.
# 실측(2026-09-07): 3회 검색 → 결과 22건 수신 → 채택 0건.
WEB_SEARCH_MAX_USES = 6
# web_search_20260209는 allowed_callers 기본값이 code_execution이라
# programmatic tool calling 미지원 모델에서 400이 발생한다. 명시적으로 direct 지정.
WEB_SEARCH_ALLOWED_CALLERS = ["direct"]

# ── 결정론적 신선 소스 (HN / RSS) ─────────────────
# Claude 웹 검색이 0건이거나 목표 미달일 때 보충하는 후보 풀.
# 발행일이 API/피드에서 직접 오므로 신선도가 구조적으로 보장된다.
FEED_POOL_ENABLED = True
HN_MIN_POINTS = 30  # HN 최소 점수
HN_MAX_RESULTS = 40  # HN Algolia 조회 상한
HN_SEARCH_QUERIES = [
    "claude code",
    "llm agent",
    "prompt engineering",
    "ai coding",
    "mcp server",
    "ai game development",
]
FEED_HTTP_TIMEOUT = 8.0  # 초
RSS_MAX_PER_FEED = 12  # 피드당 최대 채택 수
# 후보풀 선별 기준.
# 관련도 0점(대상 독자와 무관)인 후보는 버린다. arXiv cs.AI처럼 하루 60편씩
# 쏟아지는 피드가 날짜만으로 상위를 독식하는 것을 막기 위해 소스별 상한도 둔다.
FEED_MIN_RELEVANCE = 1
FEED_MAX_PER_SOURCE = 2
FEED_GAME_DEV_WEIGHT = 2  # 게임 개발 키워드 가중치
FEED_TITLE_WEIGHT = 3  # 제목 매치 가중치
FEED_DESC_MATCH_CAP = 3  # 설명 매치 상한 (긴 학술 초록의 키워드 밀집 방지)

# 소스 티어 — 낮을수록 우선. 관련도보다 먼저 적용된다.
# 티어링이 필요한 이유: 관련도를 키워드 개수로만 재면 초록이 긴 arXiv 논문이
# 항상 이긴다. 실제 QA에서 HN 후보 16개가 전부 arXiv에 밀렸다.
FEED_TIER_COMMUNITY = 0  # 투표로 검증된 소스
FEED_TIER_EDITORIAL = 1  # 큐레이션된 매체·블로그
FEED_TIER_ACADEMIC = 2  # 학술 프리프린트
FEED_SOURCE_TIERS: dict[str, int] = {
    "HackerNews": FEED_TIER_COMMUNITY,
    "ArXiv cs.AI": FEED_TIER_ACADEMIC,
    "ArXiv cs.LG": FEED_TIER_ACADEMIC,
    "ArXiv cs.GR": FEED_TIER_ACADEMIC,
}
FEED_DEFAULT_TIER = FEED_TIER_EDITORIAL

# ── AI 관련 필터 키워드 ───────────────────────────
AI_KEYWORDS = [
    # AI 코딩 도구 및 워크플로우
    "claude code",
    "cursor",
    "github copilot",
    "codeium",
    "windsurf",
    "aider",
    "claude",
    "chatgpt",
    "gpt-4",
    "gemini",
    "llm",
    "large language model",
    # 실용적 기법
    "prompt engineering",
    "system prompt",
    "chain of thought",
    "few-shot",
    "rag",
    "retrieval augmented",
    "embedding",
    "vector database",
    "fine-tuning",
    "context window",
    "structured output",
    "function calling",
    # 에이전트 및 도구
    "mcp",
    "model context protocol",
    "tool use",
    "ai agent",
    "agentic",
    "langchain",
    "llamaindex",
    "crewai",
    "autogen",
    "langgraph",
    "anthropic",
    "openai",
    "google deepmind",
    # 개발자 생산성
    "ai workflow",
    "ai coding",
    "ai assistant",
    "copilot",
    "code generation",
    "ai productivity",
    "developer tools",
    "llm integration",
    "hugging face",
    "ollama",
    "vllm",
    "litellm",
    # 한국어
    "인공지능",
    "클로드",
    "프롬프트 엔지니어링",
    "llm 활용",
    "ai 코딩",
    "ai 개발",
    "생성형 ai",
    "거대언어모델",
    "ai 워크플로우",
    "ai 도구",
    # AI 게임 개발 (프로그래머가 못하는 영역 보완)
    "ai 3d modeling",
    "ai game art",
    "ai texture generation",
    "ai animation",
    "ai game ui",
    "ai game design",
    "procedural generation",
    "ai game asset",
    "ai sound design",
    "ai music generation",
    "ai character design",
    "ai level design",
    "stable diffusion game",
    "midjourney game",
    "ai sprite",
    "ai voxel",
    "ai game development",
    "generative ai game",
    "npc ai",
    "ai game testing",
    # 한국어 게임 개발
    "ai 게임 개발",
    "ai 3d 모델링",
    "ai 게임 아트",
    "ai 게임 디자인",
    "ai 애니메이션",
    "ai 게임 에셋",
]

# ── Reddit 서브레딧 ───────────────────────────────
REDDIT_SUBREDDITS = [
    "MachineLearning",
    "LocalLLaMA",
    "artificial",
    "AINews",
    "singularity",
    # 게임 개발 AI
    "gamedev",
    "IndieGaming",
    "unrealengine",
    "unity3d",
    "StableDiffusion",
    "proceduralgeneration",
]

# ── YouTube 검색 쿼리 ─────────────────────────────
YOUTUBE_SEARCH_QUERIES = [
    "AI news latest",
    "LLM tutorial 2025",
    "artificial intelligence breakthrough",
    "인공지능 최신 뉴스",
    # 게임 개발 AI
    "AI game development tutorial 2025",
    "AI 3D modeling game assets workflow",
    "AI game art generation tools",
    "AI game UI design workflow",
    "procedural generation AI game",
]

# ── RSS 피드 ──────────────────────────────────────
RSS_FEEDS: dict[str, str] = {
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "The Verge AI": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "Medium AI": "https://medium.com/feed/tag/artificial-intelligence",
    "ZDNet Korea": "https://zdnet.co.kr/rss/",
    "IT조선": "https://it.chosun.com/section/rss/all.php",
    "Ars Technica AI": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    # 게임 개발 AI
    "80 Level": "https://80.lv/feed/",
    "Game Developer": "https://www.gamedeveloper.com/rss.xml",
    "GDC Blog": "https://gdconf.com/rss.xml",
    # Reddit r/gamedev RSS는 아티클 피드가 아니라 토론 피드라서 제외했다.
    # (QA에서 "Scam alert? I am getting a lot of PM in discord..." 같은 잡담이
    #  게시 후보로 올라왔다.) Reddit은 HN처럼 점수 기반 필터가 있어야 쓸 수 있다.
}

# RSS에서 AI 키워드 필터링이 필요 없는 소스 (이미 AI 특화)
RSS_NO_FILTER_SOURCES = {
    "VentureBeat AI",
    "The Verge AI",
    "Medium AI",
    "80 Level",
    "Game Developer",
}

# ── 게임 개발 + AI 기사 필수 포함 키워드 ───────────
# pipeline.py에서 게임 개발 관련 기사를 식별할 때 사용.
# 제목·설명·키워드에 아래 단어가 포함되면 게임 개발 기사로 간주.
GAME_DEV_KEYWORDS = [
    # 영어
    "ai game",
    "game ai",
    "game dev",
    "gamedev",
    "game development",
    "ai 3d modeling",
    "ai texture",
    "ai animation",
    "ai game art",
    "ai game ui",
    "ai game design",
    "ai game asset",
    "ai sound design",
    "ai music generation",
    "ai character design",
    "ai level design",
    "procedural generation",
    "stable diffusion game",
    "midjourney game",
    "ai sprite",
    "ai voxel",
    "generative ai game",
    "npc ai",
    "ai game testing",
    "unity ai",
    "unreal ai",
    "godot ai",
    # 한국어
    "ai 게임",
    "게임 개발 ai",
    "ai 3d 모델링",
    "ai 게임 아트",
    "ai 게임 디자인",
    "ai 게임 에셋",
    "ai 애니메이션",
]
