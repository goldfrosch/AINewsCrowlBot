---
name: discord-bot
description: Discord.py 2.x patterns for this bot. Use when modifying bot.py — event handlers, reaction feedback, embed formatting, scheduled tasks, and command structure.
---

# Discord Bot — discord.py 2.x 패턴

`bot.py`의 핵심 구조 및 수정 시 참고 레퍼런스.

## 인텐트 설정

`message_content` + `reactions` 인텐트가 필수. `bot.py` 상단 참조.

## 이벤트 구조

| 이벤트 | 위치 | 역할 |
|--------|------|------|
| `on_ready` | `bot.py` | DB 초기화, 스케줄러 시작 (중복 시작 방지 포함) |
| `on_raw_reaction_add` | `bot.py` | 👍/👎 피드백 처리 |

**`on_raw_reaction_add` 사용 이유**: `on_reaction_add`는 캐시에 있는 메시지만 처리하지만, `on_raw_reaction_add`는 캐시 밖 오래된 메시지의 반응도 수신 가능.

피드백 흐름: `payload.message_id` → `db.get_article_by_message_id()` → `ranker.apply_feedback()`.

## 임베드 색상 규칙

| 색상 | RGB | 용도 |
|------|-----|------|
| 보라 | `(108, 77, 217)` | Claude 큐레이션 |
| 파랑 | `(0, 112, 255)` | 한국어 소스 |
| 주황 | `Color.orange()` | 일반 크롤러 폴백 |

임베드 구성: `bot.py` `_build_embed()` 참조.

## 정기 작업

`tasks.loop(time=...)` + `ZoneInfo("Asia/Seoul")`로 KST 기준 매일 03:00 실행.
구현: `bot.py` `daily_brief()` 참조.

## 비동기 + 동기 브리지

Claude API / 크롤러 등 동기 함수를 async 컨텍스트에서 실행할 때는 `asyncio.to_thread()` 사용.
구현: `bot.py` `_research_and_post()` 참조.

## 커맨드 목록

| 커맨드 | 권한 | 동작 |
|--------|------|------|
| `!more [count]` | 일반 | 즉시 큐레이션 후 게시 (1–10개) |
| `!reset` | 관리자 | 선호도 전체 초기화 |
| `!stats` | 일반 | 기사 통계 출력 |

## 게시 후 처리 순서

1. `channel.send(embed=embed)` — 메시지 전송
2. `msg.add_reaction("👍")` / `msg.add_reaction("👎")` — 반응 추가
3. `db.mark_as_posted(article_id, msg_id, channel_id)` — DB 상태 갱신
4. `asyncio.sleep(0.5)` — Discord rate limit 방지

## 환경변수 필수값

| 변수 | 용도 |
|------|------|
| `DISCORD_BOT_TOKEN` | 봇 인증 |
| `DISCORD_CHANNEL_ID` | 게시 채널 (int) |
| `ANTHROPIC_API_KEY` | Claude 리서치 (없으면 크롤러 폴백) |
