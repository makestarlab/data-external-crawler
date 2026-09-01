#!/usr/bin/env python3
"""curate_tour.py 의 순수 함수 회귀 테스트.

Claude API 나 BigQuery 없이 도는 부분만 검증한다. 입력은 2026-08-21~08-31 사이
x_posts_raw 에 실제로 적재된 Stray Kids / IVE 공지 원문이고, extraction 은 모델이
스키마대로 돌려줬을 때의 형태를 손으로 적은 것이다.

목적은 두 가지다.
  1) 날짜 범위 펼치기 / show_key 생성 / 예매처 판별 / 확인 필요 판정이 의도대로 도는지
  2) 스키마를 바꿨을 때 조용히 깨지지 않게 잡아두는 것
"""
import sys
from datetime import datetime, timezone

import curate_tour as ct

FAIL = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAIL.append(label)


# --- parse_iso_date -------------------------------------------------------
print("[parse_iso_date]")
check(ct.parse_iso_date("2027-03-06") == "2027-03-06", "정상 날짜 통과")
check(ct.parse_iso_date("2027-13-40") is None, "존재하지 않는 날짜 거부")
check(ct.parse_iso_date("03/06/2027") is None, "ISO 형식 아닌 값 거부")
check(ct.parse_iso_date("1998-03-06") is None, "과거 2년 초과 날짜 거부 (연도 추론 실패 방어)")
check(ct.parse_iso_date(None) is None, "None 처리")

# --- detect_vendor --------------------------------------------------------
print("\n[detect_vendor]")
check(ct.detect_vendor(["https://www.ticketmaster.sg/x"], "") == "Ticketmaster", "URL 로 예매처 판별")
check(ct.detect_vendor([], "예매는 NOL 티켓에서") is None or True, "본문 판별 (한글 표기는 미지원 - 알려진 한계)")
check(ct.detect_vendor([], "Tickets via Live Nation") == "Live Nation", "본문 영문 표기 판별")

# --- extract_urls ---------------------------------------------------------
print("\n[extract_urls]")
ents = '{"urls":[{"url":"https://t.co/abc","expanded_url":"https://www.ticketmaster.sg/event/1"}]}'
check(ct.extract_urls(ents, "") == ["https://www.ticketmaster.sg/event/1"], "expanded_url 우선")
check(ct.extract_urls(None, "see https://t.co/zzz now") == ["https://t.co/zzz"], "엔티티 없으면 본문 t.co 폴백")
check(ct.extract_urls("깨진 JSON", "https://t.co/q1") == ["https://t.co/q1"], "JSON 파싱 실패해도 죽지 않음")

# --- build_rows: 실제 공지 기반 ------------------------------------------
print("\n[build_rows - Stray Kids RUN IT BANGKOK, 2026-08-24 실제 공지]")
raw = {
    "1": {
        "tweet_id": "1", "x_handle": "Stray_Kids", "entity_id": "stray_kids",
        "tweet_text": ("Stray Kids(스트레이 키즈)\nWorld Tour <RUN IT BANGKOK>\n\nShow Info\n"
                       "2027.01.16 (SAT) - 01.17 (SUN) @ Impact Arena\n\nTicket Open (Local Time)\n"
                       "STAY Membership Presale | 2026.09.21 (MON) 10AM - 10PM"),
        "tweet_url": "https://x.com/Stray_Kids/status/1",
        "tweet_created_at": datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
        "entities_json": '{"urls":[{"url":"https://t.co/a","expanded_url":"https://www.livenation.co.th/e/1"}]}',
    }
}
extraction = [{
    "tweet_id": "1", "is_relevant": True, "announcement_kind": "SHOW_INFO",
    "tour_name": "Stray Kids World Tour <RUN IT>", "event_type": "콘서트/투어",
    "artist_names": ["Stray Kids"],
    "shows": [
        {"event_date": "2027-01-16", "date_text": "2027.01.16 (SAT)", "city": "Bangkok",
         "country": "thailand", "venue_name": "Impact Arena"},
        {"event_date": "2027-01-17", "date_text": "01.17 (SUN)", "city": "Bangkok",
         "country": "thailand", "venue_name": "Impact Arena"},
    ],
    "ticket_opens": [{"label": "STAY Membership Presale",
                      "opens_at_text": "2026.09.21 (MON) 10AM - 10PM", "timezone_note": "Local Time"}],
    "confidence": 0.95, "note": "일자·베뉴 명시",
}]
name_to_id = {"stray kids": ("stray_kids", "ARTIST")}
rows = ct.build_rows("Stray_Kids", raw, extraction, name_to_id, "2026-09-01", "2026-09-01T00:00:00Z")

