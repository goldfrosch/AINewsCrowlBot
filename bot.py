"""
Discord 봇 본체

주요 흐름:
  1. 매일 새벽 3시(KST): Claude 웹 리서치(curator) → 상위 5개 게시
  2. Claude 리서치 실패 시: 기존 크롤러(crawlers) 폴백
  3. 각 기사 임베드에 👍/👎 반응 자동 추가 → 선호도 학습
  4. !more [n]  : 추가 기사 n개 (Claude가 이미 게시된 URL 제외 후 새로 리서치)
  5. !crawl     : 즉시 브리핑 (관리자)
  6. !stats     : 선호도 통계
  7. !reset     : 선호도 초기화 (관리자)
  8. !help_ai   : 명령어 목록
"""
import asyncio
import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import database as db
import curator as ai_curator
from ranker import rank_articles, apply_feedback
from config import (
    DISCORD_BOT_TOKEN,
    DISCORD_CHANNEL_ID,
    ARTICLES_PER_POST,
    MORE_ARTICLES_MAX,
    DAILY_POST_HOUR,
    TIMEZONE,
)

KST = ZoneInfo(TIMEZONE)
LIKE    = "👍"
DISLIKE = "👎"

_SOURCE_EMOJI: dict[str, str] = {
    "hackernews":   "🔥",
    "youtube":      "▶️",
    "reddit":       "🤖",
    "arxiv":        "📄",
    "medium":       "📝",
    "venturebeat":  "📰",
    "verge":        "📰",
    "threads":      "🧵",
    "linkedin":     "💼",
    "zdnet":        "🇰🇷",
    "it조선":       "🇰🇷",
    "ai타임스":     "🇰🇷",
    "ars technica": "🔬",
    "openai":       "🤖",
    "anthropic":    "🤖",
    "google":       "🤖",
    "meta":         "🤖",
}

def _source_emoji(source: str) -> str:
    s = source.lower()
    for key, emoji in _SOURCE_EMOJI.items():
        if key in s:
            return emoji
    return "📌"


def _make_embed(article: dict, is_ai_curated: bool = False) -> discord.Embed:
    emoji = _source_emoji(article["source"])
    title = article["title"][:250]

    is_korean = any(
        k in article["source"].lower()
        for k in ("zdnet", "it조선", "korea", "naver", "ai타임스", "전자신문")
    )

    if is_ai_curated:
        color = discord.Color.from_rgb(108, 77, 217)   # 보라: Claude 큐레이션
    elif is_korean:
        color = discord.Color.from_rgb(0, 112, 255)    # 파랑: 한국어
    else:
        color = discord.Color.orange()

    embed = discord.Embed(
        title=f"{emoji} {title}",
        url=article["url"],
        color=color,
    )

    if article.get("description"):
        embed.description = article["description"][:400]

    # 필드 구성
    source_val = article["source"]
    if is_ai_curated:
        source_val += "  ·  🧠 Claude 리서치"
    embed.add_field(name="출처", value=source_val, inline=True)

    if article.get("author"):
        embed.add_field(name="작성자", value=article["author"][:60], inline=True)

    score = article.get("platform_score", 0)
    if 0 < score < 100:  # Claude 큐레이션(100)은 점수 필드 숨김
        embed.add_field(name="점수", value=f"{int(score):,}", inline=True)

    if article.get("image_url"):
        embed.set_thumbnail(url=article["image_url"])

    pub = (article.get("published_at") or "")[:10]
    footer = f"발행일: {pub}  |  " if pub else ""
    footer += "👍 좋아요  /  👎 별로예요"
    embed.set_footer(text=footer)

    return embed


# ─── 봇 설정 ──────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ─── 이벤트 ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    db.init_db()
    print(f"✅ 봇 로그인: {bot.user}  |  채널: {DISCORD_CHANNEL_ID}")
    if not daily_brief.is_running():
        daily_brief.start()
    print(f"📅 매일 {DAILY_POST_HOUR:02d}:00 KST 자동 브리핑 등록")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    emoji = str(payload.emoji)
    if emoji not in (LIKE, DISLIKE):
        return
    liked = emoji == LIKE
    if apply_feedback(str(payload.message_id), liked):
        print(f"[반응] {'👍' if liked else '👎'} → msg={payload.message_id} 선호도 업데이트")


# ─── 스케줄 작업 ──────────────────────────────────────────────────────────────

@tasks.loop(time=datetime.time(hour=DAILY_POST_HOUR, minute=0, tzinfo=KST))
async def daily_brief():
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if not channel:
        print(f"[오류] 채널 {DISCORD_CHANNEL_ID} 를 찾을 수 없습니다.")
        return
    await _research_and_post(channel, count=ARTICLES_PER_POST, is_daily=True)


# ─── 핵심 리서치+게시 로직 ────────────────────────────────────────────────────

