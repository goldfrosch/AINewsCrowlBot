"""
선호도 심층 분석 모듈

새벽 2시에 실행되어 data/preference_profile.json 을 생성한다.
news_curation_agent.run() 이 이 파일을 읽어 큐레이션 힌트로 활용한다.

파이프라인: run_preference_analysis() → save_preference_profile()
통합:      load_preference_profile() → news_curation_agent.run(external_preferences=profile)

데이터 소스 제약:
  - 시간 윈도우 분석은 articles.posted_at 기준으로만 가능.
  - source_preferences / keywords 는 이벤트별 타임스탬프가 없어
    윈도우 필터 불가 → 전체 누적 배율 참조 용도로만 사용.
"""
import json
import sqlite3
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

KST = ZoneInfo("Asia/Seoul")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database as db

DB_PATH = Path("data/bot.db")
PREFERENCE_PROFILE_PATH = Path("data/preference_profile.json")

# 점진 확장 윈도우: 7일 → 14일 → 30일 → 전체
WINDOWS: list[int | None] = [7, 14, 30, None]
MIN_ARTICLES_WITH_FEEDBACK = 3
MIN_FEEDBACK = 3


# ── 시간 윈도우 기반 데이터 조회 ──────────────────────────────────────────────

def get_windowed_feedback(days: int | None) -> dict:
    """지정 기간 내 게시 기사의 소스·키워드별 likes/dislikes를 집계한다."""
    time_filter = ""
    if days is not None:
        time_filter = f"AND posted_at >= datetime('now', '+9 hours', '-{days} days')"

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        total = conn.execute(f"""
            SELECT COUNT(*) FROM articles
            WHERE status = 'posted'
              AND (likes > 0 OR dislikes > 0)
              {time_filter}
        """).fetchone()[0]

        by_source = conn.execute(f"""
            SELECT source,
                   SUM(likes)    AS likes,
                   SUM(dislikes) AS dislikes,
                   CAST(SUM(likes) AS REAL) /
                       MAX(SUM(likes) + SUM(dislikes), 1) AS ratio
            FROM articles
            WHERE status = 'posted'
              AND (likes > 0 OR dislikes > 0)
              {time_filter}
            GROUP BY source
            ORDER BY ratio DESC
        """).fetchall()

        raw_articles = conn.execute(f"""
            SELECT a.likes, a.dislikes, GROUP_CONCAT(k.keyword) AS keywords_csv
            FROM articles a
            LEFT JOIN article_keywords ak ON ak.article_id = a.id
            LEFT JOIN keywords k ON ak.keyword_id = k.id
            WHERE a.status = 'posted'
              AND (a.likes > 0 OR a.dislikes > 0)
              {time_filter}
            GROUP BY a.id
        """).fetchall()

        liked_titles = conn.execute(f"""
            SELECT title FROM articles
            WHERE status = 'posted' AND likes > 0
              {time_filter}
            ORDER BY likes DESC LIMIT 10
        """).fetchall()

        disliked_titles = conn.execute(f"""
            SELECT title FROM articles
            WHERE status = 'posted' AND dislikes > 0
              {time_filter}
            ORDER BY dislikes DESC LIMIT 10
        """).fetchall()

    kw_map: dict[str, dict] = {}
    for row in raw_articles:
        kw_csv = row["keywords_csv"] or ""
        keywords = [kw for kw in kw_csv.split(",") if kw]
        for kw in keywords:
            if not kw:
                continue
            entry = kw_map.setdefault(kw, {"keyword": kw, "likes": 0, "dislikes": 0})
            entry["likes"]    += row["likes"]
            entry["dislikes"] += row["dislikes"]

    by_keyword = sorted(kw_map.values(), key=lambda x: x["likes"], reverse=True)

    return {
        "days":                         days,
        "total_articles_with_feedback": total,
        "by_source":                    [dict(r) for r in by_source],
        "by_keyword":                   by_keyword,
        "most_liked_titles":            [r["title"] for r in liked_titles],
        "most_disliked_titles":         [r["title"] for r in disliked_titles],
    }


# ── 점진적 윈도우 확장 ────────────────────────────────────────────────────────

def find_sufficient_window(
    min_articles: int = MIN_ARTICLES_WITH_FEEDBACK,
) -> tuple[dict, int | None]:
    """피드백 기사가 min_articles개 이상인 가장 좁은 윈도우를 반환한다."""
    for days in WINDOWS:
        data = get_windowed_feedback(days)
        if data["total_articles_with_feedback"] >= min_articles:
            label = f"{days}일" if days else "전체 기간"
            print(f"[PreferenceAnalysis] 윈도우: {label} "
                  f"(피드백 기사 {data['total_articles_with_feedback']}개)")
            return data, days

    fallback = get_windowed_feedback(None)
    return fallback, None


def describe_window(days: int | None) -> str:
    if days is None:
        return "전체 기간"
    return f"최근 {days}일"


# ── 신뢰도 필터링 & 티어 분류 ─────────────────────────────────────────────────

def filter_reliable(items: list[dict], min_feedback: int = MIN_FEEDBACK) -> list[dict]:
    return [
        item for item in items
        if item["likes"] + item["dislikes"] >= min_feedback
    ]


