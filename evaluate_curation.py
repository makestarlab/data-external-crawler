#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
data-external-crawler/evaluate_curation.py

사람이 매긴 정답(x_curation_labels) 대비 현재 프롬프트의 정확도를 잰다.
프롬프트나 판정 기준을 고칠 때마다 돌려서 좋아졌는지 나빠졌는지를 숫자로 확인한다.

핵심 원칙: **프로덕션과 똑같은 조건으로 묻는다.**
  curate_events.call_claude() 를 그대로 호출한다. 시스템 프롬프트, 아티스트 로스터,
  "이미 등록된 최근 이벤트 목록", 링크 도메인 — 전부 프로덕션과 같은 것이 들어간다.
  이 조건을 하나라도 빼면 후속 트윗(`🔗 이벤트 상품 : …` 한 줄짜리)처럼
  맥락이 있어야 풀리는 건이 프로덕션에선 맞는데 평가에선 틀리게 나온다.
  (2026-08-12 실제로 이 문제로 FN 2건이 과대 계상됐다.)

누수 방지: 최근 이벤트 목록에서 **평가 대상 트윗 자신이 만든 행은 뺀다.**
  안 빼면 정답을 목록으로 흘려주는 셈이 된다.

돌리는 곳:
  GitHub Actions > "Curation Eval" > Run workflow  ← 권장. 시크릿이 이미 거기 있다.
  로컬은 pip install -r requirements.txt + GCP_SERVICE_ACCOUNT_JSON + ANTHROPIC_API_KEY 필요.

사용:
  python evaluate_curation.py
  python evaluate_curation.py --stratum B_통과_낮은확신
  python evaluate_curation.py --few-shot 12      # few-shot 주입 후 평가(A/B 비교용)
"""
import argparse, json, os, sys
from collections import defaultdict

from google.cloud import bigquery
import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import curate_events as ce  # noqa: E402
from bq_common import get_bq_client  # noqa: E402

PROJECT = "makestar-dw"
LOCATION = "asia-northeast3"
MODEL = os.environ.get("CURATION_MODEL", "claude-sonnet-5")

FETCH = """
SELECT
  l.tweet_id, l.verdict, l.is_relevant AS y_relevant,
  l.artist_name AS y_artist, l.album_or_title AS y_title,
  l.seller_name AS y_seller, l.event_name AS y_event,
  l.reason, l.stratum,
  r.x_handle, r.entity_id, r.entity_type, r.tweet_text, r.tweet_created_at,
  r.entities_json, ref.entities_json AS ref_entities_json
FROM `makestar-dw.makestar_ax.x_curation_labels_latest` l
JOIN `makestar-dw.makestar_ax.x_posts_raw` r USING (tweet_id)
LEFT JOIN (
  SELECT tweet_id, ANY_VALUE(entities_json) AS entities_json
  FROM `makestar-dw.makestar_ax.x_posts_raw` GROUP BY tweet_id
) ref ON ref.tweet_id = r.referenced_tweet_id
WHERE l.verdict <> 'HOLD'
  {stratum_filter}
ORDER BY r.x_handle, r.tweet_created_at
{limit_clause}
"""

# 프로덕션의 fetch_recent_representatives 와 같되, 평가 대상 트윗이 만든 행은 제외한다.
RECENT = """
SELECT x_handle, run_date, event_key, event_group_id, artist_name, album_or_title, event_name
FROM `makestar-dw.makestar_ax.x_event_announcements`
WHERE is_representative
  AND run_date >= @cutoff
  AND x_handle IN UNNEST(@handles)
  AND tweet_id NOT IN UNNEST(@exclude)
"""

# 라벨된 글과 같은 계정·같은 날에 올라온 형제 글. 배치 맥락을 프로덕션과 맞추는 데 쓴다.
SIBLINGS = """
SELECT r.x_handle, r.tweet_id, r.tweet_created_at, r.tweet_text,
       r.entities_json, ref.entities_json AS ref_entities_json
