# Learnings — ci-cd-auto-deploy

## 2026-05-07 Initial Context
- `.dockerignore` already exists at root with 8 lines: `.git`, `.env`, `.venv`, `venv/`, `__pycache__/`, `*.py[cod]`, `data/`, `.claude/`
- Plan requires expanding it to include: `.github`, `.sisyphus`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.env.*`, `tests/`, `docs/`, `*.md`, `*.log`, `.DS_Store`
- Dockerfile uses `COPY . .` at line 17 - `.dockerignore` controls what gets copied
- docker-compose.yml service name is `bot`, container name is `ainewsbot`
- docker-compose.sh has interactive `read -r` prompts at lines 68, 121, 144 - MUST NOT be used in CI
- Bot login success prints `✅ 봇 로그인: {bot.user}  |  채널: {DISCORD_CHANNEL_ID}` at bot.py:133
- `sudo docker compose` is the server convention (docker-compose.sh:3)
- Server path: `/ai-news-crowl-bot`
- Ruff config in pyproject.toml: target py311, line-length 120, command must be `ruff check .`
- requirements.txt has pytest, pytest-mock, ruff, vulture in testing/lint section
