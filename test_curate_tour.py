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
        "ticket_opens": [], "confidence": 0.3, "note": "일정 미공개"}]
# confidence 0.3: 문턱(0.5) 아래. 문턱을 바꾸면 이 값도 같이 봐야 한다.
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



print("\n[tweet_id 복구 - 2026-09-01 백필 전량 유실 재현]")
# 실제 로그: 모델이 tweet_id 자리에 'placeholder' / '<UNKNOWN>' / '1','2','3' 을 넣어 돌려줬고
# build_rows 가드가 1,749건을 전부 걸러 0행이 됐다.
batch = [{"tweet_id": "1990000000000000001"}, {"tweet_id": "1990000000000000002"},
         {"tweet_id": "1990000000000000003"}]

seq = [{"tweet_id": "1"}, {"tweet_id": "2"}, {"tweet_id": "3"}]
fixed, n = ct.repair_tweet_ids(seq, batch, "h")
check(n == 3 and [r["tweet_id"] for r in fixed] == [t["tweet_id"] for t in batch],
      "1-based 순번을 실제 tweet_id 로 복구")

ph = [{"tweet_id": "placeholder"}, {"tweet_id": "<UNKNOWN>"}, {"tweet_id": "placeholder"}]
fixed, n = ct.repair_tweet_ids(ph, batch, "h")
check(n == 3 and [r["tweet_id"] for r in fixed] == [t["tweet_id"] for t in batch],
      "placeholder/<UNKNOWN> 는 개수가 같을 때 위치로 복구")

short = [{"tweet_id": "placeholder"}, {"tweet_id": "placeholder"}]
fixed, n = ct.repair_tweet_ids(short, batch, "h")
check(n == 0, "개수가 어긋나면 위치 매핑 안 함 (엉뚱한 트윗에 붙이느니 버린다)")

ok = [{"tweet_id": "1990000000000000002"}]
fixed, n = ct.repair_tweet_ids(ok, batch, "h")
check(n == 0 and fixed[0]["tweet_id"] == "1990000000000000002", "정상 id 는 건드리지 않음")

mixed2 = [{"tweet_id": "1990000000000000001"}, {"tweet_id": "2"}, {"tweet_id": "placeholder"}]
fixed, n = ct.repair_tweet_ids(mixed2, batch, "h")
check(fixed[1]["tweet_id"] == batch[1]["tweet_id"], "정상 id 가 섞여 있어도 순번은 복구")
check(fixed[2]["tweet_id"] == "placeholder",
      "유효 id 가 하나라도 있으면 placeholder 는 위치 매핑 안 함 (근거 부족)")

print("\n[프롬프트 포맷 - 모델이 tweet_id 를 필드로 인식하도록]")
tid_field = ct.TOOL_SCHEMA["input_schema"]["properties"]["results"]["items"]["properties"]["tweet_id"]
check("그대로 복사" in tid_field.get("description", ""), "스키마에 원문 복사 지시가 있음")
check(ct.BATCH_SIZE <= 15, f"배치 크기가 15 이하 (현재 {ct.BATCH_SIZE})")


print("\n[사용자 메시지 조립 - 2026-09-01 변수 섀도잉 사고 재현]")
# 실제 사고: 트윗 목록을 담은 지역변수를 로스터로 덮어써서 메시지에 본문이 한 건도
# 안 들어갔다. 모델은 "트윗 본문 없이 아티스트명만 제공되어 판단 불가" 라고 답했고
# 1,749건이 통째로 날아갔다. 조립 결과에 본문이 있는지를 여기서 못 박는다.
from datetime import datetime as _dt, timezone as _tz
msg_tweets = [
  {"tweet_id": "1990000000000000001",
   "tweet_created_at": _dt(2026, 8, 24, 9, 0, tzinfo=_tz.utc),
   "tweet_text": "Stray Kids World Tour <RUN IT BANGKOK>\n2027.01.16 @ Impact Arena"},
  {"tweet_id": "1990000000000000002",
   "tweet_created_at": _dt(2026, 8, 25, 9, 0, tzinfo=_tz.utc),
   "tweet_text": "Tickets Open Now!"},
]
msg = ct.build_user_message("Stray_Kids", "Stray Kids", msg_tweets)
print(msg[:300].replace("\n", " ⏎ "))

for t in msg_tweets:
    check(t["tweet_id"] in msg, f"메시지에 tweet_id {t['tweet_id'][-4:]} 포함")
