#!/usr/bin/env python3
"""
투어 공지 Slack 알림 - x_tour_announcements -> Slack 채널

curate_tour.py 가 새로 뽑아낸 투어/공연 공지를 아티스트별로 정리해 Slack 에 보낸다.
발송 이력은 x_tour_notified 에 남기고 안티조인으로 중복 발송을 막는다.

필요한 환경변수(GitHub Secrets):
  - SLACK_WEBHOOK_URL        : Incoming Webhook URL (권장 - 채널에 고정되고 스코프 설정 불필요)
    또는
  - SLACK_BOT_TOKEN          : xoxb- 토큰 (chat:write 스코프 필요)
  - SLACK_CHANNEL_ID         : 봇 토큰 방식일 때 채널 ID. 예: C0ADRMU4UBY (#test-delphi)
  - GCP_SERVICE_ACCOUNT_JSON : BigQuery 인증 (bq_common.py 참고)

선택:
  - SLACK_CHANNEL_LABEL      : 이력에 남길 채널 표시명 (기본 '#test-delphi')
  - TOUR_NOTIFY_MAX          : 한 번에 보낼 최대 공지 수 (기본 20)
  - TOUR_NOTIFY_LOOKBACK_DAYS: 조회 범위 (기본 3)
  - TOUR_NOTIFY_DRY_RUN      : '1' 이면 Slack 으로 보내지 않고 본문만 출력
"""
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
from google.cloud import bigquery

from bq_common import PROJECT_ID, DATASET, get_bq_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("notify_tour_slack")

TOUR_TABLE = f"{PROJECT_ID}.{DATASET}.x_tour_announcements"
NOTIFIED_TABLE = f"{PROJECT_ID}.{DATASET}.x_tour_notified"

CHANNEL_LABEL = os.environ.get("SLACK_CHANNEL_LABEL", "#test-delphi")
MAX_NOTIFY = int(os.environ.get("TOUR_NOTIFY_MAX", "20"))
LOOKBACK_DAYS = int(os.environ.get("TOUR_NOTIFY_LOOKBACK_DAYS", "3"))
DRY_RUN = os.environ.get("TOUR_NOTIFY_DRY_RUN") == "1"

# 공지 유형별 표시. 정보량이 큰 것부터 보낸다 - 하루 상한(MAX_NOTIFY)에 걸렸을 때
# 잘려 나가는 쪽이 "티켓 오픈 리마인드" 같은 덜 중요한 것이 되도록.
KIND_META = {
    "NEW_TOUR":        ("🚀", "새 투어 발표", 0),
    "NEW_CITY":        ("📍", "도시/회차 추가", 1),
    "SHOW_INFO":       ("🗓️", "일정·공연장 확정", 2),
    "SCHEDULE_CHANGE": ("⚠️", "일정 변경", 3),
    "SOLD_OUT":        ("🔥", "매진", 4),
    "TICKET_OPEN":     ("🎟️", "티켓 오픈", 5),
    "OTHER":           ("📣", "기타 공연 소식", 6),
}


def fetch_unsent(bq):
    """아직 이 채널로 안 보낸 투어 공지를 가져온다."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    return list(bq.query(
        f"""
        SELECT a.tweet_id, a.x_handle, a.artist_names, a.artist_entity_ids,
               a.tour_name, a.event_type, a.announcement_kind, a.confidence,
               a.needs_review, a.review_reason, a.ticket_vendor, a.ticket_urls,
               a.tweet_url, a.tweet_created_at, a.shows, a.ticket_opens
        FROM `{TOUR_TABLE}` a
        LEFT JOIN (
          SELECT DISTINCT tweet_id FROM `{NOTIFIED_TABLE}`
          WHERE channel = @channel AND status = 'SENT'
        ) n USING (tweet_id)
        WHERE a.is_relevant
          AND a.run_date >= @cutoff
          AND n.tweet_id IS NULL
        ORDER BY a.tweet_created_at
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("cutoff", "DATE", cutoff),
            bigquery.ScalarQueryParameter("channel", "STRING", CHANNEL_LABEL),
        ]),
    ).result())


