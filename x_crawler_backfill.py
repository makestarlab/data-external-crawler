#!/usr/bin/env python3
"""
X 크롤러 - 일회성 백필 스크립트

목적: x_crawler.py의 정규 일일 크롤링은 2026-07-29부터 시작됐다. 그 이전 기간
(기본: 2026-07-01 ~ 크롤러 시작일 전날)의 과거 포스팅을 한 번 소급 수집해서
x_posts_raw를 채운다.

x_crawler.py(일일 정규 크롤러)와의 차이점:
  - since_id 워터마크를 무시하고, 항상 start_time(BACKFILL_START_DATE)부터 조회한다.
  - 계정당 최대 페이지 수를 늘린다(BACKFILL_MAX_PAGES, 기본 20페이지=최대 2000건).
    참고: X API의 GET /2/users/:id/tweets 엔드포인트는 계정당 최근 3200건까지만
    반환 가능하므로, 한 달치 정도 백필에는 20페이지면 충분하지만 트윗이 매우 많은
    계정은 3200건(약 32페이지) 이전 데이터는 애초에 API로 가져올 수 없다.
  - x_crawl_state(워터마크)는 절대 갱신하지 않는다. 일일 정규 크롤러가 참조하는
    since_id를 과거 데이터로 되돌리면(작은 tweet_id로 덮어쓰면) 다음 정규 실행 때
    최신 포스팅을 놓치게 되므로, 백필은 read-only하게 상태를 남겨둔다.
  - x_posts_raw에 이미 있는 tweet_id는 걸러내고 신규 행만 적재한다(정규 크롤러와
    수집 기간이 겹칠 수 있으므로 중복 방지).
  - run_date는 "백필을 실행한 날짜"가 아니라 각 트윗의 실제 작성일(KST 기준)로
    채운다. 그래야 파티션/집계가 실제 포스팅 시점을 반영한다.

필요한 환경변수(GitHub Secrets, x_crawler.py와 동일):
  - X_BEARER_TOKEN
  - GCP_SERVICE_ACCOUNT_JSON

선택 환경변수:
  - BACKFILL_START_DATE : 'YYYY-MM-DD' (기본값 '2026-07-01')
  - BACKFILL_MAX_PAGES  : 계정당 최대 페이지 수 (기본값 20)
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from google.cloud import bigquery

from bq_common import PROJECT_ID, DATASET, get_bq_client
from x_crawler import (
    TARGETS_FILE,
    RAW_TABLE,
    batch_lookup_users,
    get_tweets,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("x_crawler_backfill")

KST = timezone(timedelta(hours=9))
DEFAULT_START_DATE = "2026-07-01"
DEFAULT_MAX_PAGES = 20


def parse_start_date():
    raw = os.environ.get("BACKFILL_START_DATE", DEFAULT_START_DATE)
    return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def fetch_existing_tweet_ids(bq, handles):
    """이미 x_posts_raw에 적재된 tweet_id를 계정별로 조회해서 중복 적재를 막는다."""
    if not handles:
        return set()
    rows = bq.query(
        f"""
        SELECT tweet_id
        FROM `{RAW_TABLE}`
        WHERE x_handle IN UNNEST(@handles)
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("handles", "STRING", handles),
        ]),
    ).result()
    return {r["tweet_id"] for r in rows}


def build_backfill_row(collected_at, target, tweet):
    """x_crawler.build_row와 거의 동일하지만, run_date를 '백필 실행일'이 아니라
    트윗의 실제 작성일(KST)로 채운다."""
    ref_list = tweet.get("referenced_tweets") or []
    ref_types = {r["type"] for r in ref_list}
    ref_id = ref_list[0]["id"] if ref_list else None

    created_at = tweet.get("created_at")
    if created_at:
        created_dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%fZ" if "." in created_at else "%Y-%m-%dT%H:%M:%SZ")
        created_dt = created_dt.replace(tzinfo=timezone.utc)
        run_date_kst = created_dt.astimezone(KST).strftime("%Y-%m-%d")
    else:
        run_date_kst = datetime.now(timezone.utc).astimezone(KST).strftime("%Y-%m-%d")

    return {
        "run_date": run_date_kst,
        "collected_at": collected_at,
        "entity_id": target["entity_id"],
        "entity_type": target["entity_type"],
        "x_handle": target["x_handle"],
        "x_user_id": target.get("x_user_id"),
        "tweet_id": tweet["id"],
        "tweet_url": f"https://x.com/{target['x_handle']}/status/{tweet['id']}",
        "tweet_text": tweet.get("text"),
        "tweet_created_at": tweet.get("created_at"),
        "is_retweet": "retweeted" in ref_types,
        "is_quote": "quoted" in ref_types,
        "is_reply": "replied_to" in ref_types,
        "referenced_tweet_id": ref_id,
        "public_metrics_json": json.dumps(tweet.get("public_metrics", {}), ensure_ascii=False),
        "entities_json": json.dumps(tweet.get("entities", {}), ensure_ascii=False),
        "raw_json": json.dumps(tweet, ensure_ascii=False),
        "scrape_method": "x_api_v2_backfill_start_time",
    }


