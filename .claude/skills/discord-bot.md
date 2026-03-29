---
name: discord-bot
description: Discord.py 2.x patterns for this bot. Use when modifying bot.py — event handlers, reaction feedback, embed formatting, scheduled tasks, and command structure.
---

# Discord Bot — discord.py 2.x 패턴

이 프로젝트 `bot.py`의 핵심 구조 및 패턴 레퍼런스.

## 봇 설정

```python
intents = discord.Intents.default()
intents.message_content = True   # 메시지 내용 읽기
intents.reactions = True          # 반응 이벤트 수신

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
```

## 이벤트 패턴

### on_ready
```python
@bot.event
async def on_ready():
    db.init_db()                    # DB 초기화 (멱등)
    if not daily_brief.is_running():
        daily_brief.start()         # 중복 시작 방지
```

### 반응 이벤트 (👍/👎 피드백)
```python
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return                       # 봇 자신의 반응 무시
    emoji = str(payload.emoji)
    if emoji not in ("👍", "👎"):
        return
    # message_id로 DB에서 기사 찾아 선호도 업데이트
    apply_feedback(str(payload.message_id), emoji == "👍")
```

> `on_reaction_add` 대신 `on_raw_reaction_add` 사용 이유:
> 캐시에 없는 오래된 메시지의 반응도 수신 가능

## 임베드 구성

```python
embed = discord.Embed(
    title=f"{emoji} {title[:250]}",
    url=article["url"],
    color=discord.Color.from_rgb(108, 77, 217),  # 보라: Claude 큐레이션
)
embed.description = summary[:400]
embed.add_field(name="출처", value=source, inline=True)
embed.set_footer(text="👍 좋아요  /  👎 별로예요")
```

색상 규칙:
- `(108, 77, 217)` 보라 — Claude 큐레이션
- `(0, 112, 255)` 파랑 — 한국어 소스
- `Color.orange()` — 일반 크롤러

## 정기 작업 (KST 기준)

```python
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

@tasks.loop(time=datetime.time(hour=3, minute=0, tzinfo=KST))
async def daily_brief():
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    await _research_and_post(channel, count=5, is_daily=True)
```

## 비동기 + 동기 브리지

Claude API / 크롤러 등 동기 함수를 async 컨텍스트에서 실행:
```python
raw_articles = await asyncio.to_thread(
    ai_curator.curate,
    count,
    exclude_urls,
    prefs,
)
```

## 커맨드 구조

```python
@bot.command(name="more")
async def cmd_more(ctx, count: int = 5):
    count = max(1, min(count, 10))   # 범위 클램핑
    await _research_and_post(ctx.channel, count=count)

@bot.command(name="reset")
@commands.has_permissions(administrator=True)  # 관리자 전용
async def cmd_reset(ctx):
    db.reset_preferences()
    await ctx.send("✅ 초기화 완료")
```

## 게시 후 반응 추가 패턴

```python
msg = await channel.send(embed=embed)
await msg.add_reaction("👍")
await msg.add_reaction("👎")
db.mark_as_posted(article["id"], str(msg.id), str(channel.id))
await asyncio.sleep(0.5)  # Discord rate limit 방지
```

## 환경변수 필수값

| 변수 | 용도 |
|------|------|
| `DISCORD_BOT_TOKEN` | 봇 인증 |
| `DISCORD_CHANNEL_ID` | 게시 채널 (int) |
| `ANTHROPIC_API_KEY` | Claude 리서치 (없으면 크롤러 폴백) |