def artist_label(row):
    names = list(row["artist_names"] or [])
    if names:
        return " × ".join(names[:3]) + (" 외" if len(names) > 3 else "")
    return f"@{row['x_handle']}"


def format_shows(shows):
    """회차를 '도시 · 공연장' 단위로 접어서 날짜를 묶는다.
    같은 도시 2회차를 두 줄로 쓰면 채널이 금방 지저분해진다."""
    if not shows:
        return []
    grouped = defaultdict(list)
    order = []
    for s in shows:
        city = s.get("city") or "도시 미정"
        country = s.get("country")
        venue = s.get("venue_name") or "공연장 미정"
        key = (f"{city}, {country}" if country else city, venue)
        if key not in grouped:
            order.append(key)
        grouped[key].append(s.get("event_date") or (s.get("date_text") or "날짜 미정"))
    lines = []
    for key in order:
        place, venue = key
        dates = ", ".join(str(d) for d in grouped[key])
        lines.append(f"`{dates}`  {place} · {venue}")
    return lines


def build_blocks(row):
    icon, kind_label, _ = KIND_META.get(row["announcement_kind"] or "OTHER", KIND_META["OTHER"])
    header = f"{icon} *{artist_label(row)}* — {kind_label}"

    lines = [header]
    title_bits = [b for b in [row["tour_name"], row["event_type"]] if b]
    if title_bits:
        lines.append("*" + "*  ·  ".join(title_bits[:1]) + "*" +
                     (f"  ·  {title_bits[1]}" if len(title_bits) > 1 else ""))

    show_lines = format_shows(row["shows"])
    if show_lines:
        lines.extend(show_lines[:6])
        if len(show_lines) > 6:
            lines.append(f"_외 {len(show_lines) - 6}개 회차_")

    opens = row["ticket_opens"] or []
    if opens:
        parts = []
        for o in opens[:3]:
            seg = " ".join(x for x in [o.get("label"), o.get("opens_at_text")] if x)
            if o.get("timezone_note"):
                seg += f" ({o['timezone_note']})"
            if seg:
                parts.append(seg)
        if parts:
            lines.append("🎟️ " + " / ".join(parts))

    if row["ticket_vendor"]:
        lines.append(f"🏷️ 예매처: {row['ticket_vendor']}")

    if row["needs_review"]:
        lines.append(f"⚠️ _확인 필요: {row['review_reason']}_")

    blocks = [{
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(lines)[:2900]},
    }]

    ctx = [f"<{row['tweet_url']}|원문 보기>", f"@{row['x_handle']}"]
    if row["tweet_created_at"]:
        kst = row["tweet_created_at"].astimezone(timezone(timedelta(hours=9)))
        ctx.append(kst.strftime("%m/%d %H:%M KST"))
    if row["confidence"] is not None:
        ctx.append(f"확신도 {row['confidence']:.2f}")
    blocks.append({"type": "context",
                   "elements": [{"type": "mrkdwn", "text": "  ·  ".join(ctx)}]})
    return blocks, header


def post_to_slack(blocks, fallback):
    """Webhook 이 있으면 그걸 쓰고, 없으면 봇 토큰으로 chat.postMessage."""
    if DRY_RUN:
        print("--- DRY RUN ---")
        print(json.dumps(blocks, ensure_ascii=False, indent=2))
        return True, None

    # .strip() 이 붙은 이유: 깃허브 시크릿에 값을 붙여넣을 때 끝에 줄바꿈이 딸려
    # 들어가는 일이 잦다. 값이 안 보이니 육안으로 못 잡고, 슬랙은 이걸 invalid_auth
    # 로만 돌려줘서 "토큰이 틀렸다" 로 오진하게 된다. (2026-09-02 실제로 겪음)
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if webhook:
        r = requests.post(webhook, json={"text": fallback, "blocks": blocks}, timeout=15)
        # Webhook 은 성공 시 본문이 "ok" 인 200 을 준다. 실패해도 200 을 주는 경우가 있어
        # 본문까지 확인한다.
        ok = r.status_code == 200 and r.text.strip() == "ok"
        return ok, None if ok else f"HTTP {r.status_code} {r.text[:200]}"

    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    channel = os.environ.get("SLACK_CHANNEL_ID", "").strip()
    if not token or not channel:
        raise RuntimeError("SLACK_WEBHOOK_URL 또는 (SLACK_BOT_TOKEN + SLACK_CHANNEL_ID) 가 필요합니다")
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        json={"channel": channel, "text": fallback, "blocks": blocks}, timeout=15,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    ok = bool(body.get("ok"))
    return ok, None if ok else f"{body.get('error') or r.text[:200]}"


