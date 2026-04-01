---
name: ai-social-media-content
description: inference.sh CLI(infsh)를 사용해 TikTok·Instagram·YouTube·Twitter 등 소셜 미디어 콘텐츠(영상·이미지·캡션)를 생성하는 패턴. AI 뉴스 브리핑 콘텐츠의 소셜 미디어 배포 자동화 또는 Discord 임베드용 미리보기 이미지 생성 시 참조.
source: https://skills.sh/inferen-sh/skills/ai-social-media-content
---

# AI Social Media Content — inference.sh 기반 콘텐츠 생성

[inference.sh](https://inference.sh) CLI(`infsh`)를 통해 플랫폼별 소셜 미디어 콘텐츠를 생성한다.

## 설치 및 인증

```bash
# inference.sh CLI 설치 (별도 문서 참조)
infsh login
```

## 플랫폼별 포맷

| 플랫폼 | 비율 | 길이 | 해상도 |
|--------|------|------|--------|
| TikTok | 9:16 세로 | 15-60s | 1080×1920 |
| Instagram Reels | 9:16 세로 | 15-90s | 1080×1920 |
| Instagram Feed | 1:1 또는 4:5 | - | 1080×1080 |
| YouTube Shorts | 9:16 세로 | <60s | 1080×1920 |
| YouTube Thumbnail | 16:9 | - | 1280×720 |
| Twitter/X | 16:9 또는 1:1 | <140s | 1920×1080 |

---

## 콘텐츠 워크플로우

### TikTok / Reels 영상

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Satisfying slow motion video of paint being mixed, vibrant colors swirling together, vertical 9:16, ASMR aesthetic, viral TikTok style"
}'
```

### YouTube Thumbnail

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "YouTube thumbnail, shocked face emoji, bright yellow background, bold text area on right, attention-grabbing, high contrast, professional"
}'
```

### Twitter/X 비주얼 포스트

```bash
# 이미지 생성
infsh app run falai/flux-dev --input '{
  "prompt": "Tech infographic style image showing AI trends, modern design, data visualization aesthetic, shareable"
}'

# 트윗 게시
infsh app run twitter/post-tweet --input '{
  "text": "The future of AI is here. Here are the top 5 trends reshaping tech in 2024 🧵",
  "media_url": "<image-url>"
}'
```

### 토킹헤드 영상 (스크립트 → 보이스오버 → 아바타)

```bash
# 1. 스크립트 작성
infsh app run openrouter/claude-sonnet-45 --input '{
  "prompt": "Write a 30-second engaging script about productivity tips for a TikTok."
}' > script.json

# 2. 보이스오버 생성
infsh app run infsh/kokoro-tts --input '{
  "prompt": "<script>",
  "voice": "af_sarah"
}' > voice.json

# 3. AI 아바타 합성
infsh app run bytedance/omnihuman-1-5 --input '{
  "image_url": "https://your-avatar.jpg",
  "audio_url": "<voice-url>"
}'
```

---

## 캡션 & 해시태그 생성

```bash
infsh app run openrouter/claude-haiku-45 --input '{
  "prompt": "Write an engaging Instagram caption for a sunset beach photo. Include a hook, value, and call to action. Add 10 relevant hashtags."
}'
```

### 바이럴 훅 공식

```bash
infsh app run openrouter/claude-haiku-45 --input '{
  "prompt": "Generate 5 viral TikTok hooks for a video about morning routines. Use patterns: curiosity gap, bold claim, relatable struggle, before/after, or tutorial format."
}'
```

---

## 멀티플랫폼 일괄 생성

```bash
CONCEPT="AI 뉴스 브리핑 하이라이트"

# TikTok 세로형
infsh app run google/veo-3-1-fast --input "{
  \"prompt\": \"$CONCEPT visualization, vertical 9:16, quick cuts, text overlays style\"
}"

# Twitter 정방형
infsh app run falai/flux-dev --input "{
  \"prompt\": \"$CONCEPT infographic, square format, minimal design, shareable\"
}"

# YouTube 썸네일
infsh app run falai/flux-dev --input "{
  \"prompt\": \"$CONCEPT thumbnail, surprised person, bold text space, 16:9\"
}"
```

---

## AI 뉴스 봇 활용 포인트

- **Discord 임베드 썸네일**: `falai/flux-dev`로 AI 뉴스 토픽별 썸네일 이미지 생성
- **AI 뉴스 요약 영상**: 주간 AI 뉴스 하이라이트를 `veo-3-1-fast`로 숏폼 클립 생성
- **Twitter 자동 게시**: 큐레이션된 기사를 `twitter/post-tweet`으로 자동 배포

## 모범 사례

1. **첫 3초 훅** — 가장 흥미로운 장면으로 시작
2. **세로형 우선** — TikTok, Reels, Shorts는 9:16
3. **일관된 미학** — 브랜드 색상·스타일 통일
4. **텍스트 안전 구역** — 플랫폼 UI 요소 공간 확보
5. **일괄 생성** — 여러 콘텐츠를 한번에 생성

## 관련 스킬

```bash
npx skills add inference-sh/skills@ai-video-generation
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@twitter-automation
npx skills add inference-sh/skills@text-to-speech
npx skills add inference-sh/skills@infsh-cli
```
