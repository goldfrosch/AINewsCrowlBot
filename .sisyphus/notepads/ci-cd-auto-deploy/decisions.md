# Decisions — ci-cd-auto-deploy

## 2026-05-07 Initial
- Using server-side Git delivery (fetch + hard reset) not rsync/scp/registry
- SSH from GitHub Actions using pinned known_hosts
- Manual-only trigger via workflow_dispatch (no push/tag/schedule deploy)
- Deploy concurrency guard prevents overlapping manual deployments
- Server `.env` and `./data` preserved; workflow never overwrites them
- No rollback, canary, blue-green, Discord notifications, coverage, mypy