async def _research_and_post(
    channel: discord.TextChannel,
    count: int = ARTICLES_PER_POST,
    is_daily: bool = False,
) -> None:
    """
    Claude 웹 리서치로 기사를 가져와 게시합니다.
    !more 호출 시 오늘 이미 게시된 URL을 Claude에게 전달해 중복 방지합니다.
    """
    status_msg = await channel.send("🧠 Claude가 AI 뉴스를 리서치하는 중…")
    articles_to_post = []

    try:
        exclude = db.get_todays_posted_urls()
        prefs   = db.get_all_preferences()

        raw_articles = await asyncio.to_thread(
            ai_curator.curate,
            count,
            exclude,
            prefs,
        )

        if raw_articles:
            new_count = sum(1 for a in raw_articles if db.upsert_article(a.to_dict()))
            await status_msg.edit(
                content=f"✅ Claude 리서치 완료 — {len(raw_articles)}개 선정 ({new_count}개 신규)"
            )
            pending = db.get_pending_articles(limit=count + 10)
            articles_to_post = rank_articles(pending)[:count]
        else:
            await status_msg.edit(content="⚠️ Claude 리서치 결과가 없습니다.")

    except Exception as e:
        await status_msg.edit(content=f"❌ Claude 리서치 실패: {e}")
        return

    if not articles_to_post:
        await channel.send("📭 게시할 새 기사가 없습니다.")
        return

    # ── 3. 헤더 메시지 ──────────────────────────────────────────────────────
    if is_daily:
        today = datetime.datetime.now(tz=KST).strftime("%Y년 %m월 %d일")
        await channel.send(
            f"## 🤖 {today} AI 뉴스 브리핑  [🧠 Claude 리서치]\n"
            f"오늘의 주요 AI 소식 **{len(articles_to_post)}개**입니다."
        )

    # ── 4. 기사 임베드 게시 ─────────────────────────────────────────────────
    for article in articles_to_post:
        embed = _make_embed(article, is_ai_curated=True)
        msg = await channel.send(embed=embed)
        await msg.add_reaction(LIKE)
        await msg.add_reaction(DISLIKE)
        db.mark_as_posted(article["id"], str(msg.id), str(channel.id))
        await asyncio.sleep(0.5)


# ─── 명령어 ───────────────────────────────────────────────────────────────────

@bot.command(name="more")
async def cmd_more(ctx: commands.Context, count: int = ARTICLES_PER_POST):
    """추가 기사를 가져옵니다. 예: !more 3"""
    count = max(1, min(count, MORE_ARTICLES_MAX))
    await _research_and_post(ctx.channel, count=count, is_daily=False)


@bot.command(name="crawl")
@commands.has_permissions(administrator=True)
async def cmd_crawl(ctx: commands.Context):
    """즉시 브리핑을 실행합니다. (관리자 전용)"""
    await _research_and_post(ctx.channel, count=ARTICLES_PER_POST, is_daily=True)


@bot.command(name="stats")
async def cmd_stats(ctx: commands.Context):
    """봇 통계 및 학습된 선호도를 표시합니다."""
    stats = db.get_stats()
    prefs = db.get_all_preferences()

    embed = discord.Embed(title="📊 AINewsCrawlBot 통계", color=discord.Color.green())

    embed.add_field(name="현재 모드", value="🧠 Claude 웹 리서치", inline=False)

    embed.add_field(
        name="기사 현황",
        value=(
            f"수집 총계: **{stats['total']}**개\n"
            f"게시 완료: **{stats['posted']}**개\n"
            f"대기 중:   **{stats['pending']}**개\n"
            f"총 👍: {stats['total_likes']}  /  총 👎: {stats['total_dislikes']}"
        ),
        inline=False,
    )

    if prefs["sources"]:
        lines = [
            f"• {s['source']}: **{s['multiplier']:.2f}x**  "
            f"(👍{s['total_likes']} 👎{s['total_dislikes']})"
            for s in prefs["sources"][:10]
        ]
        embed.add_field(name="소스 선호도 배율", value="\n".join(lines), inline=False)

    if prefs["keywords"]:
        top = [k for k in prefs["keywords"] if k["multiplier"] != 1.0][:10]
        if top:
            lines = [f"• `{k['keyword']}`: {k['multiplier']:.2f}x" for k in top]
            embed.add_field(name="키워드 선호도 (변화된 것)", value="\n".join(lines), inline=False)

    embed.set_footer(text="👍/👎 반응이 누적될수록 Claude의 리서치 방향이 취향에 맞춰집니다.")
    await ctx.send(embed=embed)


@bot.command(name="reset")
@commands.has_permissions(administrator=True)
async def cmd_reset(ctx: commands.Context):
    """선호도 데이터를 초기화합니다. (관리자 전용)"""
    db.reset_preferences()
    await ctx.send("✅ 소스 및 키워드 선호도가 초기화되었습니다.")


@bot.command(name="help_ai")
async def cmd_help(ctx: commands.Context):
    """사용 가능한 명령어 목록을 표시합니다."""
    embed = discord.Embed(title="🤖 AINewsCrawlBot 명령어", color=discord.Color.blurple())
    embed.add_field(
        name="일반",
        value=(
            "`!more [n]`  — 추가 기사 n개 요청 (기본 5, 최대 10)\n"
            "`!stats`     — 봇 통계 및 선호도 현황\n"
            "`!help_ai`   — 이 도움말"
        ),
        inline=False,
    )
    embed.add_field(
        name="관리자",
        value=(
            "`!crawl`  — 즉시 브리핑 실행\n"
            "`!reset`  — 학습된 선호도 초기화"
        ),
        inline=False,
    )
    embed.set_footer(text="각 기사에 👍/👎 반응을 남기면 Claude가 취향을 학습합니다.")
    await ctx.send(embed=embed)
