#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
data-external-crawler/evaluate_curation.py

사람이 매긴 정답(x_curation_labels) 대비 현재 프롬프트의 정확도를 잰다.
프롬프트를 고칠 때마다 이걸 돌려서 좋아졌는지 나빠졌는지를 숫자로 확인한다.

사용:
  python evaluate_curation.py                    # 전체 라벨로 평가
  python evaluate_curation.py --stratum B_통과_낮은확신
  python evaluate_curation.py --limit 30 --out eval_20260812.json
  python evaluate_curation.py --few-shot 12      # few-shot 주입 후 평가(A/B 비교용)

전제:
  curate_events.py 에 SYSTEM_PROMPT, TOOL_SCHEMA, 그리고 트윗 하나를 판정하는
  호출부가 있다. 아래 classify_one() 은 그 호출부와 같은 방식으로 Claude 를 부른다.
  curate_events.py 쪽 시그니처가 다르면 classify_one() 만 맞춰 고치면 된다.
"""
import argparse, json, os, sys, time
from collections import defaultdict

from google.cloud import bigquery
import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from curate_events import build_system_prompt, TOOL_SCHEMA  # noqa: E402

PROJECT = "makestar-dw"
LOCATION = "asia-northeast3"
MODEL = os.environ.get("CURATION_MODEL", "claude-sonnet-5")

FETCH = """
SELECT
  l.tweet_id, l.verdict, l.is_relevant AS y_relevant,
  l.artist_name AS y_artist, l.album_or_title AS y_title,
  l.seller_name AS y_seller, l.event_name AS y_event,
  l.reason, l.stratum,
  a.x_handle, a.entity_type, a.tweet_text,
  r.entities_json, rr.entities_json AS ref_entities_json
FROM `makestar-dw.makestar_ax.x_curation_labels_latest` l
JOIN `makestar-dw.makestar_ax.x_event_announcements` a USING (tweet_id)
LEFT JOIN `makestar-dw.makestar_ax.x_posts_raw` r
  ON r.tweet_id = a.tweet_id
LEFT JOIN `makestar-dw.makestar_ax.x_posts_raw` rr
  ON rr.tweet_id = r.referenced_tweet_id
WHERE l.verdict <> 'HOLD'
  {stratum_filter}
