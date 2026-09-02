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
from collections import Counter, defaultdict
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


KST = timezone(timedelta(hours=9))

# 한 스레드 안에 들어가는 상세 줄 수 상한. 슬랙 section 블록은 3000자 제한이라
# 넉넉히 잡아 배치를 나눈다.
THREAD_CHAR_BUDGET = 2600


def artist_label(row):
    names = list(row["artist_names"] or [])
    if names:
        return " × ".join(names[:2]) + (" 외" if len(names) > 2 else "")
    return f"@{row['x_handle']}"


def compact_when(row):
    """회차 날짜를 한 토막으로 접는다. 여러 날이면 범위로."""
    dates = sorted({str(s.get("event_date")) for s in (row["shows"] or [])
                    if s.get("event_date")})
    if not dates:
        return None
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]}~{dates[-1]} ({len(dates)}회차)"


def compact_where(row):
    """도시만 남긴다. 국가·공연장은 스레드에서도 군더더기라 뺐다.
    필요하면 원문 링크를 타고 들어가면 된다."""
    cities = []
    for s in (row["shows"] or []):
        c = s.get("city")
        if c and c not in cities:
            cities.append(c)
    if not cities:
        return None
    return ", ".join(cities[:2]) + (f" 외 {len(cities) - 2}곳" if len(cities) > 2 else "")


def digest_line(row):
    """공지 한 건 = 한 줄. 아티스트 / 투어명 / 언제 / 어디 / 원문 링크.

    확신도, 계정 핸들, 작성 시각, 예매 오픈 시간 문구는 전부 뺐다.
    채널에서 훑어볼 때 필요한 건 '누가 어디서 언제' 뿐이고,
    나머지는 원문에 다 있다.
    """
    tour = (row["tour_name"] or row["event_type"] or "공연 소식").strip()
    if len(tour) > 60:
        tour = tour[:59] + "…"
    bits = [f"*{artist_label(row)}* — {tour}"]
    when, where = compact_when(row), compact_where(row)
    if when:
        bits.append(when)
    if where:
        bits.append(where)
    line = "  ·  ".join(bits) + f"  <{row['tweet_url']}|↗>"
    if row["needs_review"]:
        line += " ⚠️"
    return "• " + line


def build_summary_blocks(rows, overflow):
    """채널에 보이는 부모 메시지. 숫자만 있고 상세는 스레드로 내린다."""
    today = datetime.now(KST)
    weekday = "월화수목금토일"[today.weekday()]
    counts = Counter(r["announcement_kind"] or "OTHER" for r in rows)
    artists = {artist_label(r) for r in rows}
    review = sum(1 for r in rows if r["needs_review"])

    kind_bits = []
    for kind, (icon, label, _) in sorted(KIND_META.items(), key=lambda kv: kv[1][2]):
        if counts.get(kind):
            kind_bits.append(f"{icon}{label} {counts[kind]}")

    head = f"🌏 *글로벌 투어 소식*  ·  {today.month}/{today.day}({weekday})"
    stat = f"*{len(rows)}건*  ·  아티스트 {len(artists)}팀"
    if review:
        stat += f"  ·  ⚠️확인 필요 {review}"
    lines = [head, stat]
    if kind_bits:
        lines.append("  ".join(kind_bits))
    tail = "💬 상세는 이 메시지 스레드에 있습니다."
    if overflow:
        tail += f" (상한 초과 {overflow}건은 다음 실행으로 이월)"
    lines.append(tail)

    blocks = [{"type": "section",
               "text": {"type": "mrkdwn", "text": "\n".join(lines)}}]
    return blocks, f"글로벌 투어 소식 {len(rows)}건"


def build_thread_batches(rows):
    """스레드에 달 답글들. 공지 유형별로 묶고, 길면 여러 답글로 쪼갠다.

    반환: [(blocks, fallback, [해당 답글이 담은 row...]), ...]
    한 답글이 실패하면 그 답글에 담긴 건만 FAILED 로 남아 다음에 재시도된다.
    """
    by_kind = defaultdict(list)
    for r in rows:
        by_kind[r["announcement_kind"] or "OTHER"].append(r)

    batches, cur_lines, cur_rows = [], [], []

    def flush():
        if cur_lines:
            batches.append((
                [{"type": "section",
                  "text": {"type": "mrkdwn", "text": "\n".join(cur_lines)}}],
                "투어 공지 상세",
                list(cur_rows),
            ))
            cur_lines.clear()
            cur_rows.clear()

    for kind, (icon, label, _) in sorted(KIND_META.items(), key=lambda kv: kv[1][2]):
        group = by_kind.get(kind)
        if not group:
            continue
        header = f"{icon} *{label}* {len(group)}건"
        if cur_lines and sum(len(x) for x in cur_lines) + len(header) > THREAD_CHAR_BUDGET:
            flush()
        cur_lines.append(header)
        for r in group:
            line = digest_line(r)
            if sum(len(x) for x in cur_lines) + len(line) > THREAD_CHAR_BUDGET:
                flush()
                cur_lines.append(f"{icon} *{label}* (이어서)")
            cur_lines.append(line)
            cur_rows.append(r)
    flush()
    return batches