def _ratio_to_tier(likes: int, dislikes: int) -> str:
    # likes/(likes+dislikes) 기준 5단계: 강선호 ≥0.8 / 선호 ≥0.6 / 중립 ≥0.4 / 비선호 ≥0.2 / 강비선호
    total = likes + dislikes
    if total == 0:
        return "중립"
    ratio = likes / total
    if ratio >= 0.8:   return "강선호"
    if ratio >= 0.6:   return "선호"
    if ratio >= 0.4:   return "중립"
    if ratio >= 0.2:   return "비선호"
    return "강비선호"


def build_tiered_profile(windowed: dict, min_feedback: int = MIN_FEEDBACK) -> dict:
    reliable_sources  = filter_reliable(windowed["by_source"],  min_feedback)
    reliable_keywords = filter_reliable(windowed["by_keyword"], min_feedback)

    src_tiers = {"강선호": [], "선호": [], "중립": [], "비선호": [], "강비선호": []}
    kw_tiers  = {"강선호": [], "선호": [], "중립": [], "비선호": [], "강비선호": []}

    for s in reliable_sources:
        src_tiers[_ratio_to_tier(s["likes"], s["dislikes"])].append(s["source"])
    for k in reliable_keywords:
        kw_tiers[_ratio_to_tier(k["likes"], k["dislikes"])].append(k["keyword"])

    return {
        "sources":                src_tiers,
        "keywords":               kw_tiers,
        "reliable_source_count":  len(reliable_sources),
        "reliable_keyword_count": len(reliable_keywords),
    }


# ── 큐레이션 힌트 생성 ────────────────────────────────────────────────────────

def build_curation_hints(
    tiered: dict,
    windowed: dict,
    total_feedback: int,
) -> dict:
    # cold_start=True 이면 에이전트가 힌트를 무시하고 다양성 위주로 동작
    cold_start = total_feedback < 10

    # 누적 피드백 수 기준 신뢰도: <10 → low / <30 → medium / 30+ → high
    if total_feedback < 10:
        confidence = "low"
    elif total_feedback < 30:
        confidence = "medium"
    else:
        confidence = "high"

    boost_sources = (tiered["sources"]["강선호"] + tiered["sources"]["선호"])[:5]
    avoid_sources = (tiered["sources"]["강비선호"] + tiered["sources"]["비선호"])[:5]

    return {
        "boost_sources":  boost_sources,
        "avoid_sources":  avoid_sources,
        "focus_keywords": tiered["keywords"]["강선호"][:10],
        "skip_keywords":  tiered["keywords"]["강비선호"][:10],
        "cold_start":     cold_start,
        "confidence":     confidence,
        "data_window":    describe_window(windowed["days"]),
    }


# ── 전체 분석 파이프라인 ──────────────────────────────────────────────────────

def run_preference_analysis(
    min_articles: int = MIN_ARTICLES_WITH_FEEDBACK,
    min_feedback: int = MIN_FEEDBACK,
) -> dict:
    """
    선호도 심층 분석 실행. 최근 7일 우선, 부족 시 자동 확장.

    반환:
      {
        "windowed":       dict,
        "tiered_profile": dict,
        "curation_hints": dict,
        "total_feedback": int,
        "data_window":    str,
        "summary":        str,
      }
    """
    windowed, used_days = find_sufficient_window(min_articles)

    agg_prefs = db.get_all_preferences()
    total_agg = sum(s["total_likes"] + s["total_dislikes"] for s in agg_prefs["sources"])

    tiered = build_tiered_profile(windowed, min_feedback)
    hints  = build_curation_hints(tiered, windowed, total_agg)

    data_window = describe_window(used_days)
    summary = (
        f"데이터 윈도우: {data_window} | "
        f"피드백 기사 {windowed['total_articles_with_feedback']}개 | "
        f"누적 피드백 {total_agg}건 | "
        f"신뢰도: {hints['confidence']}"
    )

    return {
        "windowed":       windowed,
        "tiered_profile": tiered,
        "curation_hints": hints,
        "total_feedback": total_agg,
        "data_window":    data_window,
        "summary":        summary,
    }


# ── 저장 / 로드 ───────────────────────────────────────────────────────────────

def save_preference_profile(analysis: dict) -> dict:
    """분석 결과를 data/preference_profile.json 에 저장하고 프로파일 dict를 반환한다."""
    profile = {
        "generated_at":   datetime.datetime.now(tz=KST).isoformat(),
        "curation_hints": analysis["curation_hints"],
        "tiered_profile": analysis["tiered_profile"],
        "total_feedback": analysis["total_feedback"],
        "data_window":    analysis["data_window"],
        "summary":        analysis["summary"],
    }
    PREFERENCE_PROFILE_PATH.parent.mkdir(exist_ok=True)
    PREFERENCE_PROFILE_PATH.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[PreferenceAnalysis] 저장 완료: {PREFERENCE_PROFILE_PATH}")
    return profile


def load_preference_profile() -> dict | None:
    """저장된 선호도 프로파일을 읽어 반환. 없거나 읽기 실패 시 None."""
    if not PREFERENCE_PROFILE_PATH.exists():
        return None
    try:
        return json.loads(PREFERENCE_PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[PreferenceAnalysis] 프로파일 로드 실패: {e}")
        return None


# ── CLI 진입점 ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()
    analysis = run_preference_analysis()
    profile  = save_preference_profile(analysis)

    print("\n" + "=" * 60)
    print(analysis["summary"])
    print("=" * 60)
    print(json.dumps(profile["curation_hints"], ensure_ascii=False, indent=2))