ORDER BY l.stratum, l.tweet_id
{limit_clause}
"""

FEWSHOT = """
SELECT tweet_id, verdict, reason, artist_name, album_or_title, seller_name, event_name, stratum
FROM `makestar-dw.makestar_ax.x_curation_labels_latest`
WHERE verdict <> 'HOLD' AND reason IS NOT NULL AND TRIM(reason) <> ''
QUALIFY ROW_NUMBER() OVER (PARTITION BY verdict ORDER BY FARM_FINGERPRINT(tweet_id)) <= @per
"""


def bq():
    return bigquery.Client(project=PROJECT, location=LOCATION)


def load_fewshot(client, n_per_class):
    """라벨에서 few-shot 예시를 뽑아 프롬프트 꼬리에 붙일 텍스트로 만든다."""
    if not n_per_class:
        return ""
    job = client.query(
        FEWSHOT,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("per", "INT64", n_per_class)]
        ),
    )
    rows = list(job.result())
    if not rows:
        return ""
    lines = ["", "## 사람이 매긴 판정 예시", "",
             "아래는 실제로 사람이 판정한 사례다. 같은 기준으로 판단할 것.", ""]
    for r in rows:
        lines.append("- [%s] %s" % (r["verdict"], r["reason"]))
    return "\n".join(lines)


def build_system(fewshot):
    """프로덕션과 똑같은 프롬프트를 쓴다. few-shot 만 꼬리에 붙인다."""
    base = build_system_prompt()
    return base + fewshot if fewshot else base


def classify_one(ac, system, row):
    """트윗 하나를 판정한다. curate_events.py 의 호출부와 같은 형태."""
    urls = []
    for key in ("entities_json", "ref_entities_json"):
        try:
            ents = json.loads(row.get(key) or "{}")
        except Exception:
            continue
        for u in (ents.get("urls") or []):
            v = u.get("unwound_url") or u.get("expanded_url")
            if v:
                urls.append(v)

    user = json.dumps({
        "x_handle": row["x_handle"],
        "entity_type": row["entity_type"],
        "tweet_text": row["tweet_text"],
        "link_urls": urls,
    }, ensure_ascii=False)

    for attempt in range(3):
        try:
            resp = ac.messages.create(
                model=MODEL, max_tokens=1500, system=system,
                tools=[TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": TOOL_SCHEMA["name"]},
                messages=[{"role": "user", "content": user}],
            )
            for block in resp.content:
                if block.type == "tool_use":
                    return block.input
            return {}
        except Exception as e:            # 레이트리밋/일시 오류
            if attempt == 2:
                print("  실패 %s: %s" % (row["tweet_id"], e), file=sys.stderr)
                return {}
            time.sleep(2 ** attempt)


def first_ann(payload):
    """tool 결과에서 첫 이벤트를 꺼낸다. 스키마가 배열이든 단건이든 받아낸다."""
    if not payload:
        return {"is_relevant": False}
    for key in ("announcements", "events", "items", "results"):
        arr = payload.get(key)
        if isinstance(arr, list):
            return arr[0] if arr else {"is_relevant": False}
    return payload


def norm(s):
    return (s or "").strip().lower().replace(" ", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stratum")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--few-shot", type=int, default=0,
                    help="verdict 당 few-shot 예시 개수. 0이면 주입 안 함")
    ap.add_argument("--out", default="eval_result.json")
    args = ap.parse_args()

    client = bq()
    sql = FETCH.format(
        stratum_filter=("AND l.stratum = @st" if args.stratum else ""),
        limit_clause=("LIMIT %d" % args.limit if args.limit else ""),
    )
    cfg = bigquery.QueryJobConfig()
    if args.stratum:
        cfg.query_parameters = [bigquery.ScalarQueryParameter("st", "STRING", args.stratum)]
    rows = [dict(r) for r in client.query(sql, job_config=cfg).result()]
    if not rows:
        print("라벨이 없습니다. 라벨링 도구로 먼저 정답을 넣어 주세요.")
        return

    system = build_system(load_fewshot(client, args.few_shot))
    print("평가 대상 %d건 / 모델 %s / few-shot %d" % (len(rows), MODEL, args.few_shot))
    print("system prompt %d자" % len(system))

    ac = anthropic.Anthropic()
    detail, agg = [], defaultdict(lambda: dict(n=0, hit=0, fp=0, fn=0, t_hit=0, t_n=0))

    for i, row in enumerate(rows, 1):
        pred = first_ann(classify_one(ac, system, row))
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
            "tweet_id": row["tweet_id"], "stratum": row["stratum"],
            "y": y_rel, "p": p_rel, "ok": p_rel == y_rel,
            "y_title": row["y_title"], "p_title": pred.get("album_or_title"),
            "y_seller": row["y_seller"], "p_seller": pred.get("seller_name"),
            "human_reason": row["reason"], "model_note": pred.get("extraction_note"),
            "tweet_text": (row["tweet_text"] or "")[:200],
        })
        if i % 10 == 0:
            print("  %d/%d" % (i, len(rows)))

    print("\n%-18s %5s %6s %6s %6s   %s" % ("층", "건수", "정확도", "FP", "FN", "타이틀정확도"))
    for k in sorted(agg, key=lambda x: (x == "__전체__", x)):
        a = agg[k]
        acc = a["hit"] / a["n"] * 100 if a["n"] else 0
        tacc = ("%.0f%% (%d/%d)" % (a["t_hit"] / a["t_n"] * 100, a["t_hit"], a["t_n"])
                if a["t_n"] else "—")
        print("%-18s %5d %5.1f%% %6d %6d   %s" % (k, a["n"], acc, a["fp"], a["fn"], tacc))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"model": MODEL, "few_shot": args.few_shot,
                   "summary": {k: dict(v) for k, v in agg.items()},
                   "detail": detail}, f, ensure_ascii=False, indent=2)
    print("\n틀린 건은 %s 의 detail 에서 ok=false 로 찾으세요." % args.out)


if __name__ == "__main__":
    main()
