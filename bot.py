"""
Discord 봇 본체

주요 흐름:
  1. 매일 새벽 3시(KST): Claude 에이전트 큐레이션 → 상위 5개 게시
  2. 각 기사 임베드에 👍/👎 반응 자동 추가 → 선호도 학습
  3. !more [n]  : 추가 기사 n개 (Claude가 이미 게시된 URL 제외 후 새로 리서치)
  4. !crawl     : 즉시 브리핑 (관리자)
  5. !stats     : 선호도 통계
  6. !reset     : 선호도 초기화 (관리자)
  7. !help_ai   : 명령어 목록
"""

import asyncio
import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import database as db
import token_tracker
from agents.preference_analysis import run_preference_analysis, save_preference_profile
from config import (
    ALLOWED_USER_IDS,
    ARTICLES_PER_POST,
    DAILY_POST_HOUR,
    DISCORD_CHANNEL_ID,
    MORE_ARTICLES_MAX,
    PREFERENCE_ANALYSIS_HOUR,
    TIMEZONE,
)
from pipeline import run_curation_pipeline
from ranker import apply_feedback

KST = ZoneInfo(TIMEZONE)
LIKE = "👍"
DISLIKE = "👎"

_SOURCE_EMOJI: dict[str, str] = {
    "hackernews": "🔥",
    "youtube": "▶️",
    "reddit": "🤖",
    "arxiv": "📄",
    "medium": "📝",
    "venturebeat": "📰",
    "verge": "📰",
    "threads": "🧵",
    "linkedin": "💼",
    "zdnet": "🇰🇷",
    "it조선": "🇰🇷",
    "ai타임스": "🇰🇷",
    "ars technica": "🔬",
    "openai": "🤖",
    "anthropic": "🤖",
    "google": "🤖",
    "meta": "🤖",
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
        k in article["source"].lower() for k in ("zdnet", "it조선", "korea", "naver", "ai타임스", "전자신문")
    )

    if is_ai_curated:
        color = discord.Color.from_rgb(108, 77, 217)  # 보라: Claude 큐레이션
    elif is_korean:
        color = discord.Color.from_rgb(0, 112, 255)  # 파랑: 한국어
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
    token_tracker.init_token_db()
    print(f"✅ 봇 로그인: {bot.user}  |  채널: {DISCORD_CHANNEL_ID}")
    if not daily_preference_analysis.is_running():
        daily_preference_analysis.start()
    if not daily_brief.is_running():
        daily_brief.start()
    print(f"📅 매일 {PREFERENCE_ANALYSIS_HOUR:02d}:00 KST 선호도 분석 / {DAILY_POST_HOUR:02d}:00 KST 브리핑 등록")


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


@tasks.loop(time=datetime.time(hour=PREFERENCE_ANALYSIS_HOUR, minute=0, tzinfo=KST))
async def daily_preference_analysis():
    """새벽 2시: 선호도 심층 분석 → data/preference_profile.json 저장."""
    print("[선호도 분석] 시작…")
    try:
        analysis = await asyncio.to_thread(run_preference_analysis)
        await asyncio.to_thread(save_preference_profile, analysis)
        print(f"[선호도 분석] 완료 — {analysis['summary']}")
    except Exception as e:
        print(f"[선호도 분석] 오류: {e}")


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
    핵심 로직은 pipeline.run_curation_pipeline()에 위임합니다.
    """
    status_msg = await channel.send("🧠 Claude가 AI 뉴스를 리서치하는 중…")

    try:
        result = await asyncio.to_thread(run_curation_pipeline, count)

        if result["error"]:
            await status_msg.edit(content=f"❌ Claude 큐레이션 실패: {result['error']}")
            return

        if not result["articles"]:
            msg = "⚠️ Claude 큐레이션 결과가 없습니다." if result["raw_count"] == 0 else "📭 게시할 새 기사가 없습니다."
            await status_msg.edit(content=msg)
            return

        await status_msg.edit(
            content=f"✅ Claude 큐레이션 완료 — {result['raw_count']}개 선정 ({result['new_count']}개 신규)"
        )

    except Exception as e:
        await status_msg.edit(content=f"❌ Claude 큐레이션 실패: {e}")
        return

    articles_to_post = result["articles"]

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


# ─── 권한 체크 ────────────────────────────────────────────────────────────────


def is_admin_or_allowed():
    """관리자이거나 ALLOWED_USER_IDS에 포함된 유저면 통과."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id in ALLOWED_USER_IDS:
            return True
        if ctx.guild and ctx.author.guild_permissions.administrator:
            return True
        raise commands.MissingPermissions(["administrator"])

    return commands.check(predicate)


# ─── 명령어 ───────────────────────────────────────────────────────────────────


@bot.command(name="more")
async def cmd_more(ctx: commands.Context, count: int = ARTICLES_PER_POST):
    """추가 기사를 가져옵니다. 예: !more 3"""
    count = max(1, min(count, MORE_ARTICLES_MAX))
    await _research_and_post(ctx.channel, count=count, is_daily=False)


@bot.command(name="crawl")
@is_admin_or_allowed()
async def cmd_crawl(ctx: commands.Context):
    """즉시 브리핑을 실행합니다. (관리자 전용)"""
    await _research_and_post(ctx.channel, count=ARTICLES_PER_POST, is_daily=False)


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
            f"• {s['source']}: **{s['multiplier']:.2f}x**  (👍{s['total_likes']} 👎{s['total_dislikes']})"
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


