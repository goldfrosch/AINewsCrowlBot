---
name: article-finder
description: 웹 검색으로 AI 실무 아티클·피드·스레드, AI 게임 개발 사례, 저비용 3D/그래픽스 학습자료를 탐색하는 패턴. 필라(pillar)별로 호출이 분리되며 각 필라는 자기 신선도 정책과 검색 예산을 가진다.
---

# Article Finder — 3개 필라 탐색 패턴

대상 독자: 코딩 에이전트로 프로덕션 코드를 짜면서 게임 클라이언트도 개발하는 소프트웨어 엔지니어.
`web_search_20260209` 도구로 웹 검색을 수행한다. 호출 1회 = 필라 1개.

## 필라

| 필라 | 찾는 것 | 신선도 | 배분 |
|------|---------|--------|------|
| `ai_practice` | AI 개발 실무의 지금 이 순간 | 14일 | 5 |
| `ai_game` | AI로 게임 에셋·UI·사운드를 만드는 법 + AI로 만든 게임 | 45일 | 3 |
| `graphics_3d` | 무료·저비용 3D 생성과 그래픽스 기초 | 120일 | 2 |

## 토픽 목록

### `ai_practice`

| 토픽명 | 검색 대상 |
|--------|----------|
| `claude_code_tips` | Claude Code CLI·훅·CLAUDE.md·스킬·서브에이전트·워크트리 워크플로우 |
| `agentic_coding_workflow` | 코딩 에이전트 실전 운용 — 스펙 주도 개발, 하네스, 병렬 에이전트, 리뷰 루프, 자율 PR 파이프라인 |
| `prompt_engineering` | 프롬프트 기법·CoT·스키마 강제 출력·DSPy식 자동 최적화 |
| `context_engineering` | 롱컨텍스트 전략·메모리·컴팩션·검색 배치·토큰 예산 설계 |
| `ai_coding_tools` | Copilot·Codex·Gemini CLI·Amp·오픈소스 코딩 에이전트 실전 팁 (Cursor 제외) |
| `multi_agent_orchestration` | 멀티 에이전트·supervisor-worker·LangGraph·CrewAI·에이전트 간 프로토콜 |
| `harness_engineering` | LLM 평가 하네스·프롬프트 회귀 테스트·AI CI/CD·트레이싱·옵저버빌리티 |
| `ai_code_modification` | AI 기반 대규모 코드 수정·마이그레이션·에이전틱 코드 리뷰·코드모드 |
| `dev_productivity` | LLM 코드 리뷰·테스트 생성·문서화·디버깅 워크플로우 |
| `llm_best_practices` | 컨텍스트 관리·RAG·에이전트 메모리·비용 최적화·지연 감소·로컬 추론 |
| `ai_dev_feeds` | **구독할 가치가 있는 소스** — 엔지니어링 뉴스레터 아카이브, 실무자 블로그 인덱스, RSS/Atom 피드, 큐레이션 링크 모음 |
| `community_tips` | HN 스레드·r/LocalLLaMA·r/ClaudeAI·X 스레드·Lobsters — 결론이 있는 실무 토론 |
| `korean_practitioner` | 한국어 AI 개발 아티클 (Velog·브런치·카카오/우아한형제들/토스 기술블로그) |
| `tutorials_deep_dive` | Claude/OpenAI/Gemini API 통합·에이전트 시스템 설계·밑바닥부터 구현 |

### `ai_game`

| 토픽명 | 검색 대상 |
|--------|----------|
| `ai_game_art_asset` | AI로 3D 모델·텍스처·스프라이트·복셀 아트·캐릭터 디자인 생성 |
| `ai_game_ui_sound` | AI로 게임 UI/UX·사운드 이펙트·배경 음악·애니메이션 생성 |
| `ai_game_world` | AI로 프로시저럴 생성·레벨 디자인·월드 빌딩·환경 아트 |
| `ai_game_workflow` | Unity·Unreal·Godot에 통합된 AI 도구·AI 보조 파이프라인·1인 개발자 사례 |
| `ai_made_games` | **AI로 실제 만든 게임** — 출시작·게임잼 결과물·데브로그·포스트모템. AI가 뭘 했고 어디서 막혔는지 적힌 실패담도 유효 |

### `graphics_3d`

