#!/usr/bin/env python3
"""notify_tour_slack.py 의 메시지 조립 테스트. Slack/BigQuery 접속 없이 돈다.

2026-09-02 개편: 공지 1건 = 메시지 1건이었던 걸 '요약 1건 + 스레드 답글' 로 바꿨다.
채널 도배를 막는 게 목적이므로, 테스트도 '채널에 나가는 건 하나뿐인가',
'상세가 충분히 짧은가' 를 본다.
"""
import sys
from datetime import datetime, timezone
import notify_tour_slack as ns

FAIL = []
def check(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)


def mkrow(**kw):
    base = {
        "tweet_id": "1", "x_handle": "Stray_Kids",
        "artist_names": ["Stray Kids"], "artist_entity_ids": ["stray_kids"],
        "tour_name": "Stray Kids World Tour <RUN IT>", "event_type": "콘서트/투어",
        "announcement_kind": "SHOW_INFO", "confidence": 0.95,
        "needs_review": False, "review_reason": None,
        "ticket_vendor": "Live Nation", "ticket_urls": ["https://www.livenation.co.th/e/1"],
        "tweet_url": "https://x.com/Stray_Kids/status/1",
        "tweet_created_at": datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
        "shows": [
            {"event_date": "2027-01-16", "date_text": "2027.01.16 (SAT)", "city": "Bangkok",
             "country": "THAILAND", "venue_name": "Impact Arena"},
            {"event_date": "2027-01-17", "date_text": "01.17 (SUN)", "city": "Bangkok",
             "country": "THAILAND", "venue_name": "Impact Arena"},
        ],
        "ticket_opens": [{"label": "STAY Membership Presale",
                          "opens_at_text": "2026.09.21 (MON) 10AM - 10PM",
                          "timezone_note": "Local Time"}],
    }
    base.update(kw)
    return base


print("[상세 한 줄]")
line = ns.digest_line(mkrow())
print(line)
check("Stray Kids" in line, "아티스트명 포함")
check("RUN IT" in line, "투어명 포함")
check("2027-01-16~2027-01-17" in line, "여러 날짜가 범위로 접힘")
check("Bangkok" in line, "도시 포함")
check("https://x.com/Stray_Kids/status/1" in line, "원문 링크 포함")
check(len(line) <= 160, f"한 줄이 충분히 짧음 (현재 {len(line)}자)")
check("확신도" not in line and "0.95" not in line, "확신도는 노출하지 않음")
check("Live Nation" not in line, "예매처는 노출하지 않음")
check("STAY Membership Presale" not in line, "예매 오픈 시간 문구는 노출하지 않음")
check("Impact Arena" not in line, "공연장은 노출하지 않음")
check("@Stray_Kids" not in line, "계정 핸들은 노출하지 않음")

print("\n[확인 필요 표시]")
warn = ns.digest_line(mkrow(needs_review=True, review_reason="도시 결측"))
print(warn)
check("⚠️" in warn, "확인 필요는 배지 하나로만")
check("도시 결측" not in warn, "확인 필요 사유 문구는 채널에 안 씀")

print("\n[요약 메시지]")
rows = [
    mkrow(tweet_id="1"),
    mkrow(tweet_id="2", announcement_kind="TICKET_OPEN", artist_names=["태민"]),
    mkrow(tweet_id="3", announcement_kind="TICKET_OPEN", artist_names=["WayV"]),
    mkrow(tweet_id="4", announcement_kind="NEW_TOUR", artist_names=["JO1"], needs_review=True),
]
sblocks, sfallback = ns.build_summary_blocks(rows, overflow=2)
stext = sblocks[0]["text"]["text"]
print(stext)
check(len(sblocks) == 1, "요약은 블록 하나")
check("*4건*" in stext, "총 건수 표기")
check("아티스트 4팀" in stext, "아티스트 팀 수 표기")
check("티켓 오픈 2" in stext, "유형별 건수 표기")
check("확인 필요 1" in stext, "확인 필요 건수 표기")
check("스레드" in stext, "상세가 스레드에 있다고 안내")
check("이월 2건" in stext or "2건은 다음 실행" in stext, "이월 건수 안내")
check("RUN IT" not in stext, "요약에는 개별 투어명이 안 들어감")

print("\n[스레드 답글]")
batches = ns.build_thread_batches(rows)
for i, (blocks, fb, brows) in enumerate(batches, 1):
    print(f"--- 답글 {i} ({len(brows)}건) ---")
    print(blocks[0]["text"]["text"])
check(len(batches) >= 1, "답글이 최소 1개")
check(sum(len(b[2]) for b in batches) == len(rows), "모든 공지가 답글에 빠짐없이 담김")
joined = "\n".join(b[0][0]["text"]["text"] for b in batches)
check(joined.index("새 투어 발표") < joined.index("티켓 오픈"), "새 투어 발표가 티켓 오픈보다 위")
check("*티켓 오픈* 2건" in joined, "유형 헤더에 건수 표기")

print("\n[다건 배치 분할]")
many = [mkrow(tweet_id=str(i), artist_names=[f"아티스트{i}"],
              tour_name="아주아주 긴 투어 이름 " * 4) for i in range(40)]
mb = ns.build_thread_batches(many)
check(len(mb) > 1, f"길면 여러 답글로 쪼개짐 (답글 {len(mb)}개)")
check(sum(len(b[2]) for b in mb) == 40, "쪼개도 누락 없음")
for blocks, _, _ in mb:
    check(len(blocks[0]["text"]["text"]) < 3000, "각 답글이 슬랙 3000자 제한 이내")

print("\n[정렬 우선순위]")
check(ns.KIND_META["NEW_TOUR"][2] < ns.KIND_META["TICKET_OPEN"][2],
      "새 투어 발표가 티켓 오픈보다 우선")

print()
if FAIL:
    print("실패:", FAIL)
    sys.exit(1)
print("전체 통과")
