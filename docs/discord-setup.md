# Discord 봇 세팅 가이드

## 1. Discord Developer Portal 접속

[discord.com/developers/applications](https://discord.com/developers/applications) → **New Application** 클릭

---

## 2. 봇 생성

**Bot** 탭 → **Add Bot** → 이름 설정

**Token 복사** — `.env` 파일에 직접 입력 (외부 공유 금지)

```
DISCORD_BOT_TOKEN=여기에_토큰_입력
```

---

## 3. 봇 권한 설정 (Privileged Gateway Intents)

**Bot** 탭 → 아래 항목 **ON**:

| 항목 | 필요 이유 |
|------|-----------|
| `Message Content Intent` | 메시지 내용 읽기 (커맨드 처리) |
| `Server Members Intent` | 멤버 정보 (선택) |

---

## 4. OAuth2 URL 생성

> **관리자에게 이 과정에서 생성된 URL을 전달합니다.**

**OAuth2** 탭 → **URL Generator**

**Scopes 선택:**
- [x] `bot`
- [x] `applications.commands` (슬래시 커맨드 사용 시)

**Bot Permissions 선택:**

| 권한 | 필요 이유 |
|------|-----------|
| `Send Messages` | 뉴스 게시 |
| `Embed Links` | 임베드 메시지 |
| `Read Message History` | 반응 읽기 |
| `Add Reactions` | 👍/👎 반응 추가 |
| `View Channels` | 채널 접근 |
| `Use External Emojis` | 이모지 사용 (선택) |

하단에 생성된 URL을 관리자에게 전달 → 관리자가 해당 URL로 서버에 봇 초대

---

## 5. 채널 ID 획득 (관리자 수행)

1. Discord 설정 → **고급** → **개발자 모드 ON**
2. 뉴스를 게시할 채널 **우클릭** → **ID 복사**
3. 복사한 ID를 개발자에게 전달

```
DISCORD_CHANNEL_ID=복사한_채널_ID
```

---

## 관리자 체크리스트

```
[ ] 1. 봇 초대 URL로 서버에 봇 추가
[ ] 2. 뉴스 게시용 채널 ID 개발자에게 전달
[ ] 3. 봇에게 해당 채널 접근 권한 확인
[ ] 4. (선택) 봇 전용 역할 생성 및 채널 권한 설정
```

---

## 최종 .env 구성

```env
DISCORD_BOT_TOKEN=봇_토큰_여기에
DISCORD_CHANNEL_ID=채널_ID_여기에
ANTHROPIC_API_KEY=클로드_키_여기에
```

> 관리자는 봇 토큰을 알 필요 없으며, 채널 ID만 개발자에게 전달하면 됩니다.
