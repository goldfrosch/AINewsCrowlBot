# CI/CD Auto Deploy for Docker Compose Bot

## TL;DR
> **Summary**: Add a manual GitHub Actions deployment pipeline that runs `pytest` and `ruff check`, then SSHes into the Linux server at `/ai-news-crowl-bot` to reset to `origin/main`, rebuild Docker Compose, start the bot, and verify health from container status/logs.
> **Deliverables**:
> - Root `.dockerignore`
> - `.github/workflows/deploy.yml`
> - `docs/deployment.md`
> **Effort**: Short
> **Parallel**: YES - 3 waves
> **Critical Path**: Task 1 → Task 2 → Final Verification; Task 3 runs alongside Task 1

## Context
### Original Request
User wants automatic deployment because they currently deploy manually to a Linux server.

### Interview Summary
- CI provider: GitHub Actions.
- Trigger: manual approval via `workflow_dispatch`, not push/tag deploy.
- Runtime: Docker Compose on a single Linux server.
- Server path: `/ai-news-crowl-bot`.
- Deploy branch: `main` only.
- Server access: SSH from GitHub Actions using pinned known_hosts.
- Server Docker command: `sudo docker compose`.
- Pre-deploy checks: `pytest tests/ -v` and `ruff check .`.
- Failure handling: restart/status/log collection; no automated rollback.

### Metis Review (gaps addressed)
- Added `.dockerignore` as mandatory first task because `Dockerfile:17` uses `COPY . .` and no ignore file exists.
- Chose server-side Git delivery: `git fetch origin main && git reset --hard origin/main`; no rsync/scp, no registry.
- Added deploy concurrency guard to prevent overlapping manual deployments.
- Preserved server `.env` and `./data` volume; workflow must never overwrite them.
- Excluded `vulture`, `dry_run.py`, Docker registry, rollback, blue-green, notifications, coverage, and type checking.
- Added post-deploy health check using `docker compose ps` and log grep for `✅ 봇 로그인` from `bot.py:133`.

## Work Objectives
### Core Objective
Implement a safe, manual CI/CD deployment path from GitHub Actions to the existing Docker Compose Linux server.

### Deliverables
1. `.dockerignore` at repository root.
2. `.github/workflows/deploy.yml` with `ci` and `deploy` jobs.
3. `docs/deployment.md` documenting secrets, server prerequisites, first-time setup, deployment operation, and failure handling.

### Definition of Done (verifiable conditions with commands)
- `python -m pip install -r requirements.txt` succeeds on GitHub Actions runner.
- `ruff check .` succeeds before deploy.
- `pytest tests/ -v` succeeds before deploy.
- GitHub Actions workflow is syntactically valid and has `workflow_dispatch`, `concurrency`, `ci`, and `deploy`.
- Deploy job SSHes to `/ai-news-crowl-bot`, runs `git fetch origin main`, `git reset --hard origin/main`, `sudo docker compose build`, `sudo docker compose up -d`, and health checks.
- Server `.env` and `./data` are not modified by workflow steps.

### Must Have
- Manual-only deploy trigger.
- `main` branch only.
- SSH secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `SSH_KNOWN_HOSTS`.
- `sudo docker compose` commands.
- Post-failure logs with `sudo docker compose logs --tail=50`.
- Post-deploy success check for service `Up` and log line `✅ 봇 로그인`.

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- Do not edit application code: no changes to `Dockerfile`, `docker-compose.yml`, `main.py`, `bot.py`, database/ranker/curator files.
- Do not use interactive `docker-compose.sh`; it has `read -r` prompts at `docker-compose.sh:68`, `docker-compose.sh:121`, and `docker-compose.sh:144`.
- Do not write, regenerate, delete, or upload `.env`.
- Do not touch server `./data` except read-only verification.
- Do not add Docker registry/image publishing.
- Do not add rollback, canary, blue-green, cron deploys, Discord notifications, coverage, mypy/pyright, or `dry_run.py` CI.
- Do not use `git pull`; use fetch + hard reset for deterministic deploy.

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.
- Test decision: tests-after with existing pytest and Ruff.
- QA policy: Every task has agent-executed scenarios.
- Evidence: `.sisyphus/evidence/task-{N}-{slug}.{ext}`

## Execution Strategy
### Parallel Execution Waves
> Target: 5-8 tasks per wave. This is a small plan, so fewer tasks are intentional.