def record_sent(bq, records):
    if not records:
        return
    table = bq.get_table(NOTIFIED_TABLE)
    job = bq.load_table_from_json(
        records, NOTIFIED_TABLE,
        job_config=bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            schema=table.schema,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND),
    )
    job.result()
    if job.errors:
        raise RuntimeError(f"BigQuery 로드 잡 오류: {job.errors}")


def log_auth_shape():
    """토큰 값을 노출하지 않고 '어떤 모양인지' 만 남긴다.

    2026-09-02 에 invalid_auth 로 9건이 통째로 실패했는데, 로컬 토큰은 멀쩡해서
    원인을 찾는 데 시간을 썼다. 깃허브 시크릿은 값이 안 보이니 접두사와 길이만
    찍어두면 다음엔 로그 한 줄로 끝난다.
    """
    for name in ("SLACK_WEBHOOK_URL", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID"):
        raw = os.environ.get(name, "")
        if not raw:
            log.info("%s: 미설정", name)
            continue
        clean = raw.strip()
        prefix = clean[:5] if name != "SLACK_CHANNEL_ID" else clean
        note = "" if clean == raw else f" (앞뒤 공백/줄바꿈 {len(raw) - len(clean)}자 제거함)"
        log.info("%s: %s... 길이 %d%s", name, prefix, len(clean), note)


def main():
    log_auth_shape()
    bq = get_bq_client()
    rows = fetch_unsent(bq)
    if not rows:
        log.info("발송할 신규 투어 공지 없음")
        return

    # 아티스트별로 묶고, 그 안에서는 정보량이 큰 공지부터.
    def sort_key(r):
        _, _, prio = KIND_META.get(r["announcement_kind"] or "OTHER", KIND_META["OTHER"])
        return (artist_label(r), prio, r["tweet_created_at"])
    rows = sorted(rows, key=sort_key)

    overflow = max(0, len(rows) - MAX_NOTIFY)
    targets = rows[:MAX_NOTIFY]
    log.info("발송 대상 %d건 (상한 %d, 초과 %d건은 다음 실행으로 이월)",
             len(targets), MAX_NOTIFY, overflow)

    now = datetime.now(timezone.utc).isoformat()
    records, sent, failed = [], 0, 0
    for r in targets:
        blocks, fallback = build_blocks(r)
        ok, err = post_to_slack(blocks, fallback)
        if ok:
            sent += 1
        else:
            failed += 1
            log.error("발송 실패 tweet_id=%s: %s", r["tweet_id"], err)
        records.append({"tweet_id": r["tweet_id"], "channel": CHANNEL_LABEL,
                        "sent_at": now, "status": "SENT" if ok else "FAILED",
                        "error_note": err})

    if not DRY_RUN:
        record_sent(bq, records)

    log.info("Slack %s 발송 완료 | 성공 %d · 실패 %d · 이월 %d",
             CHANNEL_LABEL, sent, failed, overflow)
    if failed:
        # 발송 실패는 FAILED 로 남으므로 다음 실행에서 자동 재시도된다.
        # 그래도 워크플로는 빨갛게 띄워서 눈에 보이게 한다.
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Slack 알림 실패")
        sys.exit(1)
