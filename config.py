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
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

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
ARTICLES_PER_POST = 5  # 하루 기본 게시 수
MORE_ARTICLES_MAX = 10  # !more 최대 요청 수
MAX_PER_SOURCE = 10  # 소스당 최대 수집 수

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
    "ArXiv cs.AI": "https://export.arxiv.org/rss/cs.AI",
    "ArXiv cs.LG": "https://export.arxiv.org/rss/cs.LG",
    "Medium AI": "https://medium.com/feed/tag/artificial-intelligence",
    "ZDNet Korea": "https://zdnet.co.kr/rss/",
    "IT조선": "https://it.chosun.com/section/rss/all.php",
    "Ars Technica AI": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    # 게임 개발 AI
    "80 Level": "https://80.lv/feed/",
    "Game Developer": "https://www.gamedeveloper.com/rss.xml",
    "GDC Blog": "https://gdconf.com/rss.xml",
    "ArXiv cs.GR": "https://export.arxiv.org/rss/cs.GR",
    "Reddit r/gamedev": "https://www.reddit.com/r/gamedev/.rss",
}

# RSS에서 AI 키워드 필터링이 필요 없는 소스 (이미 AI 특화)
RSS_NO_FILTER_SOURCES = {
    "VentureBeat AI",
    "The Verge AI",
    "ArXiv cs.AI",
    "ArXiv cs.LG",
    "ArXiv cs.GR",
    "Medium AI",
    "80 Level",
    "Game Developer",
}