Wave 1: Task 1 (`.dockerignore`) and Task 3 (`docs/deployment.md`) can run in parallel.
Wave 2: Task 2 (`deploy.yml`) after Task 1, because workflow validation should account for final Docker build context policy.
Wave 3: Final Verification after all implementation tasks.

### Dependency Matrix (full, all tasks)
| Task | Depends On | Blocks |
|---|---|---|
| 1. Add `.dockerignore` | None | 2, Final Verification |
| 2. Add GitHub Actions deploy workflow | 1 | Final Verification |
| 3. Add deployment documentation | None | Final Verification |

### Agent Dispatch Summary (wave → task count → categories)
| Wave | Tasks | Categories |
|---|---:|---|
| 1 | 2 | quick, writing |
| 2 | 1 | unspecified-high |
| 3 | 4 review agents | oracle, unspecified-high, unspecified-high, deep |

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [x] 1. Add Docker build context protection

  **What to do**: Create root `.dockerignore`. Include exactly these patterns unless a line is proven harmful before editing: `.git`, `.github`, `.sisyphus`, `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.venv/`, `venv/`, `.env`, `.env.*`, `data/`, `tests/`, `docs/`, `*.md`, `*.log`, `.DS_Store`. Add a comment that `.env` and `data/` must never enter the image.
  **Must NOT do**: Do not modify `Dockerfile`; do not remove `requirements.txt` or Python source from the build context; do not ignore `agents/`, `crawlers/`, `config.py`, `bot.py`, `main.py`, `database.py`, `ranker.py`, `curator.py`, `pipeline.py`, or `token_tracker.py`.

  **Recommended Agent Profile**:
  - Category: `quick` - Single new configuration file with deterministic content.
  - Skills: [] - No specialized skill needed.
  - Omitted: [`discord-bot`, `sqlite-ops`] - No bot/database code changes.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 2 | Blocked By: none

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `Dockerfile:13-17` - dependencies are copied first, then `COPY . .`; `.dockerignore` controls the second copy.
  - Pattern: `docker-compose.yml:8-10` - host `./data` is mounted into `/app/data`; do not bake DB files into the image.
  - Pattern: `.gitignore:137-145` - `.env`, `data/`, and virtualenv paths are already local/runtime-only.
  - Pattern: `.gitignore:194-195` - `.ruff_cache/` is already local cache.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `.dockerignore` exists at repository root.
  - [ ] `.dockerignore` contains `.env`, `.env.*`, `data/`, `.git`, `tests/`, `.pytest_cache/`, and `.ruff_cache/`.
  - [ ] `docker build --progress=plain -t ainewsbot-ci-check .` succeeds locally or in CI-capable environment.
  - [ ] Build output/context does not include `.env`, `data/`, `.git`, or `tests/` paths; save relevant output to `.sisyphus/evidence/task-1-dockerignore-build.txt`.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Clean Docker build context
    Tool: Bash
    Steps: Run `docker build --progress=plain -t ainewsbot-ci-check .` from repo root; inspect output and resulting image build success.
    Expected: Command exits 0; no secret/runtime paths are copied into image context; `python main.py` remains the container command from Dockerfile.
    Evidence: .sisyphus/evidence/task-1-dockerignore-build.txt

  Scenario: Sensitive file exclusion
    Tool: Bash
    Steps: Create temporary untracked files `.env.ci-probe` and `data/ci-probe.txt`; run Docker build; remove probes; inspect build context/output.
    Expected: Probe files are not copied into image context and are absent from build output; probes are cleaned up after the scenario.
    Evidence: .sisyphus/evidence/task-1-dockerignore-sensitive.txt
  ```

  **Commit**: YES | Message: `build(docker): exclude local artifacts from image context` | Files: [`.dockerignore`]

- [x] 2. Add manual GitHub Actions deployment workflow

  **What to do**: Create `.github/workflows/deploy.yml`. Use `name: Deploy`, `on: workflow_dispatch`, and `concurrency: group: deploy-production; cancel-in-progress: false`. Add `ci` job on `ubuntu-latest`: checkout, setup Python 3.11, cache pip, install `requirements.txt`, run `ruff check .`, run `pytest tests/ -v`. Add `deploy` job with `needs: ci`, `if: github.ref == 'refs/heads/main'`, and environment `production`. Use SSH action or direct SSH with secrets `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `SSH_KNOWN_HOSTS`. The remote script must set `APP_DIR=/ai-news-crowl-bot`, `cd "$APP_DIR"`, verify `.env` exists, verify `data/` exists or create it with `mkdir -p data`, run `git fetch origin main`, `git reset --hard origin/main`, `sudo docker compose build`, `sudo docker compose up -d`, sleep up to 30 seconds, verify `sudo docker compose ps` shows `ainewsbot`/`bot` as `Up`, grep `sudo docker compose logs --tail=100 bot` for `✅ 봇 로그인`, and on failure run `sudo docker compose ps` plus `sudo docker compose logs --tail=50 bot` before exiting non-zero.
  **Must NOT do**: Do not use `docker-compose.sh`; do not run `git pull`; do not write `.env`; do not delete `data/`; do not add `dry_run.py`, `vulture`, format check, coverage, Docker registry, rollback, or deploy-on-push.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - GitHub Actions YAML plus shell deployment logic needs careful failure handling.
  - Skills: [`git-master`] - Use for workflow/git safety patterns if committing is requested during execution.
  - Omitted: [`discord-bot`, `sqlite-ops`, `claude-api`] - No application, DB schema, or Anthropic API changes.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: Final Verification | Blocked By: 1

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `requirements.txt:11-17` - pytest, pytest-mock, ruff, vulture exist; use pytest and ruff only.
  - Pattern: `pyproject.toml:5-28` - Ruff config; command must be `ruff check .`.
  - Pattern: `docker-compose.yml:1-15` - service is `bot`, container name is `ainewsbot`, env file is `.env`, volume is `./data:/app/data`.
  - Pattern: `docker-compose.sh:3` - existing manual convention uses `sudo docker compose`.
  - Pattern: `docker-compose.sh:41-63` - build and up are separate operations; preserve this ordering in CI.
  - Pattern: `docker-compose.sh:67-69`, `docker-compose.sh:120-121`, `docker-compose.sh:142-144` - interactive prompts prove this script must not run in CI.
  - Pattern: `bot.py:129-138` - successful startup prints `✅ 봇 로그인` and schedule registration; use this log as health signal.
  - External: `https://github.com/appleboy/ssh-action` - acceptable SSH action if chosen; pin to a stable major version such as `appleboy/ssh-action@v1`.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `.github/workflows/deploy.yml` exists.
  - [ ] Workflow trigger is manual-only (`workflow_dispatch`) and does not include `push`, `pull_request`, `schedule`, or `release` deploy triggers.
  - [ ] Workflow has `concurrency` with `cancel-in-progress: false`.
  - [ ] CI job runs `ruff check .` and `pytest tests/ -v` before deploy.
  - [ ] Deploy job is blocked unless `github.ref == 'refs/heads/main'`.
  - [ ] Deploy job uses all four required secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `SSH_KNOWN_HOSTS`.
  - [ ] Remote deploy script uses `/ai-news-crowl-bot`, `git fetch origin main`, `git reset --hard origin/main`, `sudo docker compose build`, and `sudo docker compose up -d`.
  - [ ] Remote deploy script never contains `.env` write redirection, `rm -rf data`, `docker compose down -v`, or `git pull`.
  - [ ] YAML parses successfully with an available YAML parser or `actionlint`; save output to `.sisyphus/evidence/task-2-workflow-validate.txt`.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: CI blocks bad code before deploy
    Tool: Bash
    Steps: Run `python -m pip install -r requirements.txt`, then `ruff check .`, then `pytest tests/ -v`; inspect workflow YAML to confirm deploy has `needs: ci`.
    Expected: Local checks exit 0 on current code; if either command fails in a future run, deploy job is unreachable because of `needs: ci`.
    Evidence: .sisyphus/evidence/task-2-ci-gate.txt

  Scenario: Deploy script preserves production data/secrets
    Tool: Bash
    Steps: Parse `.github/workflows/deploy.yml` and search for forbidden patterns: `git pull`, `docker compose down -v`, `rm -rf data`, `cat > .env`, `echo .* > .env`, `workflow_dispatch` missing, `push:` present.
    Expected: No forbidden pattern appears; required patterns `/ai-news-crowl-bot`, `git reset --hard origin/main`, `sudo docker compose build`, `sudo docker compose up -d`, `SSH_KNOWN_HOSTS` appear.
    Evidence: .sisyphus/evidence/task-2-deploy-guardrails.txt
  ```

  **Commit**: YES | Message: `ci(deploy): add manual ssh docker compose deployment` | Files: [`.github/workflows/deploy.yml`]

- [x] 3. Document production deployment setup and operation

  **What to do**: Create `docs/deployment.md`. Include sections: overview, architecture, GitHub Secrets, server prerequisites, first-time server setup, manual deployment steps from GitHub UI, what the workflow does, health checks, failure handling, data/secrets preservation, and troubleshooting. Document required secrets exactly: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `SSH_KNOWN_HOSTS`. Document server requirements: repo cloned at `/ai-news-crowl-bot`, `origin` points to GitHub repo, branch tracks `main`, Docker Compose plugin installed, deploy user can run `sudo docker compose` non-interactively, `.env` exists on server, `data/` persists SQLite DB. Include commands for generating known_hosts (`ssh-keyscan -H <host>`) and validating passwordless sudo (`ssh user@host 'sudo -n docker compose version'`). Document that deployment should not be triggered during the 02:00/06:00 KST scheduled jobs unless downtime is acceptable.
  **Must NOT do**: Do not include real secrets, tokens, IPs, or private keys. Do not instruct users to store production `.env` in GitHub. Do not promise zero downtime or rollback.

  **Recommended Agent Profile**:
  - Category: `writing` - Documentation-heavy task with exact operational instructions.
  - Skills: [] - No project skill required because this is deployment documentation, not Discord/API code.
  - Omitted: [`discord-bot`, `sqlite-ops`] - Only reference runtime behavior; do not change it.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: Final Verification | Blocked By: none

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `README.md` - match Korean project documentation tone and command-block style.
  - Pattern: `docker-compose.yml:4-10` - container name, env file, data volume.
  - Pattern: `README.md` environment section - required production env variables are `DISCORD_BOT_TOKEN` and `DISCORD_CHANNEL_ID`; optional keys include Anthropic/YouTube/Reddit/Threads.
  - Pattern: `bot.py:156-169` - scheduled jobs run at configured KST hours; deploy can briefly restart the bot.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `docs/deployment.md` exists.
  - [ ] Document lists all four GitHub Secrets exactly: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `SSH_KNOWN_HOSTS`.
  - [ ] Document includes `/ai-news-crowl-bot`, `sudo -n docker compose version`, `ssh-keyscan -H`, `.env`, and `data/`.
  - [ ] Document explicitly states GitHub Actions must not overwrite server `.env` or `data/`.
  - [ ] Document troubleshooting includes CI failure, SSH failure, sudo failure, Docker build failure, container unhealthy/no login log.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: New operator can configure secrets from documentation
    Tool: Bash
    Steps: Search `docs/deployment.md` for `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `SSH_KNOWN_HOSTS`, `ssh-keyscan -H`, and `sudo -n docker compose version`.
    Expected: Every required setup item appears exactly and has a short explanation.
    Evidence: .sisyphus/evidence/task-3-docs-secrets.txt

  Scenario: Documentation protects production state
    Tool: Bash
    Steps: Search `docs/deployment.md` for warnings about `.env`, `data/`, no rollback, and no zero-downtime guarantee.
    Expected: Documentation tells the operator that `.env` stays only on server, `data/` contains SQLite production state, rollback is not automated, and deploy causes a brief restart.
    Evidence: .sisyphus/evidence/task-3-docs-guardrails.txt
  ```

  **Commit**: YES | Message: `docs(deploy): document manual production deployment workflow` | Files: [`docs/deployment.md`]

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [x] F1. Plan Compliance Audit — oracle
- [x] F2. Code Quality Review — unspecified-high
- [x] F3. Real Manual QA — unspecified-high
- [x] F4. Scope Fidelity Check — deep

## Commit Strategy
- Commit 1: `build(docker): exclude local artifacts from image context` with `.dockerignore`.
- Commit 2: `ci(deploy): add manual ssh docker compose deployment` with `.github/workflows/deploy.yml`.
- Commit 3: `docs(deploy): document manual production deployment workflow` with `docs/deployment.md`.
- If the user requests one combined commit instead, use `ci(deploy): add manual docker compose deployment` and include all three files.

## Success Criteria
- The workflow can be manually triggered from GitHub Actions and only deploys `main`.
- A failed `ruff check .` or `pytest tests/ -v` prevents any SSH deployment.
- A successful deployment rebuilds and starts the Docker Compose `bot` service on `/ai-news-crowl-bot`.
- The production `.env` and `data/bot.db` remain server-local and preserved.
- Failure output includes enough logs/status for diagnosis without manual SSH inspection.