def load_rows_to_bq(bq, rows):
    if not rows:
        log.info("적재할 신규(백필) 포스팅 없음")
        return
    table = bq.get_table(RAW_TABLE)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=table.schema,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = bq.load_table_from_json(rows, RAW_TABLE, job_config=job_config)
    job.result()
    if job.errors:
        raise RuntimeError(f"BQ load job errors: {job.errors}")
    log.info("x_posts_raw에 %d행 백필 적재 완료", len(rows))


def main():
    start_time = parse_start_date()
    max_pages = int(os.environ.get("BACKFILL_MAX_PAGES", DEFAULT_MAX_PAGES))
    now_utc = datetime.now(timezone.utc)
    collected_at = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    log.info("백필 시작: start_time=%s, 계정당 최대 %d페이지", start_time.isoformat(), max_pages)

    with open(TARGETS_FILE, encoding="utf-8") as f:
        targets = json.load(f)
    log.info("백필 대상: %d개 핸들", len(targets))

    bq = get_bq_client()
    handles = [t["x_handle"] for t in targets]
    users = batch_lookup_users(handles)
    existing_ids = fetch_existing_tweet_ids(bq, handles)
    log.info("기존 x_posts_raw에 이미 있는 tweet_id %d건 (중복 제외 대상)", len(existing_ids))

    all_rows = []
    summary_rows = [("x_handle", "entity_type", "status", "fetched", "new_inserted")]

    for target in targets:
        handle = target["x_handle"]
        u = users.get(handle.lower())
        if not u:
            log.error("%s: user lookup 실패, 건너뜀", handle)
            summary_rows.append((handle, target["entity_type"], "USER_LOOKUP_FAILED", 0, 0))
            continue

        user_id = u["id"]
        try:
            tweets = get_tweets(user_id, since_id=None, start_time=start_time, max_pages=max_pages)
        except Exception as e:
            log.exception("백필 실패: %s", handle)
            summary_rows.append((handle, target["entity_type"], "ERROR", 0, 0))
            continue

        row_target = {**target, "x_user_id": user_id}
        new_rows = []
        for tw in tweets:
            if tw["id"] in existing_ids:
                continue
            new_rows.append(build_backfill_row(collected_at, row_target, tw))
            existing_ids.add(tw["id"])  # 같은 실행 내 중복도 방지

        all_rows.extend(new_rows)
        summary_rows.append((handle, target["entity_type"], "SUCCESS", len(tweets), len(new_rows)))
        log.info("%s: %d건 조회, %d건 신규 적재 대상", handle, len(tweets), len(new_rows))
        time.sleep(1)

    load_rows_to_bq(bq, all_rows)
    log.info("주의: 백필은 x_crawl_state(since_id 워터마크)를 갱신하지 않는다 - 정규 크롤러 상태는 그대로 유지됨")

    summary_path = f"run_summary_backfill_{start_time.strftime('%Y%m%d')}.csv"
    with open(summary_path, "w", encoding="utf-8") as f:
        for row in summary_rows:
            f.write(",".join(str(c) for c in row) + "\n")

    total_fetched = sum(r[3] for r in summary_rows[1:])
    total_new = sum(r[4] for r in summary_rows[1:])
    log.info("백필 완료: 대상 %d개 계정, 총 조회 %d건, 신규 적재 %d건 (나머지는 기존과 중복이라 스킵)",
              len(targets), total_fetched, total_new)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("백필 실행 중 처리되지 않은 예외 발생")
        sys.exit(1)
