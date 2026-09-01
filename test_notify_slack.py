#!/usr/bin/env python3
"""notify_tour_slack.py 의 메시지 조립 테스트. Slack/BigQuery 접속 없이 돈다."""
import sys
from datetime import datetime, timezone
import notify_tour_slack as ns

FAIL=[]
def check(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)

row = {
  "tweet_id":"1","x_handle":"Stray_Kids",
  "artist_names":["Stray Kids"],"artist_entity_ids":["stray_kids"],
  "tour_name":"Stray Kids World Tour <RUN IT>","event_type":"콘서트/투어",
  "announcement_kind":"SHOW_INFO","confidence":0.95,
  "needs_review":False,"review_reason":None,
  "ticket_vendor":"Live Nation","ticket_urls":["https://www.livenation.co.th/e/1"],
  "tweet_url":"https://x.com/Stray_Kids/status/1",
  "tweet_created_at":datetime(2026,8,24,9,0,tzinfo=timezone.utc),
  "shows":[
    {"event_date":"2027-01-16","date_text":"2027.01.16 (SAT)","city":"Bangkok","country":"THAILAND","venue_name":"Impact Arena"},
    {"event_date":"2027-01-17","date_text":"01.17 (SUN)","city":"Bangkok","country":"THAILAND","venue_name":"Impact Arena"},
  ],
  "ticket_opens":[{"label":"STAY Membership Presale","opens_at_text":"2026.09.21 (MON) 10AM - 10PM","timezone_note":"Local Time"}],
}
blocks, fallback = ns.build_blocks(row)
text = blocks[0]["text"]["text"]
ctx  = blocks[1]["elements"][0]["text"]
print("[메시지 본문]"); print(text); print("[컨텍스트]"); print(ctx); print()

check("Stray Kids" in text, "아티스트명 포함")
check("일정·공연장 확정" in text, "공지 유형 라벨")
check("RUN IT" in text, "투어명 포함")
check("2027-01-16, 2027-01-17" in text, "같은 도시 2회차가 한 줄로 묶임")
check(text.count("Impact Arena")==1, "공연장이 중복 출력되지 않음")
check("Bangkok, THAILAND" in text, "도시·국가 표기")
check("STAY Membership Presale" in text, "티켓 오픈 안내 포함")
check("Live Nation" in text, "예매처 포함")
check("확인 필요" not in text, "정상 공지에는 확인 필요 배지 없음")
check("08/24 18:00 KST" in ctx, "작성 시각이 KST 로 변환됨")
check("확신도 0.95" in ctx, "확신도 표기")

print("\n[확인 필요 케이스]")
row2 = dict(row, needs_review=True, review_reason="도시 결측; entity_master 매칭 실패",
            announcement_kind="NEW_TOUR", confidence=0.5,
            shows=[{"event_date":None,"date_text":"TBA","city":None,"country":None,"venue_name":None}],
            ticket_opens=[], ticket_vendor=None)
t2 = ns.build_blocks(row2)[0][0]["text"]["text"]
print(t2)
check("확인 필요" in t2, "확인 필요 배지 노출")
check("새 투어 발표" in t2, "NEW_TOUR 라벨")
check("도시 미정" in t2 and "공연장 미정" in t2, "결측을 빈칸이 아니라 명시적으로 표기")

print("\n[다회차·다도시 축약]")
many=[{"event_date":f"2027-0{i}-01","date_text":"","city":f"City{i}","country":"USA","venue_name":f"Venue{i}"} for i in range(1,9)]
t3 = ns.build_blocks(dict(row, shows=many))[0][0]["text"]["text"]
check("외 2개 회차" in t3, "6개 초과 회차는 접힘")

print("\n[정렬 우선순위]")
check(ns.KIND_META["NEW_TOUR"][2] < ns.KIND_META["TICKET_OPEN"][2],
      "새 투어 발표가 티켓 오픈보다 우선")

print()
if FAIL:
    print("실패:", FAIL); sys.exit(1)
print("전체 통과")