@bot.command(name="analyze")
@is_admin_or_allowed()
async def cmd_analyze(ctx: commands.Context):
    """선호도 심층 분석을 즉시 실행합니다. (관리자 전용)"""
    status_msg = await ctx.send("🔍 선호도 분석 중…")
    try:
        analysis = await asyncio.to_thread(run_preference_analysis)
        profile = await asyncio.to_thread(save_preference_profile, analysis)

        hints = profile["curation_hints"]

        embed = discord.Embed(
            title="🧠 선호도 분석 완료",
            description=analysis["summary"],
            color=discord.Color.from_rgb(108, 77, 217),
        )

        if hints["boost_sources"]:
            embed.add_field(name="✅ 선호 소스", value=", ".join(hints["boost_sources"]), inline=False)
        if hints["avoid_sources"]:
            embed.add_field(name="❌ 비선호 소스", value=", ".join(hints["avoid_sources"]), inline=False)
        if hints["focus_keywords"]:
            embed.add_field(name="🔑 선호 키워드", value=", ".join(hints["focus_keywords"]), inline=False)
        if hints["skip_keywords"]:
            embed.add_field(name="🚫 비선호 키워드", value=", ".join(hints["skip_keywords"]), inline=False)

        embed.add_field(
            name="신뢰도",
            value=f"`{hints['confidence']}`  (데이터 윈도우: {hints['data_window']})",
            inline=False,
        )
        embed.set_footer(text=f"분석 시각: {profile['generated_at'][:19].replace('T', ' ')}")

        await status_msg.delete()
        await ctx.send(embed=embed)
    except Exception as e:
        await status_msg.edit(content=f"❌ 선호도 분석 실패: {e}")


@bot.command(name="reset")
@is_admin_or_allowed()
async def cmd_reset(ctx: commands.Context):
    """선호도 데이터를 초기화합니다. (관리자 전용)"""
    db.reset_preferences()
    await ctx.send("✅ 소스 및 키워드 선호도가 초기화되었습니다.")


@bot.command(name="tokens")
async def cmd_tokens(ctx: commands.Context):
    """오늘의 토큰 사용량, 5시간 윈도우 비교, 전체 일평균을 표시합니다."""
    today = token_tracker.get_today_token_stats()
    window = token_tracker.get_window_stats()
    avg = token_tracker.get_average_daily_stats()

    embed = discord.Embed(title="🔢 Claude 토큰 사용 현황", color=discord.Color.from_rgb(108, 77, 217))

    # 오늘 통계
    embed.add_field(
        name="📅 오늘 사용량",
        value=(
            f"API 호출: **{today['call_count']}**회\n"
            f"입력 토큰: **{today['total_input']:,}**\n"
            f"출력 토큰: **{today['total_output']:,}**\n"
            f"합계: **{today['total_tokens']:,}**\n"
            f"호출당 평균: **{today['avg_per_call']:,}**"
        ),
        inline=True,
    )

    # 5시간 윈도우 비교
    if window["pct_change"] is None:
        trend = "이전 윈도우 데이터 없음"
    elif window["pct_change"] > 0:
        trend = f"▲ +{window['pct_change']}%"
    elif window["pct_change"] < 0:
        trend = f"▼ {window['pct_change']}%"
    else:
        trend = "→ 변화 없음"

    embed.add_field(
        name="⏱️ 5시간 윈도우 비교",
        value=(
            f"현재 윈도우: **{window['current_tokens']:,}** ({window['current_calls']}회)\n"
            f"이전 윈도우: **{window['prev_tokens']:,}** ({window['prev_calls']}회)\n"
            f"증감: **{trend}**"
        ),
        inline=True,
    )

    # 전체 일평균
    embed.add_field(
        name="📊 전체 평균",
        value=(
            f"측정 기간: **{avg['total_days']}**일\n"
            f"누적 합계: **{avg['grand_total']:,}**\n"
            f"일 평균: **{avg['avg_per_day']:,}**\n"
            f"호출당 평균: **{avg['avg_per_call']:,}**"
        ),
        inline=False,
    )

    # 호출자별 오늘 내역
    if today["callers"]:
        lines = [f"• `{c['caller']}`: {c['tokens']:,} tok ({c['calls']}회)" for c in today["callers"][:8]]
        embed.add_field(name="🔍 오늘 호출 내역", value="\n".join(lines), inline=False)

    # 최근 7일 일별 사용량
    if avg["recent_daily"]:
        lines = [f"• {d['day']}: **{d['tokens']:,}** ({d['calls']}회)" for d in avg["recent_daily"][:7]]
        embed.add_field(name="📈 최근 7일", value="\n".join(lines), inline=False)

    embed.set_footer(text="5시간 윈도우 = Anthropic 요금제 롤링 한도 기준")
    await ctx.send(embed=embed)


@bot.command(name="help_ai")
async def cmd_help(ctx: commands.Context):
    """사용 가능한 명령어 목록을 표시합니다."""
    embed = discord.Embed(title="🤖 AINewsCrawlBot 명령어", color=discord.Color.blurple())
    embed.add_field(
        name="일반",
        value=(
            "`!more [n]`  — 추가 기사 n개 요청 (기본 5, 최대 10)\n"
            "`!stats`     — 봇 통계 및 선호도 현황\n"
            "`!tokens`    — Claude 토큰 사용량 (오늘/윈도우/평균)\n"
            "`!help_ai`   — 이 도움말"
        ),
        inline=False,
    )
    embed.add_field(
        name="관리자",
        value=(
            "`!crawl`    — 즉시 브리핑 실행\n"
            "`!analyze`  — 선호도 심층 분석 즉시 실행\n"
            "`!reset`    — 학습된 선호도 초기화"
        ),
        inline=False,
    )
    embed.set_footer(text="각 기사에 👍/👎 반응을 남기면 Claude가 취향을 학습합니다.")
    await ctx.send(embed=embed)