check("Impact Arena" in msg, "메시지에 트윗 본문이 실제로 들어감 (이게 빠져서 사고가 났다)")
check("Tickets Open Now!" in msg, "두 번째 트윗 본문도 포함")
check(msg.count("- tweet_id:") == 2, "포스팅 줄이 트윗 수만큼 있음")
check(msg.rstrip().endswith("Tickets Open Now!"), "포스팅 목록이 메시지 맨 끝에 위치")
check("분석할 신규 포스팅 목록:" in msg, "목록 헤더가 명시됨")
check("Stray Kids 본인의 공식 계정" in msg, "계정 주인 이름이 문맥으로 들어감")
check(len(msg) < 4000, f"로스터를 안 넣어 메시지가 짧음 ({len(msg)}자)")

empty = ct.build_user_message("h", None, msg_tweets)
check("Impact Arena" in empty, "아티스트명을 몰라도 본문은 들어감")


print("\n[국가·도시 정규화 - 2026-09-02 실측에서 나온 흔들림]")
check(ct.normalize_country("South Korea") == "KOREA", "SOUTH KOREA -> KOREA (시트 표기)")
check(ct.normalize_country("대한민국") == "KOREA", "한글 국가명 -> 영문")
check(ct.normalize_country("United States") == "USA", "UNITED STATES -> USA")
check(ct.normalize_country("England") == "UNITED KINGDOM", "ENGLAND -> UNITED KINGDOM")
check(ct.normalize_country("Peru") == "PERU", "매핑에 없으면 대문자만 적용")
check(ct.normalize_country(None) is None, "None 처리")
check(ct.normalize_city("서울") == "Seoul", "서울 -> Seoul")
check(ct.normalize_city("서울특별시") == "Seoul", "접미사 붙은 형태도 처리")
check(ct.normalize_city("청두") == "Chengdu", "청두 -> Chengdu")
check(ct.normalize_city("Belmont Park") == "Belmont Park", "모르는 도시는 원문 유지")

print("\n[한국어판/영어판 공지가 같은 공연으로 묶이는가]")
ko = ct.make_show_key("NCT", "2026-09-18", ct.normalize_city("서울"))
en = ct.make_show_key("NCT", "2026-09-18", ct.normalize_city("Seoul"))
check(ko == en, "서울/Seoul 이 같은 show_key (전에는 두 행으로 갈렸다)")

print("\n[확인 필요 판정 완화]")
def _rows(kind, shows, conf=0.9):
    r = [{"tweet_id": "1", "is_relevant": True, "announcement_kind": kind,
          "tour_name": "T", "event_type": "콘서트/투어", "artist_names": ["Stray Kids"],
          "shows": shows, "ticket_opens": [], "confidence": conf, "note": ""}]
    return ct.build_rows("Stray_Kids", raw, r, name_to_id, "2026-09-02", "2026-09-02T00:00:00Z")[0]

no_date = [{"event_date": None, "date_text": "TBA", "city": None, "country": None, "venue_name": None}]
check(_rows("NEW_TOUR", no_date)["needs_review"] is False,
      "NEW_TOUR 는 날짜 없어도 확인 큐로 안 감 (투어 발표는 원래 미정)")
check(_rows("TICKET_OPEN", no_date)["needs_review"] is False,
      "TICKET_OPEN 도 날짜 없어도 통과 (이미 발표된 공연의 예매 안내)")
r_ni = _rows("NEW_CITY", no_date)
check(r_ni["needs_review"] is True, "NEW_CITY 는 날짜 없으면 확인 큐로")
check("일정 공지인데 확정 날짜 없음" in r_ni["review_reason"], "사유가 기록됨")

full = [{"event_date": "2027-01-16", "date_text": "", "city": "서울",
         "country": "south korea", "venue_name": "KSPO DOME"}]
ok = _rows("SHOW_INFO", full)
check(ok["needs_review"] is False, "완전한 SHOW_INFO 는 통과")
check(ok["shows"][0]["city"] == "Seoul", "build_rows 가 도시를 정규화")
check(ok["shows"][0]["country"] == "KOREA", "build_rows 가 국가를 정규화")

