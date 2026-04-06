# AINewsCrawlBot — 전체 파이프라인

## 개요

봇은 두 개의 스케줄 작업과 실시간 반응 처리로 구성된 **자동 학습 루프**를 실행한다.

```
[02:00 KST] 선호도 분석 ──────────────────────────────────────┐
[06:00 KST] 뉴스 브리핑 ←── preference_profile.json ──────────┘
[실시간]    👍/👎 반응 → DB 업데이트 → 다음 브리핑에 반영
```

---

## 1. 선호도 분석 파이프라인 (새벽 2:00 KST)

```
bot.daily_preference_analysis()
  │
  └── agents/preference_analysis.py: run_preference_analysis()
        │
        ├── [1] find_sufficient_window()
        │     7일 → 14일 → 30일 → 전체 순으로 윈도우 확장
        │     최소 3개 기사 + 3개 피드백 조건 만족 시 중단
        │
        ├── [2] get_windowed_feedback(days)
        │     DB: articles.likes/dislikes, keywords 배율 집계
        │     소스별·키워드별 likes/dislikes 합산
        │
        ├── [3] build_tiered_profile()
        │     ratio = likes / (likes + dislikes)
        │     강선호(≥0.8) / 선호(≥0.6) / 중립(≥0.4) / 비선호(≥0.2) / 강비선호
        │
        ├── [4] build_curation_hints()
        │     cold_start: total_feedback < 10 → 다양성 우선
        │     confidence: low/medium/high
        │     boost_sources, avoid_sources, focus_keywords, skip_keywords
        │
        └── [5] save_preference_profile()
              → data/preference_profile.json
```

---

## 2. 뉴스 브리핑 파이프라인 (오전 6:00 KST)

```
bot.daily_brief()
  │
  └── bot._research_and_post(count=5)
        │
        ├── [A] pipeline.run_curation_pipeline(count=5)
        │     │
        │     ├── [A-1] load_preference_profile()
        │     │         data/preference_profile.json 읽기
        │     │
        │     ├── [A-2] database.get_todays_posted_urls()
        │     │         오늘 이미 게시된 URL 목록 (중복 방지)
        │     │
        │     ├── [A-3] curator.research(count, exclude_urls, preferences)
        │     │     │
        │     │     ├── [Try] agents/news_curation_agent.py: run()
        │     │     │     │
        │     │     │     ├── [Tool 1] analyze_preferences
        │     │     │     │     DB에서 소스·키워드 배율 읽기
        │     │     │     │     external_preferences 병합
        │     │     │     │
        │     │     │     ├── [Tool 2] find_ai_articles (반복 가능)
        │     │     │     │     토픽별 anthropic web_search 호출
        │     │     │     │     결과를 누적 (articles_pool)
        │     │     │     │
        │     │     │     └── [Tool 3] review_articles
        │     │     │           규칙 필터: 스팸 정규식, AI 키워드, URL 중복
        │     │     │           Claude 검토: 광고·중복 탐지, 선호도 반영
        │     │     │           최종 target_count개 선별
        │     │     │
        │     │     └── [Fallback] _fallback_research()
        │     │           단순 웹 검색 1회 (RateLimit 시 30초 대기 재시도)
        │     │
        │     ├── [A-4] database.upsert_article() × N
        │     │         URL 기준 중복 방지 저장
        │     │         반환: (new_count, raw_count)
        │     │
        │     ├── [A-5] ranker.rank_articles(articles)
        │     │         final_score = normalize(platform_score)
        │     │                     × source_multiplier
        │     │                     × avg(keyword_multipliers)
        │     │         final_score 내림차순 정렬
        │     │         database.update_final_scores() 저장
        │     │
        │     └── 상위 count개 반환
        │
        └── [B] Discord 게시
              헤더 메시지 전송
              기사별 Embed 생성 (_make_embed)
                - 색상·이모지는 source 기반
                - 필드: 제목, 설명, 저자, 발행일, 플랫폼 점수
              각 메시지에 👍/👎 반응 자동 추가
              database.mark_as_posted(article_id, message_id, channel_id)
```

---

## 3. 실시간 반응 파이프라인

```
Discord: on_raw_reaction_add 이벤트
  │
  ├── 봇 자신의 반응 무시
  ├── emoji ≠ 👍/👎 → 무시
  │
  └── ranker.apply_feedback(message_id, liked)
        │
        ├── database.get_article_by_message_id(message_id)
        ├── database.update_article_reaction(article_id, liked)
        ├── database.update_source_preference(source, liked)
        │     liked:  multiplier += 0.15  (max 5.0)
        │     disliked: multiplier -= 0.15  (min 0.1)
        └── database.update_keyword_preference(keyword, liked) × 각 키워드
              liked:  multiplier += 0.05  (max 5.0)
              disliked: multiplier -= 0.05  (min 0.1)
```

---

## 4. 명령어 파이프라인

### `!more [n]`
```
bot._research_and_post(count=n)  ← 브리핑 파이프라인과 동일
```

### `!crawl` (관리자)
```
bot._research_and_post(count=ARTICLES_PER_POST)
```

### `!analyze`
```
agents/preference_analysis.py: run_preference_analysis()
  → save_preference_profile()
  → Discord: 분석 결과 Embed 게시
```

### `!stats`
```
database.get_stats()
database.get_all_preferences()
  → Discord: 통계 + 상위 소스/키워드 선호도 Embed
```

### `!tokens`
```
token_tracker.get_today_token_stats()
token_tracker.get_average_daily_stats()
token_tracker.get_window_stats()
  → Discord: 토큰 사용량 Embed
```

---

## 5. 데이터 흐름 요약

```
웹 (AI 뉴스 소스)
    │
    ▼  [anthropic web_search]
news_curation_agent (3단계 agentic loop)
    │
    ▼  [Article list]
curator.research()
    │
    ▼  [upsert]
database (articles 테이블)
    │
    ▼  [rank]
ranker.rank_articles()  ←── source_preferences, keywords 테이블
    │
    ▼  [top N]
bot._research_and_post()
    │
    ▼  [Discord Embed]
Discord 채널
    │
    ▼  [👍/👎]
ranker.apply_feedback()
    │
    ▼  [±0.15 / ±0.05]
source_preferences, keywords 테이블
    │
    ▼  [새벽 2시]
preference_analysis.run_preference_analysis()
    │
    ▼
data/preference_profile.json  ──▶  다음 큐레이션 힌트로 재주입
```

---

## 6. 에러 처리 전략

| 상황 | 처리 |
|------|------|
| `anthropic.RateLimitError` | 30초 대기 후 1회 재시도 |
| 에이전트 실패 | `_fallback_research()` (단순 웹 검색) |
| DB 오류 | 예외 로깅, 작업 스킵 |
| Discord 게시 실패 | 예외 로깅, 스킵 |
| preference_profile.json 없음 | 선호도 없이 진행 (cold_start 모드) |

---

## 7. CLI 테스트 모드

```bash
python dry_run.py [--count 5] [--verbose] [--db path/to/bot.db]
```

Discord 없이 `pipeline.run_curation_pipeline()` 실행 → 결과 콘솔 출력

```bash
python agents/news_curation_agent.py
```

에이전트 단독 실행 (독립 테스트)