| 토픽명 | 검색 대상 |
|--------|----------|
| `free_ai_3d_tools` | 무료·초저가 AI 3D 생성 — 오픈소스 image-to-3D/text-to-3D(Hunyuan3D, TRELLIS, TripoSR, InstantMesh), Meshy·Tripo·Rodin 무료 티어, Blender AI 애드온, 로컬 GPU 셋업, 비용 비교 |
| `graphics_fundamentals` | 프로그래머가 독학 가능한 그래픽스·아트 기초 — 셰이더, PBR 재질 이론, 라이팅, 렌더 파이프라인, 코더를 위한 Blender |
| `asset_pipeline` | 생성 에셋을 게임에 쓸 수 있게 만들기 — 리토폴로지, UV 언랩, LOD, 텍스처 베이킹, 리깅, 임포트 설정 + 무료/CC0 에셋 소스와 라이선스 |

## 반환 아티클 필드

| 필드 | 설명 |
|------|------|
| `url` | 기사 URL. **검색 결과에 그대로 나온 링크만** — 지어내면 하류에서 fetch 실패로 100% 폐기된다 |
| `title` | 제목 |
| `source` | 출처 (예: "Simon Willison's Weblog") |
| `description` | 2–3문장 요약 |
| `author` | 저자 |
| `published_at` | 발행일 (YYYY-MM-DD). 검색 결과 메타데이터·스니펫·URL 경로에서 확인되지 않으면 빈 문자열 `""` — 추측 금지, 날짜를 모른다는 이유로 기사를 버리지 말 것 |
| `curator_reason` | 선택 이유 (개발자에게 구체적으로 어떤 가치가 있는지 1문장) |

## 검색 전략

- **검색 예산을 다 쓴다.** 안 쓴 쿼리 하나 = 독자가 못 본 토픽 하나.
  쉽게 검색되는 토픽 하나로 할당량을 채우지 말고 여러 토픽에 나눠 쓴다
- 한 도메인에서 2건까지만 채택한다
- 이미 수집한 URL은 `already_collected` 집합으로 중복 방지
- 최근 게시된 URL은 `db.get_recent_posted_urls()`로 제외
- **날짜를 확인하지 못했다고 기사를 버리지 않는다** — `web_search`만으로는 발행일을
  확정할 수 없는 경우가 많고, 파이프라인이 페이지를 직접 받아 발행일을 재검증한다.
  빈 배열을 반환하는 것이 가장 나쁜 결과다
- Unreal·Unity·Godot을 동등하게 탐색하며 특정 엔진 포함을 강제하지 않음
- 논문·프리프린트·학술 초록·광고·보도자료·얕은 listicle·콘텐츠 팜 제외
- 중국어 본문은 제외하되 중국계 출처의 영어·한국어 본문은 허용

## 필라별 우선순위

**`ai_practice`** — 이번 주에 나온 1차 기록이 최고다. 개별 아티클뿐 아니라
뉴스레터 호·블로그 인덱스·고신호 토론 스레드도 유효한 결과다(계속 따라갈 소스를 찾는 것도 목적).
모델 출시·투자 소식·"AI 도구 톱10"은 제외.

**`ai_game`** — "어떤 툴을 어떤 설정으로 돌려 무엇이 나왔고 어디를 손봤는지"가 있어야 한다.
게임 플레이 AI(체스·바둑 봇, NPC 행동트리)는 제외 — 게임 **제작**에 쓰는 내용만.

**`graphics_3d`** — 가장 싼 경로 우선. 오픈소스·로컬 실행 > 무료 티어 > 유료 SaaS.
가격·무료 한도·라이선스·VRAM 요구사항이 적혀 있으면 가치가 크게 올라간다.
에버그린 학습자료 환영 — 오래됐어도 여전히 맞으면 좋은 결과다.

## 고품질 소스 예시

**AI 실무**: Simon Willison's Weblog, Hamel Husain, eugeneyan.com, jxnl.co, Lilian Weng,
Anthropic Engineering, OpenAI Cookbook, LangChain Blog, Modal, Pragmatic Engineer,
HackerNews AI 스레드, r/LocalLLaMA, Velog·브런치·카카오/토스 기술블로그

**게임 · 그래픽스**: 80 Level, Game Developer (GDC), Unreal/Unity/Godot 공식 문서,
Blender 공식·커뮤니티, NVIDIA Developer, LearnOpenGL, The Book of Shaders, iquilezles.org,
Polyhaven·itch.io(CC0 에셋), r/gamedev·r/IndieGaming, GameFromScratch, Sebastian Lague
