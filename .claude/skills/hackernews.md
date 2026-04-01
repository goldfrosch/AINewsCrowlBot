---
name: hackernews
description: HackerNews 공개 API를 사용해 top/best/new 스토리, 댓글 스레드, 사용자 프로필을 인증 없이 curl로 가져오는 패턴. community_buzz 토픽 크롤링이나 AI 관련 HN 스레드 탐색 시 참조.
source: https://skills.sh/vm0-ai/vm0-skills/hackernews
---

# Hacker News API Skill

HackerNews 공개 Firebase API — **인증 불필요**, curl 직접 호출.

## Base URL

```
https://hacker-news.firebaseio.com/v0
```

## 주요 엔드포인트

| 엔드포인트 | 설명 |
|-----------|------|
| `/topstories.json` | 상위 500개 스토리 ID |
| `/beststories.json` | 최고 투표 스토리 ID |
| `/newstories.json` | 최신 500개 스토리 ID |
| `/askstories.json` | Ask HN 게시물 |
| `/showstories.json` | Show HN 게시물 |
| `/jobstories.json` | 채용 공고 |
| `/item/{id}.json` | 아이템 상세 (제목·URL·점수·댓글) |
| `/user/{id}.json` | 사용자 프로필 (karma·제출 이력) |
| `/maxitem.json` | 현재 최대 아이템 ID |
| `/updates.json` | 최근 변경된 아이템 |

---

## 예시 명령어

### Top 10 스토리 ID 가져오기

```bash
curl -s "https://hacker-news.firebaseio.com/v0/topstories.json" | jq '.[:10]'
```

### 스토리 상세 정보 (점수 포함)

```bash
curl -s "https://hacker-news.firebaseio.com/v0/topstories.json" | jq '.[:10][]' | while read id; do
  curl -s "https://hacker-news.firebaseio.com/v0/item/${id}.json" | jq '{id, title, score, url, by}'
done
```

### 고득점 스토리 필터링 (100점 이상)

```bash
curl -s "https://hacker-news.firebaseio.com/v0/topstories.json" | jq '.[:30][]' | while read id; do
  curl -s "https://hacker-news.firebaseio.com/v0/item/${id}.json" \
    | jq -r 'select(.score >= 100) | "\(.score) | \(.title) | \(.url)"'
done
```

### AI/ML 관련 스토리 검색

```bash
# 상위 50개 스토리에서 AI 관련 제목 필터
curl -s "https://hacker-news.firebaseio.com/v0/topstories.json" | jq '.[:50][]' | while read id; do
  curl -s "https://hacker-news.firebaseio.com/v0/item/${id}.json" \
    | jq -r 'select(.title | test("AI|LLM|GPT|Claude|machine learning|neural"; "i")) | "\(.score) | \(.title)"'
done
```

### 사용자 프로필 조회

```bash
curl -s "https://hacker-news.firebaseio.com/v0/user/<username>.json"
```

### 댓글 스레드 탐색

```bash
ITEM_ID=12345
curl -s "https://hacker-news.firebaseio.com/v0/item/${ITEM_ID}.json" | jq '.kids[:5][]' | while read kid_id; do
  curl -s "https://hacker-news.firebaseio.com/v0/item/${kid_id}.json" | jq '{by, text}'
done
```

---

## AI 뉴스 봇 활용 포인트

- **`community_buzz` 토픽**: HN top/best 스토리에서 AI 관련 스레드를 수집해 `find_ai_articles` 보완
- **고득점 필터**: 100점 이상 스토리만 선별해 품질 보장
- **Ask HN / Show HN**: AI 도구 소개나 토론 스레드는 `dev_tools`, `applications` 토픽과 연계
- **실시간 트렌드**: `/updates.json`으로 빠르게 부상하는 AI 관련 스토리 감지

## 모범 사례

- 문서화된 속도 제한 없음 — 대량 요청 시 딜레이 추가
- `jq`로 JSON 필터링 및 추출 활용
- 가능하면 결과 캐싱
- Ask HN 게시물의 `url` 필드는 null일 수 있으므로 null 처리 필요
- 타임스탬프는 Unix 형식

## 설치

```bash
npx skills add https://github.com/vm0-ai/vm0-skills --skill hackernews
```
