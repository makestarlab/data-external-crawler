#!/usr/bin/env python3
"""
X (Twitter) 크롤러 - K-pop 아티스트/셀러 계정 일일 포스팅 수집

상태 저장소(단일 소스): BigQuery `makestar_ax.x_crawl_state`
  - since_id 워터마크를 여기서 읽고, 실행 후 여기에 갱신한다.
  - 로컬 JSON 상태 파일은 더 이상 사용하지 않는다 (과거 버전과의 차이점).

적재 대상: BigQuery `makestar_ax.x_posts_raw` (ELT의 raw landing 테이블)
  - 필터링 없이 수집된 모든 포스팅을 적재한다. 큐레이션(의미있는 데이터 선별)은
    별도의 후속 배치가 담당한다 (아직 미구현).

실행 방식: GitHub Actions에서 일 1회 cron으로 실행되는 것을 전제로 작성됨.
  필요한 환경변수(GitHub Secrets):
    - X_BEARER_TOKEN           : X API v2 Bearer Token
    - GCP_SERVICE_ACCOUNT_JSON : BigQuery 인증 (bq_common.py 참고)
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from google.cloud import bigquery

from bq_common import PROJECT_ID, DATASET, get_bq_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("x_crawler")

STATE_TABLE = f"{PROJECT_ID}.{DATASET}.x_crawl_state"
RAW_TABLE = f"{PROJECT_ID}.{DATASET}.x_posts_raw"
TARGETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "x_crawl_targets.json")

API_BASE = "https://api.twitter.com/2"
FIRST_RUN_LOOKBACK_DAYS = 1  # x_crawl_state에 last_tweet_id가 없는 핸들(최초 실행)에만 적용
MAX_PAGES_PER_ACCOUNT = 3    # 계정당 최대 300건 (100건 x 3페이지)
KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------
def load_state(bq):
    """x_crawl_state 테이블에서 현재 워터마크를 읽어온다. (단일 소스)"""
    rows = bq.query(f"SELECT x_handle, last_tweet_id, x_user_id FROM `{STATE_TABLE}`").result()
    return {r["x_handle"]: {"last_tweet_id": r["last_tweet_id"], "x_user_id": r["x_user_id"]} for r in rows}


def load_rows_to_bq(bq, rows):
    if not rows:
        log.info("적재할 신규 포스팅 없음")
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
    log.info("x_posts_raw에 %d행 적재 완료", len(rows))


def update_state(bq, updates):
    """updates 각 원소: x_handle, x_user_id, last_tweet_id, last_crawled_at,
    last_run_tweet_count, last_run_status, last_run_note
    -> MERGE로 x_crawl_state 갱신 (파라미터 바인딩, SQL 텍스트 조립 없음)
    """
    if not updates:
        return
    query = f"""
    MERGE `{STATE_TABLE}` T
    USING UNNEST(@updates) S
    ON T.x_handle = S.x_handle
    WHEN MATCHED THEN UPDATE SET
      T.x_user_id = S.x_user_id,
      T.last_tweet_id = COALESCE(S.last_tweet_id, T.last_tweet_id),
      T.last_crawled_at = S.last_crawled_at,
      T.last_run_tweet_count = S.last_run_tweet_count,
      T.last_run_status = S.last_run_status,
      T.last_run_note = S.last_run_note,
      T.updated_at = CURRENT_TIMESTAMP()
    """
    struct_params = []
    for u in updates:
        struct_params.append(
            bigquery.StructQueryParameter(
                None,
                bigquery.ScalarQueryParameter("x_handle", "STRING", u["x_handle"]),
                bigquery.ScalarQueryParameter("x_user_id", "STRING", u.get("x_user_id")),
                bigquery.ScalarQueryParameter("last_tweet_id", "STRING", u.get("last_tweet_id")),
                bigquery.ScalarQueryParameter("last_crawled_at", "TIMESTAMP", u["last_crawled_at"]),
                bigquery.ScalarQueryParameter("last_run_tweet_count", "INT64", u["last_run_tweet_count"]),
                bigquery.ScalarQueryParameter("last_run_status", "STRING", u["last_run_status"]),
                bigquery.ScalarQueryParameter("last_run_note", "STRING", u.get("last_run_note")),
            )
        )
    array_param = bigquery.ArrayQueryParameter("updates", "STRUCT", struct_params)
    job_config = bigquery.QueryJobConfig(query_parameters=[array_param])
    bq.query(query, job_config=job_config).result()
    log.info("x_crawl_state %d개 핸들 갱신 완료", len(updates))


# ---------------------------------------------------------------------------
# X API v2
# ---------------------------------------------------------------------------
def request_with_retry(method, url, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {os.environ['X_BEARER_TOKEN']}"
    resp = None
    for attempt in range(5):
        resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code == 429:
            reset = resp.headers.get("x-rate-limit-reset")
            wait = max(int(reset) - int(time.time()), 5) if reset else 60
            wait = min(wait, 900)
            log.warning("rate limited, %ss 대기", wait)
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        return resp
    return resp


def batch_lookup_users(usernames):
    """최대 100개씩 조회. 반환: {username_lower: user_dict}
    (과거 버전의 대소문자 불일치 버그 수정: 저장/조회 모두 lower() 기준)
    """
    result = {}
    for i in range(0, len(usernames), 100):
        chunk = usernames[i:i + 100]
        resp = request_with_retry(
            "GET",
            f"{API_BASE}/users/by",
            params={"usernames": ",".join(chunk), "user.fields": "public_metrics,verified"},
        )
        if resp is None or resp.status_code != 200:
            code = resp.status_code if resp is not None else "N/A"
            text = resp.text[:300] if resp is not None else ""
            log.error("user lookup 실패: %s %s", code, text)
            continue
        data = resp.json()
        for u in data.get("data", []):
            result[u["username"].lower()] = u
    return result


def get_tweets(user_id, since_id=None, start_time=None, max_pages=MAX_PAGES_PER_ACCOUNT):
    """since_id(있으면 우선) 또는 start_time 기준, 최대 max_pages 페이지 수집
    (기본값은 일일 크롤링용 MAX_PAGES_PER_ACCOUNT=3. 백필 스크립트는 더 큰 값을 넘겨서 씀).
    expansions는 의도적으로 생략 (인용/RT 원본 Post-read 과금 방지, BQ 조인으로 대체).
    """
    tweets = []
    pagination_token = None
    for _ in range(max_pages):
        params = {
            "max_results": 100,
            "tweet.fields": "created_at,public_metrics,referenced_tweets,entities",
            "exclude": "replies",
        }
        if since_id:
            params["since_id"] = since_id
        elif start_time:
            params["start_time"] = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        if pagination_token:
            params["pagination_token"] = pagination_token

        resp = request_with_retry("GET", f"{API_BASE}/users/{user_id}/tweets", params=params)
        if resp is None or resp.status_code != 200:
            code = resp.status_code if resp is not None else "N/A"
            text = resp.text[:300] if resp is not None else ""
            log.error("tweets fetch 실패 (user_id=%s): %s %s", user_id, code, text)
            break

        data = resp.json()
        page_tweets = data.get("data", [])
        tweets.extend(page_tweets)
        pagination_token = data.get("meta", {}).get("next_token")
        if not pagination_token:
            break
    return tweets


def build_row(run_date_kst, collected_at, target, tweet):
    ref_list = tweet.get("referenced_tweets") or []
    ref_types = {r["type"] for r in ref_list}
    ref_id = ref_list[0]["id"] if ref_list else None
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
        "scrape_method": "x_api_v2_since_id",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    now_utc = datetime.now(timezone.utc)
    run_date_kst = now_utc.astimezone(KST).strftime("%Y-%m-%d")
    collected_at = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(TARGETS_FILE, encoding="utf-8") as f:
        targets = json.load(f)
    log.info("크롤링 대상: %d개 핸들", len(targets))

    bq = get_bq_client()
    state = load_state(bq)

    handles = [t["x_handle"] for t in targets]
    users = batch_lookup_users(handles)

    all_rows = []
    state_updates = []
    summary_rows = [("x_handle", "entity_type", "status", "tweet_count")]

    try:
        for target in targets:
            handle = target["x_handle"]
            u = users.get(handle.lower())

            if not u:
                state_updates.append({
                    "x_handle": handle, "x_user_id": None, "last_tweet_id": None,
                    "last_crawled_at": collected_at, "last_run_tweet_count": 0,
                    "last_run_status": "USER_LOOKUP_FAILED", "last_run_note": None,
                })
                summary_rows.append((handle, target["entity_type"], "USER_LOOKUP_FAILED", 0))
                continue

            user_id = u["id"]
            prior = state.get(handle, {})
            since_id = prior.get("last_tweet_id")
            start_time = None if since_id else (now_utc - timedelta(days=FIRST_RUN_LOOKBACK_DAYS))

            try:
                tweets = get_tweets(user_id, since_id=since_id, start_time=start_time)
            except Exception as e:
                log.exception("크롤링 실패: %s", handle)
                state_updates.append({
                    "x_handle": handle, "x_user_id": user_id, "last_tweet_id": since_id,
                    "last_crawled_at": collected_at, "last_run_tweet_count": 0,
                    "last_run_status": "ERROR", "last_run_note": str(e)[:500],
                })
                summary_rows.append((handle, target["entity_type"], "ERROR", 0))
                continue

            row_target = {**target, "x_user_id": user_id}
            for tw in tweets:
                all_rows.append(build_row(run_date_kst, collected_at, row_target, tw))

            new_last_id = max((t["id"] for t in tweets), key=int) if tweets else since_id
            state_updates.append({
                "x_handle": handle, "x_user_id": user_id, "last_tweet_id": new_last_id,
                "last_crawled_at": collected_at, "last_run_tweet_count": len(tweets),
                "last_run_status": "SUCCESS", "last_run_note": None,
            })
            summary_rows.append((handle, target["entity_type"], "SUCCESS", len(tweets)))
            time.sleep(1)
    finally:
        # 루프 도중 예기치 못한 예외가 나더라도, 지금까지 모은 결과는 최대한 반영한다.
        load_rows_to_bq(bq, all_rows)
        update_state(bq, state_updates)

    summary_path = f"run_summary_{run_date_kst}.csv"
    with open(summary_path, "w", encoding="utf-8") as f:
        for row in summary_rows:
            f.write(",".join(str(c) for c in row) + "\n")

    ok = sum(1 for r in summary_rows[1:] if r[2] == "SUCCESS")
    failed = sum(1 for r in summary_rows[1:] if r[2] != "SUCCESS")
    log.info("실행 완료: 대상 %d개 (성공 %d / 실패 %d), 신규 포스팅 %d건 적재",
              len(targets), ok, failed, len(all_rows))

    if failed:
        # 일부 계정 실패는 있을 수 있는 일이라 워크플로 자체를 실패시키지는 않되,
        # 로그에는 남겨서 Actions 요약에서 눈에 띄게 한다.
        log.warning("실패한 핸들 %d개 - 위 로그 참고", failed)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("크롤러 실행 중 처리되지 않은 예외 발생")
        sys.exit(1)
