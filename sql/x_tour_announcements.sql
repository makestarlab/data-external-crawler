-- 투어 공지 큐레이션 결과 테이블 + 뷰
-- curate_tour.py 가 적재한다. 최초 1회 수동 실행.
--
-- 왜 x_event_announcements 에 컬럼을 붙이지 않고 테이블을 나눴는가
--   x_event_announcements 는 "판매처가 특정 음반 구매에 붙여 여는 이벤트" 전용이고
--   판정 기준(prompts/classification_rules.md 의 세 질문)이 2026-08-12 사람 라벨 감사로
--   튜닝돼 있다. 투어 일정은 판정 기준도 스키마도 완전히 다르므로, 한 테이블/한 프롬프트에
--   섞으면 기존 분류 정확도를 건드리게 된다. 테이블과 프롬프트를 분리해 서로 독립적으로
--   개선할 수 있게 둔다.

CREATE TABLE IF NOT EXISTS `makestar-dw.makestar_ax.x_tour_announcements` (
  run_date            DATE      NOT NULL OPTIONS(description="큐레이션 실행일 (Asia/Seoul 기준) - 파티션 컬럼"),
  tweet_id            STRING    NOT NULL OPTIONS(description="x_posts_raw.tweet_id"),
  x_handle            STRING    NOT NULL OPTIONS(description="공지한 X 계정 핸들"),
  entity_id           STRING    OPTIONS(description="entity_master.entity_id (계정 소유 엔티티)"),

  artist_names        ARRAY<STRING> OPTIONS(description="공지에 등장한 아티스트명 원문. 합동 공연 대응으로 배열"),
  artist_entity_ids   ARRAY<STRING> OPTIONS(description="entity_master 로 해소된 entity_id. 미해소분은 배열에서 빠진다"),

  tour_name           STRING    OPTIONS(description="공식 투어명 원문. 예: Stray Kids World Tour <RUN IT>"),
  event_type          STRING    OPTIONS(description="콘서트/투어 · 팬미팅 · 팬콘 · 뮤직 페스티벌 · 시상식 · 쇼케이스 · 기타"),
  announcement_kind   STRING    OPTIONS(description="NEW_TOUR(투어 최초 발표) · NEW_CITY(도시/회차 추가) · SHOW_INFO(일자·베뉴 확정) · TICKET_OPEN(티켓 오픈 안내) · SOLD_OUT(매진) · SCHEDULE_CHANGE(변경·취소) · OTHER"),

  shows ARRAY<STRUCT<
    show_key    STRING  OPTIONS(description="SHA1(대표 아티스트|공연일자|도시)[:20] - 같은 공연의 반복 공지를 묶는 키"),
    event_date  DATE    OPTIONS(description="공연 일자. 원문이 범위(3.06-03.07)면 날짜마다 한 원소로 펼친다"),
    date_text   STRING  OPTIONS(description="원문 날짜 표기 그대로 (검증용)"),
    city        STRING,
    country     STRING,
    venue_name  STRING
  >> OPTIONS(description="공지 하나가 여러 회차를 담는 경우가 흔하다. 실측: Stray Kids RUN IT BANGKOK 공지 1건에 2027-01-16·01-17 두 회차"),

  ticket_opens ARRAY<STRUCT<
    label         STRING OPTIONS(description="선예매 구분. 예: STAY Membership Presale, LN Presale, General Sale"),
    opens_at_text STRING OPTIONS(description="원문 표기. 지역별 현지시각이라 TIMESTAMP 로 강제 변환하지 않는다"),
    timezone_note STRING OPTIONS(description="KST / Local Time 등 원문에 적힌 기준")
  >>,
  ticket_urls         ARRAY<STRING> OPTIONS(description="본문/엔티티에서 뽑은 예매 링크. t.co 단축 링크 포함"),
  ticket_vendor       STRING    OPTIONS(description="Ticketmaster · Live Nation · Interpark · NOL Ticket · Klook 등 판별되면"),

  is_relevant         BOOL      OPTIONS(description="투어/공연 일정 공지가 맞는지. 일상 트윗·굿즈 판매·음원 홍보는 FALSE"),
  confidence          FLOAT64   OPTIONS(description="추출 확신도 0.0~1.0"),
  needs_review        BOOL      OPTIONS(description="사람 확인 필요. confidence 미달이거나 핵심 필드 결측일 때 TRUE"),
  review_reason       STRING    OPTIONS(description="needs_review 가 TRUE 인 이유"),
  note                STRING    OPTIONS(description="모델이 남긴 판단 근거 (감사용, 한 줄)"),

  tweet_text          STRING,
  tweet_url           STRING,
  tweet_created_at    TIMESTAMP,
  extracted_at        TIMESTAMP NOT NULL,
  extraction_model    STRING
)
PARTITION BY run_date
CLUSTER BY x_handle, announcement_kind
OPTIONS(description="아티스트 공식 X 계정 공지에서 추출한 글로벌 투어 일정. curate_tour.py 가 적재. 2026-09-01 생성");


-- 회차 단위로 펼친 뷰. 엑셀 '글로벌 투어 현황' 한 행 = 이 뷰 한 행에 대응한다.
CREATE OR REPLACE VIEW `makestar-dw.makestar_ax.v_tour_shows` AS
SELECT
  s.show_key,
  s.event_date,
  a.event_type,
  a.tour_name,
  a.artist_names,
  a.artist_entity_ids,
  s.country,
  s.city,
  s.venue_name,
  a.ticket_urls,
  a.ticket_vendor,
  a.announcement_kind,
  a.confidence,
  a.needs_review,
  a.x_handle,
  a.tweet_url,
  a.tweet_created_at,
  a.run_date
FROM `makestar-dw.makestar_ax.x_tour_announcements` a, UNNEST(a.shows) s
WHERE a.is_relevant AND s.event_date IS NOT NULL;


-- 같은 공연의 반복 공지를 하나로 접은 뷰. 최초 공지 시각과 최신 상태를 같이 본다.
-- 우선순위: 일자·베뉴가 확정된 공지(SHOW_INFO)를 대표로 삼고, 없으면 가장 이른 공지.
CREATE OR REPLACE VIEW `makestar-dw.makestar_ax.v_tour_shows_latest` AS
SELECT * EXCEPT(rn, first_seen_at, kinds),
       first_seen_at,
       kinds AS announcement_kinds
FROM (
  SELECT v.*,
    MIN(v.tweet_created_at) OVER (PARTITION BY v.show_key) AS first_seen_at,
    ARRAY_AGG(DISTINCT v.announcement_kind) OVER (PARTITION BY v.show_key) AS kinds,
    ROW_NUMBER() OVER (
      PARTITION BY v.show_key
      ORDER BY CASE v.announcement_kind WHEN 'SHOW_INFO' THEN 0 WHEN 'NEW_CITY' THEN 1 ELSE 2 END,
               v.venue_name IS NULL, v.tweet_created_at DESC
    ) rn
  FROM `makestar-dw.makestar_ax.v_tour_shows` v
)
WHERE rn = 1;


-- 확인 필요 큐. 대시보드 '입력/검토' 탭이 이 뷰를 읽는다.
CREATE OR REPLACE VIEW `makestar-dw.makestar_ax.v_tour_review_queue` AS
SELECT run_date, tweet_created_at, x_handle, tour_name, event_type, announcement_kind,
       confidence, review_reason, note, tweet_url, tweet_text,
       ARRAY_LENGTH(shows) AS show_count
FROM `makestar-dw.makestar_ax.x_tour_announcements`
WHERE is_relevant AND needs_review
ORDER BY tweet_created_at DESC;