FROM `makestar-dw.makestar_ax.x_posts_raw` r
LEFT JOIN (
  SELECT tweet_id, ANY_VALUE(entities_json) AS entities_json
  FROM `makestar-dw.makestar_ax.x_posts_raw` GROUP BY tweet_id
) ref ON ref.tweet_id = r.referenced_tweet_id
WHERE CONCAT(r.x_handle, '|', CAST(DATE(r.tweet_created_at) AS STRING)) IN UNNEST(@keys)
"""

FEWSHOT = """
SELECT tweet_id, verdict, reason
FROM `makestar-dw.makestar_ax.x_curation_labels_latest`
WHERE verdict <> 'HOLD' AND reason IS NOT NULL AND TRIM(reason) <> ''
QUALIFY ROW_NUMBER() OVER (PARTITION BY verdict ORDER BY FARM_FINGERPRINT(tweet_id)) <= @per
"""


def bq():
    try:
        return get_bq_client()
    except KeyError:
        print("서비스 계정 시크릿이 없어 gcloud 기본 인증으로 붙습니다.", file=sys.stderr)
        return bigquery.Client(project=PROJECT, location=LOCATION)


def load_fewshot(client, n_per_class):
    """라벨의 '판단 이유'를 few-shot 예시로 만들어 시스템 프롬프트 꼬리에 붙일 텍스트."""
    if not n_per_class:
        return ""
    rows = list(client.query(FEWSHOT, job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("per", "INT64", n_per_class)]
    )).result())
    if not rows:
        return ""
    lines = ["", "", "## 사람이 매긴 판정 예시", "",
             "실제로 사람이 판정한 사례다. 같은 기준으로 판단할 것.", ""]
    lines += ["- [%s] %s" % (r["verdict"], r["reason"]) for r in rows]
    return "\n".join(lines)


def fetch_recent_excluding(client, handles, exclude_ids, oldest_anchor):
    """평가 대상이 처리되던 그 시점의 '최근 이벤트 목록'을 만들기 위한 원본을 긁어온다.

    [2026-08-26] 예전에는 cutoff 를 **오늘 - 45일** 로 잡았다. 라벨은 고정인데 기준일이
      매일 밀리니, 시간이 갈수록 원 이벤트가 창 밖으로 빠져나가 링크만 붙은 후속 트윗이
      풀리지 않게 된다. 프롬프트를 안 건드려도 점수가 계속 떨어졌다
      (96.7% → 95.0% → 93.3%). 프롬프트 문제가 아니라 자(尺)가 움직인 것이다.

      게다가 상한이 없어서 **평가 대상 트윗보다 나중에 생긴 이벤트**까지 목록에 들어갔다.
      정답을 미래에서 흘려주는 셈이라 반대 방향 누수였다.

      지금은 계정별로 그 계정 평가 트윗의 마지막 날짜를 기준(anchor)으로 삼아
      [anchor-45일, anchor] 구간만 준다. 실제 프로덕션이 그때 봤던 것과 같아진다.
    """
    cutoff = (oldest_anchor - ce.timedelta(days=ce.RECENT_WINDOW_DAYS)).isoformat()
    rows = list(client.query(RECENT, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("cutoff", "DATE", cutoff),
        bigquery.ArrayQueryParameter("handles", "STRING", handles),
        bigquery.ArrayQueryParameter("exclude", "STRING", exclude_ids),
    ])).result())
    by_handle = defaultdict(list)
    for r in rows:
        by_handle[r["x_handle"]].append(dict(r))
    return by_handle


def recent_window_for(all_recent, x_handle, anchor):
    """계정 하나에 대해 [anchor-45일, anchor] 구간만 남기고 프로덕션과 같은 상한을 건다."""
    lo = anchor - ce.timedelta(days=ce.RECENT_WINDOW_DAYS)
    picked = [e for e in all_recent.get(x_handle, []) if lo <= e["run_date"] <= anchor]
    picked.sort(key=lambda e: e["run_date"], reverse=True)
    return picked[:ce.MAX_RECENT_PER_HANDLE]


def fetch_siblings(client, rows):
    """라벨된 글과 같은 계정·같은 날의 원문 글을 (계정, 날짜) 별로 모아 온다.

    [2026-08-26] `🔗 이벤트 상품 : …` 처럼 링크만 있는 후속 글은 본문만으로 판정이 안 된다.
      프로덕션에서는 원 이벤트 글과 **같은 배치**에 들어가서 풀린다 — 트윗 id 가 연속인 걸
      보면 같은 초에 올라온 형제 글이다. 그런데 평가에서는 라벨된 글만 배치에 넣었다.
      맥락을 통째로 빼고 물어본 셈이라, 프로덕션은 맞히는 건을 평가만 계속 틀렸다
      (B층 FN 2건이 프롬프트를 어떻게 고쳐도 안 없어졌다).
    """
    keys = sorted({"%s|%s" % (r["x_handle"], r["tweet_created_at"].date().isoformat())
                   for r in rows})
    out = defaultdict(dict)
    got = list(client.query(SIBLINGS, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("keys", "STRING", keys),
    ])).result())
    for r in got:
        d = dict(r)
        out[(d["x_handle"], d["tweet_created_at"].date())][d["tweet_id"]] = d
    return {k: list(v.values()) for k, v in out.items()}


def norm(s):
    return (s or "").strip().lower().replace(" ", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stratum")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--few-shot", type=int, default=0,
                    help="판정별 few-shot 예시 개수. 0이면 주입 안 함(기준선)")
    ap.add_argument("--out", default="eval_result.json")
    args = ap.parse_args()

    client = bq()
    cfg = bigquery.QueryJobConfig()
    if args.stratum:
        cfg.query_parameters = [bigquery.ScalarQueryParameter("st", "STRING", args.stratum)]
    sql = FETCH.format(
        stratum_filter=("AND l.stratum = @st" if args.stratum else ""),
        limit_clause=("LIMIT %d" % args.limit if args.limit else ""),
    )
    rows = [dict(r) for r in client.query(sql, job_config=cfg).result()]
    if not rows:
        print("라벨이 없습니다. 라벨링 도구로 먼저 정답을 넣어 주세요.")
        return

    # few-shot 은 curate_events 의 프롬프트 캐시에 직접 얹는다.
    # 이렇게 해야 call_claude 가 프로덕션 경로 그대로 돌면서 예시만 추가된다.
    fewshot = load_fewshot(client, args.few_shot)
    if fewshot:
        ce._SYSTEM_PROMPT_CACHE = ce.build_system_prompt() + fewshot
    system = ce.build_system_prompt()

    ce.MODEL = MODEL
    name_to_id, id_to_name, artist_roster = ce.load_entity_lookup(client)

    by_handle = defaultdict(list)
    for r in rows:
        by_handle[r["x_handle"]].append(r)
    all_ids = [r["tweet_id"] for r in rows]

    # 같은 계정·같은 날 형제 글을 먼저 가져온다. 배치 맥락이 프로덕션과 같아야 한다.
    siblings = fetch_siblings(client, rows)

    # 최근 이벤트 목록에서는 **이번 배치에 들어가는 글이 만든 행을 전부** 뺀다.
    #   형제 글의 결과까지 목록에 남아 있으면, 프로덕션 실행 시점엔 아직 없던 정답을
    #   미리 흘려주는 셈이 된다.
    batch_ids = sorted(set(all_ids) | {s["tweet_id"] for v in siblings.values() for s in v})

    groups = defaultdict(list)                      # (계정, 날짜) -> 라벨된 행
    for r in rows:
        groups[(r["x_handle"], r["tweet_created_at"].date())].append(r)
    recent_all = fetch_recent_excluding(client, list(by_handle), batch_ids,
                                        min(d for _, d in groups))

    print("평가 대상 %d건 / 계정 %d개 / 모델 %s / few-shot %d"
          % (len(rows), len(by_handle), MODEL, args.few_shot))
    print("system prompt %d자" % len(system))

    ac = anthropic.Anthropic()
    detail, agg = [], defaultdict(lambda: dict(n=0, hit=0, fp=0, fn=0, t_hit=0, t_n=0))
    done = 0

    for (x_handle, day), labeled in groups.items():
        entity_type = labeled[0]["entity_type"]
        known_artist_name = id_to_name.get(labeled[0]["entity_id"]) if entity_type == "ARTIST" else None
        recent_events = recent_window_for(recent_all, x_handle, day)

        # 라벨된 글 + 같은 날 형제 글. 점수는 라벨된 글만 매긴다.
        seen = {r["tweet_id"] for r in labeled}
        batch = list(labeled) + [s for s in siblings.get((x_handle, day), [])
                                 if s["tweet_id"] not in seen]
        batch.sort(key=lambda t: t["tweet_created_at"])
        batch = batch[:ce.MAX_TWEETS_PER_CALL * 2]
        for r in labeled:                      # 잘려 나간 라벨 건이 없도록 되붙인다
            if r["tweet_id"] not in {t["tweet_id"] for t in batch}:
                batch.append(r)

        preds = {}
        try:
            # 프로덕션과 똑같은 호출. 배치 단위도 프로덕션과 같다.
            for chunk in ce.chunked(batch, ce.MAX_TWEETS_PER_CALL):
                for res in ce.call_claude(ac, x_handle, entity_type, known_artist_name,
                                          artist_roster, recent_events, chunk):
                    preds[str(res.get("tweet_id"))] = res
        except Exception as e:
            print("  %s 실패: %s" % (x_handle, e), file=sys.stderr)

        for row in labeled:
            pred = preds.get(row["tweet_id"], {})
            p_rel = bool(pred.get("is_relevant"))
            y_rel = bool(row["y_relevant"])

            for k in (row["stratum"], "__전체__"):
                a = agg[k]
                a["n"] += 1
                if p_rel == y_rel:
                    a["hit"] += 1
                elif p_rel and not y_rel:
                    a["fp"] += 1
                else:
                    a["fn"] += 1
                if y_rel and row["y_title"]:
                    a["t_n"] += 1
                    if norm(pred.get("album_or_title")) == norm(row["y_title"]):
                        a["t_hit"] += 1

            detail.append({
                "tweet_id": row["tweet_id"], "x_handle": x_handle, "stratum": row["stratum"],
                "y": y_rel, "p": p_rel, "ok": p_rel == y_rel,
                "missing_from_response": row["tweet_id"] not in preds,
                "y_title": row["y_title"], "p_title": pred.get("album_or_title"),
                "y_seller": row["y_seller"], "p_seller": pred.get("seller_name"),
                "human_reason": row["reason"], "model_note": pred.get("note"),
                "confidence": pred.get("confidence"),
                "recent_events_given": len(recent_events),
                "batch_size_given": len(batch),
                "tweet_text": (row["tweet_text"] or "")[:200],
            })
            done += 1
            if done % 10 == 0:
                print("  %d/%d" % (done, len(rows)))

    print("\n%-18s %5s %6s %6s %6s   %s" % ("층", "건수", "정확도", "FP", "FN", "타이틀정확도"))
    for k in sorted(agg, key=lambda x: (x == "__전체__", x)):
        a = agg[k]
        acc = a["hit"] / a["n"] * 100 if a["n"] else 0
        tacc = ("%.0f%% (%d/%d)" % (a["t_hit"] / a["t_n"] * 100, a["t_hit"], a["t_n"])
                if a["t_n"] else "—")
        print("%-18s %5d %5.1f%% %6d %6d   %s" % (k, a["n"], acc, a["fp"], a["fn"], tacc))

    missing = sum(1 for d in detail if d["missing_from_response"])
    if missing:
        print("\n주의: 응답에 안 담겨 온 트윗 %d건. 배치가 크거나 max_tokens 가 모자란 신호다." % missing)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"model": MODEL, "few_shot": args.few_shot,
                   "summary": {k: dict(v) for k, v in agg.items()},
                   "detail": detail}, f, ensure_ascii=False, indent=2)
    print("\n틀린 건은 %s 의 detail 에서 ok=false 로 찾으세요." % args.out)


if __name__ == "__main__":
    main()