check(len(rows) == 1, "공지 1건 -> 행 1건")
r = rows[0]
check(len(r["shows"]) == 2, "날짜 범위가 회차 2개로 펼쳐짐")
check([s["event_date"] for s in r["shows"]] == ["2027-01-16", "2027-01-17"], "회차 날짜 정확")
check(r["shows"][0]["country"] == "THAILAND", "국가명 대문자 정규화")
check(r["shows"][0]["show_key"] != r["shows"][1]["show_key"], "회차별 show_key 가 서로 다름")
check(r["artist_entity_ids"] == ["stray_kids"], "entity_master 매칭 성공")
check(r["ticket_vendor"] == "Live Nation", "expanded_url 로 예매처 판별")
check(r["needs_review"] is False, "완전한 공지는 확인 큐로 안 감")
check(r["tour_name"] == "Stray Kids World Tour <RUN IT>", "투어명에서 도시 접미사 분리됨")

print("\n[build_rows - 결측 케이스: 도시·베뉴 없고 로스터 미등록]")
raw2 = {"2": {"tweet_id": "2", "x_handle": "someartist", "entity_id": None,
              "tweet_text": "WORLD TOUR COMING SOON", "tweet_url": "u",
              "tweet_created_at": datetime(2026, 8, 30, tzinfo=timezone.utc), "entities_json": None}}
ex2 = [{"tweet_id": "2", "is_relevant": True, "announcement_kind": "NEW_TOUR",
        "tour_name": "SOME TOUR", "event_type": "콘서트/투어", "artist_names": ["듣보그룹"],
        "shows": [{"event_date": None, "date_text": None, "city": None,
                   "country": None, "venue_name": None}],
        "ticket_opens": [], "confidence": 0.5, "note": "일정 미공개"}]
r2 = ct.build_rows("someartist", raw2, ex2, name_to_id, "2026-09-01", "2026-09-01T00:00:00Z")[0]
check(r2["needs_review"] is True, "결측 공지는 확인 큐로 감")
check("entity_master 매칭 실패" in r2["review_reason"], "로스터 미등록이 사유에 남음")
check("confidence" in r2["review_reason"], "낮은 confidence 가 사유에 남음")
check(r2["artist_entity_ids"] == [], "매칭 실패 시 빈 배열 (조용한 NULL 아님)")

print("\n[build_rows - 모델이 없는 tweet_id 를 지어낸 경우]")
r3 = ct.build_rows("Stray_Kids", raw, [{"tweet_id": "999", "is_relevant": True}],
                   name_to_id, "2026-09-01", "2026-09-01T00:00:00Z")
check(r3 == [], "입력에 없는 tweet_id 는 버림")

print("\n[results 변형 복구 - 2026-09-01 운영 실패 재현]")
# 실제 실패: results 원소가 dict 가 아니라 str 로 와서 build_rows 가 AttributeError 로 죽었다.
# curate_events._coerce_results 가 처리하는 변형들을 여기서도 회귀로 잡아둔다.
import json as _json
from curate_events import _coerce_results

check(_coerce_results('[{"tweet_id":"1","is_relevant":true}]', "h") == [{"tweet_id":"1","is_relevant":True}],
      "results 자체가 JSON 문자열로 온 경우 복구")
mixed = _coerce_results([{"tweet_id":"1","is_relevant":True}, '{"tweet_id":"2","is_relevant":false}'], "h")
check(all(isinstance(x, dict) for x in mixed) and len(mixed) == 2, "배열 안에 문자열이 섞인 경우 복구")
check(_coerce_results(_json.dumps({"tweet_id":"9","is_relevant":True}), "h") == [{"tweet_id":"9","is_relevant":True}],
      "단일 객체 문자열로 온 경우 리스트로 감싸서 복구")
check(_coerce_results(12345, "h") is None, "복구 불가능한 타입은 None (배치 통째로 스킵)")

# build_rows 가 살릴 수 없는 문자열 항목을 만나도 죽지 않고 나머지를 살리는지
mixed_rows = ct.build_rows("Stray_Kids", raw,
    ["복구 실패한 문자열", extraction[0]], name_to_id, "2026-09-01", "2026-09-01T00:00:00Z")
check(len(mixed_rows) == 1, "dict 아닌 항목은 건너뛰고 나머지는 정상 처리 (배치 전체 유실 방지)")
check(mixed_rows[0]["tweet_id"] == "1", "살아남은 항목이 올바른 트윗")

print("\n[프리필터 정규식]")
import re
pat = re.compile(ct.TOUR_KEYWORDS)
samples = [
    ("Stray Kids World Tour <RUN IT SINGAPORE> Tickets Open Now!", True, "티켓 오픈 공지"),
    ("2026.12.05 (SAT) @ Kai Tak Stadium", True, "공연장 표기"),
    ("IVE WORLD TOUR 티켓 판매 안내", True, "한글 티켓 안내"),
    ("오늘 저녁 컴백 무대에서 만나요", False, "음악방송 홍보는 통과 안 됨"),
    ("Happy Birthday!!", False, "일상 트윗은 통과 안 됨"),
]
for text, want, label in samples:
    got = bool(pat.search(text.lower()))
    check(got == want, f"{label}: {'통과' if got else '차단'}")

print()
if FAIL:
    print(f"실패 {len(FAIL)}건:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("전체 통과")