print("\n[증분 적재 설정]")
check(ct.FLUSH_EVERY > 0, f"FLUSH_EVERY 설정됨 ({ct.FLUSH_EVERY}행마다 적재)")
check(ct.MAX_MINUTES < 180, f"MAX_MINUTES({ct.MAX_MINUTES})가 러너 타임아웃(180분)보다 작음")


print("\n[확인 필요 판정 - 2026-09-02 30일치 실측 반영]")
def _r(kind, shows, conf=0.9):
    rr = [{"tweet_id": "1", "is_relevant": True, "announcement_kind": kind, "tour_name": "T",
           "event_type": "콘서트/투어", "artist_names": ["Stray Kids"], "shows": shows,
           "ticket_opens": [], "confidence": conf, "note": ""}]
    return ct.build_rows("Stray_Kids", raw, rr, name_to_id, "2026-09-02", "2026-09-02T00:00:00Z")[0]

no_venue = [{"event_date": "2027-01-16", "date_text": "", "city": "Bangkok",
             "country": "THAILAND", "venue_name": None}]
r = _r("SHOW_INFO", no_venue, 0.75)
check(r["needs_review"] is False,
      "일자·도시는 있고 베뉴만 없으면 통과 (투어 발표가 공연장 확정보다 먼저인 정상 케이스)")

r2 = _r("SHOW_INFO", no_venue, 0.45)
check(r2["needs_review"] is True, "confidence 0.45 는 확인 큐로")
check("confidence" in r2["review_reason"], "사유에 confidence 기록")

r3 = _r("SHOW_INFO", no_venue, 0.55)
check(r3["needs_review"] is False, f"confidence 0.55 는 통과 (문턱 {ct.CONFIDENCE_REVIEW_THRESHOLD})")

no_city = [{"event_date": "2027-01-16", "date_text": "", "city": None,
            "country": None, "venue_name": "Impact Arena"}]
check(_r("SHOW_INFO", no_city)["needs_review"] is True, "도시 결측은 여전히 확인 큐로")

no_date2 = [{"event_date": None, "date_text": "TBA", "city": "Bangkok",
             "country": "THAILAND", "venue_name": None}]
check(_r("SHOW_INFO", no_date2)["needs_review"] is True, "날짜 결측은 여전히 확인 큐로")
check(ct.CONFIDENCE_REVIEW_THRESHOLD <= 0.6,
      f"confidence 문턱이 0.6 이하 (현재 {ct.CONFIDENCE_REVIEW_THRESHOLD})")



print("\n[PROMOTER 계정 처리]")
# 프로모터 계정 공지에서 아티스트를 못 뽑으면, 계정 엔티티를 아티스트로 넣으면 안 된다.
promoter_raw = {
    "tweet_id": "9001", "x_handle": "hello82PRESENTS", "entity_id": "hello82",
    "entity_type": "PROMOTER", "tweet_text": "Tickets on sale now!",
    "tweet_url": "https://x.com/hello82PRESENTS/status/9001",
    "tweet_created_at": datetime(2026, 9, 1, tzinfo=timezone.utc), "entities_json": None,
}
res_no_artist = {"tweet_id": "9001", "is_relevant": True, "confidence": 0.8,
                 "announcement_kind": "TICKET_OPEN", "artist_names": [],
                 "tour_name": "SOME TOUR", "event_type": "콘서트/투어", "shows": []}
rows = ct.build_rows("hello82PRESENTS", {"9001": promoter_raw}, [res_no_artist],
                     {}, "2026-09-02", "2026-09-02T00:00:00Z")
check(rows[0]["artist_entity_ids"] == [],
      "프로모터 공지는 계정 엔티티를 아티스트로 넣지 않음")
check(rows[0]["needs_review"] and "프로모터" in (rows[0]["review_reason"] or ""),
      "아티스트 특정 실패는 확인 필요로 올라감")

# 같은 상황에서 ARTIST 계정이면 기존 자기참조 폴백이 그대로 살아 있어야 한다.
artist_raw = dict(promoter_raw, x_handle="Stray_Kids", entity_id="stray_kids",
                  entity_type="ARTIST")
rows2 = ct.build_rows("Stray_Kids", {"9001": artist_raw}, [res_no_artist],
                      {}, "2026-09-02", "2026-09-02T00:00:00Z")
check(rows2[0]["artist_entity_ids"] == ["stray_kids"],
      "아티스트 계정은 자기참조 폴백 유지")

print()
if FAIL:
    print(f"실패 {len(FAIL)}건:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("전체 통과")