# 배포 가이드

GitHub Actions를 이용한 수동 배포 파이프라인 문서.
모든 배포는 **workflow_dispatch**로만 실행되며, 자동 트리거는 없다.

---

## 목차

1. [아키텍처 개요](#1-아키텍처-개요)
2. [GitHub Secrets 설정](#2-github-secrets-설정)
3. [서버 사전 준비](#3-서버-사전-준비)
4. [최초 서버 세팅](#4-최초-서버-세팅)
5. [수동 배포 실행](#5-수동-배포-실행)
6. [워크플로우 상세 동작](#6-워크플로우-상세-동작)
7. [헬스 체크](#7-헬스-체크)
8. [실패 처리](#8-실패-처리)
9. [데이터 및 시크릿 보호](#9-데이터-및-시크릿-보호)
10. [배포 시간 주의사항](#10-배포-시간-주의사항)
11. [문제 해결](#11-문제-해결)

---

## 1. 아키텍처 개요

```
GitHub Actions (workflow_dispatch)
  → CI Job: ubuntu-latest, Python 3.11
      → ruff check (린트)
      → pytest (테스트)
  → Deploy Job: SSH로 서버 접속
      → cd /ai-news-crowl-bot
      → git fetch origin main && git reset --hard origin/main
      → sudo docker compose build
      → sudo docker compose up -d
      → 헬스 체크 (docker compose ps + 로그 grep)
```

| 항목 | 값 |
|------|-----|
| 워크플로우 이름 | `Deploy` |
| 트리거 방식 | `workflow_dispatch` (수동만) |
| 서버 프로젝트 경로 | `/ai-news-crowl-bot` |
| 컨테이너 이름 | `ainewsbot` |
| 서비스 이름 | `bot` |
| Docker 명령어 | `sudo docker compose` |

---

## 2. GitHub Secrets 설정

레포지토리 **Settings → Secrets and variables → Actions** 에서 아래 4개를 등록한다.

| Secret 이름 | 설명 | 예시 |
|-------------|------|------|
| `DEPLOY_HOST` | 배포 서버의 호스트명 또는 IP | `203.0.113.50` |
| `DEPLOY_USER` | SSH 접속 사용자명 | `deploy` |
| `DEPLOY_SSH_KEY` | SSH 개인키 (전체 PEM 내용) | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `SSH_KNOWN_HOSTS` | 서버의 SSH 호스트 키 | `ssh-keyscan` 출력 결과 |

### SSH_KNOWN_HOSTS 생성 방법

```bash
ssh-keyscan -H <호스트_IP_또는_도메인>
```

출력된 전체 내용을 `SSH_KNOWN_HOSTS` 시크릿 값으로 그대로 복사한다.

> **주의**: 개인키, 서버 IP 등 시크릿 값을 코드나 문서에 하드코딩하지 않는다.

---

## 3. 서버 사전 준비

배포 서버에 다음이 설치되어 있어야 한다.

| 요구사항 | 확인 명령어 | 기대 결과 |
|----------|------------|-----------|
| Docker Engine | `docker --version` | 버전 정보 출력 |
| Docker Compose V2 | `sudo -n docker compose version` | 버전 정보 출력 |
| Git | `git --version` | 버전 정보 출력 |
| Python 3.11+ (로컬 개발용이며 서버에서는 불필요) | | |
| 패스워드 없는 sudo | `sudo -n docker compose version` | 에러 없이 버전 출력 |

### 패스워드 없는 sudo 확인

```bash
sudo -n docker compose version
```

비밀번호 프롬프트 없이 버전이 출력되면 정상이다. 프롬프트가 뜨면 `visudo`로 deploy 사용자에게 Docker 권한을 추가해야 한다.

---

## 4. 최초 서버 세팅

새 서버에 처음 배포하는 경우, 아래 절차를 한 번만 수행한다.

### 4-1. 프로젝트 클론

```bash
cd /
git clone https://github.com/<사용자>/<레포>.git ai-news-crowl-bot
cd /ai-news-crowl-bot
```

### 4-2. .env 파일 생성

서버에서 직접 생성한다. **GitHub에 커밋하지 않는다.**

```bash
cat > .env << 'EOF'
# 필수
DISCORD_BOT_TOKEN=봇_토큰
DISCORD_CHANNEL_ID=채널_ID

# 권장
ANTHROPIC_API_KEY=클로드_API_키

# 선택
CLAUDE_MODEL=claude-sonnet-4-6
EOF
```

### 4-3. 데이터 디렉토리 확인

```bash
mkdir -p data
```

SQLite DB(`data/bot.db`)는 `docker-compose.yml`의 볼륨 마운트로 컨테이너 재시작 시에도 보존된다.

### 4-4. 최초 빌드 및 실행

```bash
sudo docker compose build
sudo docker compose up -d
```

---

## 5. 수동 배포 실행

GitHub 웹 UI에서 직접 실행한다.

1. 레포지토리 → **Actions** 탭
2. 좌측에서 **Deploy** 워크플로우 선택
3. **Run workflow** 버튼 클릭
4. 브랜치 `main` 확인 후 **Run workflow** 확정

배포가 시작되면 Actions 탭에서 실시간 로그를 확인할 수 있다.

---

## 6. 워크플로우 상세 동작

### 6-1. CI Job

| 단계 | 내용 |
|------|------|
| 환경 | `ubuntu-latest`, Python 3.11 |
| 린트 | `ruff check` |
| 테스트 | `pytest` |

CI가 실패하면 Deploy Job은 실행되지 않는다.

### 6-2. Deploy Job

CI 통과 후 SSH로 서버에 접속해 아래 순서로 실행된다.

```
1. cd /ai-news-crowl-bot
2. git fetch origin main
3. git reset --hard origin/main
4. sudo docker compose build
5. sudo docker compose up -d
6. 헬스 체크
```

---

## 7. 헬스 체크

배포 완료 후 워크플로우가 자동으로 헬스 체크를 수행한다.

### 확인 항목

```bash
# 컨테이너 상태 확인
sudo docker compose ps
```

`bot` 서비스 상태가 `Up`이면 정상이다.

```bash
# 로그에서 로그인 성공 확인
sudo docker compose logs bot | grep "봇 로그인"
```

로그에 `✅ 봇 로그인: {bot.user}  |  채널: {DISCORD_CHANNEL_ID}` 가 나타나면 봇이 정상적으로 Discord에 연결된 것이다.

---

## 8. 실패 처리

배포 과정에서 오류가 발생하면, 워크플로우가 종료 전에 진단 정보를 수집한다.

```bash
# 컨테이너 상태 출력
sudo docker compose ps

# 최근 로그 50줄 출력
sudo docker compose logs --tail=50 bot
```

이 정보는 Actions 로그에서 확인할 수 있으며, 실패 원인 파악에 사용한다.

> **참고**: 이 배포 파이프라인은 자동 롤백을 지원하지 않는다. 실패 시 수동으로 이전 이미지로 복구해야 한다.

---

## 9. 데이터 및 시크릿 보호

GitHub Actions 워크플로우는 **절대로** 서버의 `.env` 파일이나 `data/` 디렉토리를 덮어쓰지 않는다.

| 보호 대상 | 이유 |
|-----------|------|
| `.env` | Discord 토큰, API 키 등 민감 정보 포함. GitHub에 저장 금지 |
| `data/bot.db` | SQLite DB. 기사, 선호도 데이터 포함 |
| `data/preference_profile.json` | 선호도 분석 결과 |

### 배포 시 보존되는 항목

- `git reset --hard`는 Git이 추적하는 파일만 변경한다
- `.env`는 `.gitignore`에 포함되어 있으므로 영향을 받지 않는다
- `data/` 역시 `.gitignore`에 포함되어 보존된다

### .env 관리 원칙

- 프로덕션 `.env`는 **서버에서만** 관리한다
- GitHub Secrets에는 `.env` 전체를 저장하지 않는다
- `.env` 파일을 레포지토리에 커밋하지 않는다

---

## 10. 배포 시간 주의사항

봇이 실행 중인 예약 작업 시간대에는 배포를 피한다.

| 시간 (KST) | 작업 | 영향 |
|------------|------|------|
| **02:00** | 선호도 분석 | DB 읽기/쓰기, 프로파일 생성 중 배포 시 분석 중단 가능 |
| **06:00** | 뉴스 큐레이션 | API 호출, Discord 게시 중 배포 시 게시 실패 가능 |

### 권장 배포 시간

- **오전 07:00 ~ 익일 01:00 KST** 사이에 배포한다
- 긴급 패치가 필요한 경우에도 02:00~06:00 구간은 가급적 피한다

---

## 11. 문제 해결

### CI 실패

**증상**: Actions 탭에서 CI Job이 빨간색으로 실패

**확인**:
1. Actions 로그에서 `ruff check` 또는 `pytest` 단계를 확인
2. 로컬에서 재현:

```bash
pip install ruff pytest
ruff check .
pytest
```

**해결**: 린트 오류 수정 또는 실패한 테스트 수정 후 푸시하고 다시 배포.

---

### SSH 접속 실패

**증상**: Deploy Job에서 `Connection refused` 또는 `Permission denied`

**확인**:
1. `DEPLOY_HOST`가 올바른지 확인
2. `DEPLOY_USER`가 존재하는지 확인
3. `DEPLOY_SSH_KEY`가 서버의 공개키와 쌍을 이루는지 확인
4. `SSH_KNOWN_HOSTS`가 최신인지 확인:

```bash
ssh-keyscan -H <호스트> | diff - <(echo "GitHub Secrets의 값")
```

**해결**: 잘못된 Secret을 업데이트하고 다시 배포.

---

### sudo 실패

**증상**: `sudo: a password is required` 에러

**확인**:

```bash
sudo -n docker compose version
```

비밀번호 프롬프트가 뜨면 패스워드 없는 sudo가 설정되지 않은 것이다.

**해결**: 서버에서 `visudo`로 deploy 사용자에게 Docker 권한 추가:

```
deploy ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker compose
```

---

### Docker 빌드 실패

**증상**: `docker compose build` 단계에서 에러

**확인**:
1. Actions 로그에서 빌드 에러 메시지 확인
2. 서버에서 수동 빌드 재현:

```bash
cd /ai-news-crowl-bot
sudo docker compose build
```

**해결**: `Dockerfile` 또는 `requirements.txt` 오류 수정 후 푸시.

---

### 컨테이너 비정상 (Up이 아님)

**증상**: `docker compose ps`에서 상태가 `Up`이 아니거나 컨테이너가 없음

**확인**:

```bash
sudo docker compose ps
sudo docker compose logs --tail=50 bot
```

**해결**:
1. `.env` 파일이 존재하고 값이 올바른지 확인
2. `DISCORD_BOT_TOKEN`이 유효한지 확인
3. 포트 충돌 또는 디스크 공간 부족 여부 확인

---

### 로그에 로그인 메시지 없음

**증상**: 컨테이너는 `Up`이지만 `✅ 봇 로그인` 로그가 보이지 않음

**확인**:

```bash
sudo docker compose logs --tail=100 bot
```

**해결**:
1. `DISCORD_BOT_TOKEN`이 만료되지 않았는지 확인
2. Discord 봇이 활성화되어 있는지 Developer Portal에서 확인
3. 네트워크 방화벽이 Discord API 접근을 차단하지 않는지 확인
4. `.env`의 `DISCORD_CHANNEL_ID`가 올바른지 확인
