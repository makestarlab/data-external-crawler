-- [2026-09-02] 추출 결과를 '글로벌 투어 현황' 시트 컬럼 그대로 내보내는 뷰.
--
-- 시트 규칙은 시트 3행에 적힌 작성 기준을 그대로 옮겼다.
--   - 모든 정보는 영문 공식명. 미국은 USA 로 통일.
--   - 리전은 국가에서 파생. 시트에 적힌 기준을 따른다.
--   - 공연 규모는 베뉴 일반 캐파가 아니라 '해당 공연' 캐파.
--
-- 시트 어휘와 추출 어휘가 미묘하게 다른 지점을 여기서 흡수한다.
--   event_type '콘서트/투어'  -> 시트 '콘서트 / 투어' (슬래시 앞뒤 공백)
--   country   'SOUTH KOREA'   -> 시트 'KOREA'
--
-- [2026-09-03] K 공연 규모는 venue_master 조인으로 채우기 시작했다.
--   Ticketmaster Discovery 의 venue 객체에는 capacity 필드가 없다(문서 확인).
--   어느 예매처 API 도 안 주므로 딕셔너리를 따로 세우는 것 외에 방법이 없다.
--
-- 아직 못 채우는 칸 (NULL 로 비워둔다. 빈칸이 틀린 값보다 낫다):
--   L 판매 완료 좌석수 - 합법적으로 얻을 경로가 없다. 계획서의 '수요 지표'로 대체 예정
--   M/N 티켓 가격      - Ticketmaster priceRanges 로 채울 수 있다. 키 발급 후.
--   D 단독 이벤트 제안 여부 -> 사내 판단. 기본값 '미제안'.
--
-- 알려진 불일치: 시트 3행 기준은 멕시코를 북미로 규정하는데, 실제 데이터는
--   남미 17건 / 북미 7건으로 적혀 있다. 여기서는 적힌 기준(북미)을 따랐다.

