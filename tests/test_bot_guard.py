"""bot.py — 큐레이션 동시 실행 방지와 !more 쿨다운"""

from __future__ import annotations

import asyncio

from discord.ext import commands

import bot
from config import MORE_COOLDOWN_SECONDS


def test_manual_run_is_turned_away_while_curation_is_running(mocker) -> None:
    """06:00 브리핑과 !more가 겹치면 같은 기사를 두 번 게시하고 비용도 두 번 나간다."""
    pipeline_run = mocker.patch("bot.run_curation_pipeline")
    channel = mocker.MagicMock()
    channel.send = mocker.AsyncMock()

    async def scenario() -> None:
        async with bot._curation_lock:
            await bot._research_and_post(channel, count=2, is_daily=False)

    asyncio.run(scenario())

    pipeline_run.assert_not_called()
    channel.send.assert_awaited_once()


def test_more_cooldown_reply_reports_remaining_minutes(mocker) -> None:
    """쿨다운에 걸린 요청을 조용히 버리면 사용자는 봇이 멈춘 줄 안다."""
    ctx = mocker.MagicMock()
    ctx.send = mocker.AsyncMock()
    cooldown = commands.Cooldown(1, MORE_COOLDOWN_SECONDS)

    asyncio.run(bot.cmd_more_error(ctx, commands.CommandOnCooldown(cooldown, 125.0, commands.BucketType.guild)))

    ctx.send.assert_awaited_once()
    assert "3분" in ctx.send.call_args.args[0]