def post_to_slack(blocks, fallback, thread_ts=None):
    """Webhook 이 있으면 그걸 쓰고, 없으면 봇 토큰으로 chat.postMessage.

    반환: (성공여부, 에러문구, 메시지 ts)
    ts 는 봇 토큰 방식에서만 나온다. Webhook 은 응답이 "ok" 문자열뿐이라
    스레드를 걸 수 없다 - 이 경우 상세가 채널에 그대로 붙는다.
    """
    if DRY_RUN:
        print(f"--- DRY RUN {'(스레드 답글)' if thread_ts else '(채널)'} ---")
        print(json.dumps(blocks, ensure_ascii=False, indent=2))
        return True, None, "dry-run-ts"

    # .strip() 이 붙은 이유: 깃허브 시크릿에 값을 붙여넣을 때 끝에 줄바꿈이 딸려
    # 들어가는 일이 잦다. 값이 안 보이니 육안으로 못 잡고, 슬랙은 이걸 invalid_auth
    # 로만 돌려줘서 "토큰이 틀렸다" 로 오진하게 된다. (2026-09-02 실제로 겪음)
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if webhook:
        r = requests.post(webhook, json={"text": fallback, "blocks": blocks}, timeout=15)
        # Webhook 은 성공 시 본문이 "ok" 인 200 을 준다. 실패해도 200 을 주는 경우가 있어
        # 본문까지 확인한다.
        ok = r.status_code == 200 and r.text.strip() == "ok"
        return ok, None if ok else f"HTTP {r.status_code} {r.text[:200]}", None

    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    channel = os.environ.get("SLACK_CHANNEL_ID", "").strip()
    if not token or not channel:
        raise RuntimeError("SLACK_WEBHOOK_URL 또는 (SLACK_BOT_TOKEN + SLACK_CHANNEL_ID) 가 필요합니다")
    payload = {"channel": channel, "text": fallback, "blocks": blocks}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        json=payload, timeout=15,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    ok = bool(body.get("ok"))
    return ok, None if ok else f"{body.get('error') or r.text[:200]}", body.get("ts")


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

    # 공지 유형이 큰 것부터, 같은 유형 안에서는 아티스트 이름순.
    def sort_key(r):
        _, _, prio = KIND_META.get(r["announcement_kind"] or "OTHER", KIND_META["OTHER"])
        return (prio, artist_label(r), r["tweet_created_at"])
    rows = sorted(rows, key=sort_key)

    overflow = max(0, len(rows) - MAX_NOTIFY)
    targets = rows[:MAX_NOTIFY]
    log.info("발송 대상 %d건 (상한 %d, 초과 %d건은 다음 실행으로 이월)",
             len(targets), MAX_NOTIFY, overflow)

    now = datetime.now(timezone.utc).isoformat()

    # 1) 부모 메시지 하나. 여기서 실패하면 스레드를 걸 곳이 없으니 전부 실패 처리하고
    #    다음 실행에서 통째로 재시도한다.
    summary_blocks, summary_fallback = build_summary_blocks(targets, overflow)
    ok, err, parent_ts = post_to_slack(summary_blocks, summary_fallback)
    if not ok:
        log.error("요약 메시지 발송 실패: %s", err)
        if not DRY_RUN:
            record_sent(bq, [{"tweet_id": r["tweet_id"], "channel": CHANNEL_LABEL,
                              "sent_at": now, "status": "FAILED",
                              "error_note": f"요약 발송 실패: {err}"} for r in targets])
        sys.exit(1)

    if not parent_ts:
        # Webhook 방식이라 ts 가 없다. 상세가 스레드가 아니라 채널에 붙는다.
        log.warning("스레드 ts 를 못 받았다 (Webhook 방식). 상세가 채널에 그대로 붙는다. "
                    "스레드로 모으려면 SLACK_BOT_TOKEN 방식을 써야 한다.")

    # 2) 상세는 스레드 답글로.
    batches = build_thread_batches(targets)
    records, sent, failed = [], 0, 0
    for blocks, fallback, batch_rows in batches:
        ok, err, _ = post_to_slack(blocks, fallback, thread_ts=parent_ts)
        if ok:
            sent += len(batch_rows)
        else:
            failed += len(batch_rows)
            log.error("스레드 답글 발송 실패 (%d건): %s", len(batch_rows), err)
        for r in batch_rows:
            records.append({"tweet_id": r["tweet_id"], "channel": CHANNEL_LABEL,
                            "sent_at": now, "status": "SENT" if ok else "FAILED",
                            "error_note": err})

    if not DRY_RUN:
        record_sent(bq, records)

    log.info("Slack %s 발송 완료 | 요약 1건 + 스레드 답글 %d개 | 공지 성공 %d · 실패 %d · 이월 %d",
             CHANNEL_LABEL, len(batches), sent, failed, overflow)
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