CREATE OR REPLACE VIEW `makestar-dw.makestar_ax.v_tour_sheet_rows` AS
WITH base AS (
  SELECT
    s.*,
    -- 국가 표기를 시트 어휘로 맞춘다.
    CASE UPPER(TRIM(IFNULL(s.country, '')))
      WHEN 'SOUTH KOREA' THEN 'KOREA'
      WHEN 'REPUBLIC OF KOREA' THEN 'KOREA'
      WHEN 'UK' THEN 'UNITED KINGDOM'
      WHEN 'ENGLAND' THEN 'UNITED KINGDOM'
      WHEN 'SCOTLAND' THEN 'UNITED KINGDOM'
      WHEN 'UNITED STATES' THEN 'USA'
      WHEN 'UNITED STATES OF AMERICA' THEN 'USA'
      WHEN 'US' THEN 'USA'
      WHEN 'UAE' THEN 'ARAB EMIRATES'
      WHEN '' THEN NULL
      ELSE UPPER(TRIM(s.country))
    END AS country_std
  FROM `makestar-dw.makestar_ax.v_tour_shows_enriched` s
),
-- IP 는 시트 기준상 영문 공식명으로 통일해야 한다. 모델이 뽑은 artist_names 는
-- 같은 그룹을 'DAY6' / '데이식스' 로 섞어 쓰고, 개인명('Young K')과 팀명이 갈린다.
-- entity_master.name_en 을 정답으로 삼고, 매칭이 안 될 때만 원문을 쓴다.
--
-- UNNEST 를 상관 서브쿼리 안에서 entity_master 와 조인하면 BigQuery 가 거부한다
-- ("Correlated subqueries that reference other tables are not supported").
-- 그래서 펼치기와 집계를 별도 CTE 두 단계로 나눈다.
ids AS (
  SELECT b.show_key, eid FROM base b, UNNEST(b.artist_entity_ids) AS eid
),
ip AS (
  SELECT i.show_key,
         STRING_AGG(DISTINCT IFNULL(e.name_en, e.name), ', '
                    ORDER BY IFNULL(e.name_en, e.name)) AS ip_en
  FROM ids i
  JOIN `makestar-dw.makestar_ax.entity_master` e ON e.entity_id = i.eid
  WHERE IFNULL(e.name_en, e.name) IS NOT NULL
  GROUP BY i.show_key
)
SELECT
  event_date                                    AS `공연_일자`,
  CASE
    WHEN event_type IN ('콘서트/투어', '콘서트 / 투어') THEN '콘서트 / 투어'
    WHEN event_type IN ('팬콘', '팬미팅', '쇼케이스', '시상식', '뮤직 페스티벌') THEN event_type
    WHEN event_type IS NULL THEN NULL
    ELSE '기타'
  END                                           AS `공연_유형`,
  '미제안'                                       AS `단독_이벤트_제안_여부`,
  tour_name                                     AS `공연명`,
  COALESCE(NULLIF(ip.ip_en, ''),
           ARRAY_TO_STRING(base.artist_names, ', ')) AS `IP`,
  CASE
    WHEN country_std IN ('USA', 'CANADA', 'MEXICO') THEN '북미'
    WHEN country_std IN ('BRAZIL', 'ARGENTINA', 'CHILE', 'PERU', 'COLOMBIA',
                         'URUGUAY', 'BOLIVIA', 'PARAGUAY', 'ECUADOR') THEN '남미'
    WHEN country_std IN ('CHINA', 'HONG KONG', 'MACAU', 'MACAO') THEN '중화권'
    WHEN country_std IN ('KOREA', 'JAPAN', 'TAIWAN', 'MONGOLIA') THEN '동북아'
    WHEN country_std IN ('THAILAND', 'MALAYSIA', 'INDONESIA', 'SINGAPORE',
                         'VIETNAM', 'PHILIPPINES', 'CAMBODIA', 'MYANMAR') THEN '동남아'
    WHEN country_std IN ('AUSTRALIA', 'NEW ZEALAND') THEN '오세아니아'
    WHEN country_std IN ('ARAB EMIRATES', 'SAUDI ARABIA', 'QATAR', 'ISRAEL',
                         'TURKEY', 'KUWAIT', 'BAHRAIN') THEN '중동아시아'
    WHEN country_std IS NULL THEN NULL
    ELSE '유럽'   -- 시트 기준상 나머지는 사실상 전부 유럽이다. 틀리면 확인 필요로 잡힌다.
  END                                           AS `리전`,
  country_std                                   AS `국가`,
  city                                          AS `도시`,
  venue_name                                    AS `베뉴명`,
  -- [2026-09-03] venue_master 에서 채운다. 시트 범례상 이 값은 '공연장 Full capacity' 이지
  --   그 공연의 실제 판매 좌석수가 아니다. 시트에서는 분홍 F4CCCC 셀이 그 구분을 한다.
  --   export_tour_sheet.py 가 이 열을 분홍으로 칠해 같은 약속을 지킨다.
  base.venue_capacity                           AS `공연_규모_판매좌석수`,
  CAST(NULL AS INT64)                           AS `판매_완료_좌석수`,
  CAST(NULL AS STRING)                          AS `Regular_티켓_가격`,
  CAST(NULL AS STRING)                          AS `VIP_티켓_가격`,
  -- x.com 링크는 예매 링크가 아니다. 트윗에 붙은 사진 URL 이 그대로 섞여 들어온다.
  (SELECT u FROM UNNEST(ticket_urls) u
    WHERE NOT REGEXP_CONTAINS(u, r'(?i)^https?://(www\.)?(x|twitter)\.com/')
    LIMIT 1)                                    AS `판매_링크`,
  run_date                                      AS `작성_일자`,
  -- 비고: 사람이 검증할 때 필요한 것만. 원문 링크는 반드시 남긴다.
  ARRAY_TO_STRING(ARRAY(SELECT x FROM UNNEST([
      IF(needs_review, '[확인 필요]', NULL),
      IF(ticket_vendor IS NOT NULL, CONCAT('예매처: ', ticket_vendor), NULL),
      CONCAT('원문: ', tweet_url)
    ]) x WHERE x IS NOT NULL), ' / ')            AS `비고`,
  -- 아래는 시트에 안 들어가지만 검증·조인용으로 남긴다.
  show_key, x_handle, needs_review, confidence, announcement_kinds,
  base.venue_status, base.capacity_source, base.venue_capacity_uncertain
FROM base LEFT JOIN ip USING (show_key);

-- [2026-09-09] 이 파일의 뷰가 BigQuery 에 배포되어 있지 않았다.
--   2026-09-03 에 venue_master 조인을 추가해 이 파일을 고쳤지만
--   CREATE OR REPLACE VIEW 를 실제로 실행하지 않아서, 운영 뷰는 그 이전 버전이었다.
--   결과: 공연 규모 칸이 6일 동안 조용히 전부 비어 있었다(30건 유실).
--   export_tour_sheet.py 가 venue_status / capacity_source 를 읽으므로
--   실행하면 에러가 났을 텐데, 그 사이 스크립트를 돌린 적이 없어 드러나지 않았다.
--
--   레포의 SQL 파일과 배포된 뷰가 갈라져도 아무도 모른다는 게 진짜 문제다.
--   뷰 정의를 INFORMATION_SCHEMA.VIEWS 와 대조하는 점검을 워크플로에 넣어야 한다.
