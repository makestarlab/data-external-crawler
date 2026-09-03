-- [2026-09-03] 베뉴 수용인원 딕셔너리.
--
-- 왜 별도 테이블인가: 어느 예매처 API 도 capacity 를 주지 않는다.
--   Ticketmaster Discovery 의 venue 객체는 이름·주소·좌표·도시만 준다(문서 확인).
--   가격은 priceRanges 로 오지만 수용인원은 없다. 그래서 딕셔너리를 직접 세운다.
--
-- 시드: '글로벌 투어 현황' 시트의 분홍 F4CCCC 셀 465행 -> 고유 베뉴 274곳.
--   시트 3행 범례가 색으로 두 값을 갈라준다.
--     분홍 = 정확한 판매 좌석수를 몰라 적은 '공연장 Full capacity'  <- 딕셔너리 재료
--     무색 = 그 공연의 실제 판매 좌석수                              <- 제외
--   추출 스크립트: venue_seed_from_excel.py (data/venue_seed_20260903.csv 생성)
--
-- 매칭은 정확 일치만 한다. 부분 문자열 매칭은 조용히 틀린다 -
--   실측에서 'THAMMASAT STADIUM' 이 'AT&T STADIUM'(정규화 후 'T STADIUM')에
--   부분 일치해 65,000 이 붙었다. 실제 탐마삿은 2만대다.
--   틀린 값은 빈칸보다 나쁘다. 빈칸은 사람이 채우지만 틀린 값은 그대로 믿는다.
--
-- 적재 결과(2026-09-03): 274곳, 값 충돌 19곳(최댓값 채택, 관측값 전부 보존),
--   그중 2배 이상 벌어져 확인 필요 3곳 - Alhambra 600/1400,
--   Makuhari Messe 9000/48000, THUNDER DOME 3500/15000

CREATE TABLE IF NOT EXISTS `makestar-dw.makestar_ax.venue_master` (
  venue_key STRING NOT NULL OPTIONS(description="비교용 정규화 키. 대문자화 + 괄호 제거 + 영숫자/한글 외 공백. 단어는 지우지 않는다 - 관사를 지우면 서로 다른 공연장이 같은 키가 된다"),
  venue_name STRING NOT NULL OPTIONS(description="대표 표기. 같은 키의 여러 표기 중 가장 긴 것"),
  city STRING,
  country STRING,
  capacity INT64 OPTIONS(description="공연장 Full capacity. 특정 공연의 판매 좌석수가 아니다"),
  capacity_values ARRAY<STRING> OPTIONS(description="출처에서 관측된 값 전부. 충돌 이력을 지우지 않는다"),
  needs_review BOOL OPTIONS(description="관측값이 2배 이상 벌어짐. 부분 개방이 아니라 다른 공연장일 수 있다"),
  capacity_source STRING OPTIONS(description="EXCEL_PINK_CELL / WIKIDATA_P1083 / KOPIS_SEATSCALE / MANUAL"),
  source_note STRING,
  first_seen_date DATE,
  updated_at TIMESTAMP
)
OPTIONS(description="공연장 수용인원 딕셔너리. 어느 예매처 API 도 capacity 를 주지 않아 별도로 세운다. 시드는 엑셀 분홍 셀 274곳");

-- 시드 적재: data/venue_seed_20260903.csv 를 BigQuery 콘솔에서 이 테이블에 추가하거나
--   (스키마 자동 감지 끄고 위 정의에 맞춰 매핑), 아래 형태의 INSERT 를 쓴다.
--   venue_key 가 이미 있으면 건너뛴다 - 재실행해도 중복되지 않는다.
--
-- INSERT INTO `makestar-dw.makestar_ax.venue_master`
--   (venue_key, venue_name, city, country, capacity, capacity_values,
--    needs_review, capacity_source, source_note, first_seen_date, updated_at)
-- SELECT s.venue_key, s.venue_name, NULLIF(s.city,''), NULLIF(s.country,''),
--        s.capacity, SPLIT(s.capacity_values,'|'), s.needs_review,
--        'EXCEL_PINK_CELL', '<출처 설명>', CURRENT_DATE('Asia/Seoul'), CURRENT_TIMESTAMP()
-- FROM UNNEST([ STRUCT<...> (...), ... ]) AS s
-- LEFT JOIN `makestar-dw.makestar_ax.venue_master` v USING (venue_key)
-- WHERE v.venue_key IS NULL;


-- 회차에 수용인원을 붙이는 뷰.
CREATE OR REPLACE VIEW `makestar-dw.makestar_ax.v_tour_shows_enriched` AS
WITH s AS (
  SELECT *,
    -- venue_seed_from_excel.py 의 norm_venue 와 같은 규칙이어야 한다. 다르면 조용히 안 붙는다.
    NULLIF(TRIM(REGEXP_REPLACE(
      REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(venue_name,'')), r'\(.*?\)', ' '),
                     r'[^A-Z0-9가-힣]+', ' '),
      r'\s+', ' ')), '') AS venue_key
  FROM `makestar-dw.makestar_ax.v_tour_shows_latest`
)
SELECT
  s.* EXCEPT(venue_key),
  s.venue_key,
  v.venue_name AS venue_canonical,
  v.capacity   AS venue_capacity,
  v.capacity_source,
  v.needs_review AS venue_capacity_uncertain,
  CASE
    WHEN s.venue_name IS NULL THEN '베뉴 미정'
    WHEN v.venue_key IS NULL  THEN '딕셔너리 미등재'
    ELSE '수용인원 확보'
  END AS venue_status
FROM s
LEFT JOIN `makestar-dw.makestar_ax.venue_master` v USING (venue_key);


-- 딕셔너리에 없는 베뉴 = 다음에 채울 대상. Wikidata P1083 / KOPIS seatscale 조회 후보다.
CREATE OR REPLACE VIEW `makestar-dw.makestar_ax.v_venue_dictionary_gap` AS
SELECT venue_name, ANY_VALUE(city) AS city, ANY_VALUE(country) AS country,
       COUNT(*) AS shows, MIN(event_date) AS earliest_show
FROM `makestar-dw.makestar_ax.v_tour_shows_enriched`
WHERE venue_status = '딕셔너리 미등재'
GROUP BY venue_name
ORDER BY shows DESC, earliest_show;


-- 확인 (2026-09-03 기준: 수용인원 확보 19 / 딕셔너리 미등재 22 / 베뉴 미정 18)
SELECT venue_status, COUNT(*) AS shows
FROM `makestar-dw.makestar_ax.v_tour_shows_enriched` GROUP BY 1 ORDER BY shows DESC;
