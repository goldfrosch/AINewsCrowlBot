import sys

from bot import bot
from config import DISCORD_BOT_TOKEN


def main():
    if not DISCORD_BOT_TOKEN:
        print("[오류] DISCORD_BOT_TOKEN이 설정되지 않았습니다.")
        print("       .env.example을 참고해 .env 파일을 만들어 주세요.")
        sys.exit(1)

    print("🚀 AINewsCrawlBot 시작 중…")
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
