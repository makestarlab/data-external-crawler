-- [2026-09-02] 완화된 판정 규칙을 이미 적재된 행에 다시 적용한다.
--
-- 배경: 2026-09-02 에 curate_tour.py 의 needs_review 규칙을 두 군데 고쳤다.
--   1) "공연장 결측" 을 확인 필요 사유에서 제거 (투어 발표가 공연장 확정보다 먼저인 게 정상)
--   2) 날짜/도시 결측은 NEW_CITY, SHOW_INFO 에만 적용 (NEW_TOUR, TICKET_OPEN 은 없는 게 정상)
--   3) confidence 임계값 0.7 -> 0.5
--
-- 판정에 쓰는 값(shows, confidence, artist_entity_ids, announcement_kind)이 전부
-- 테이블에 저장돼 있어서, Claude 재호출 없이 SQL 만으로 다시 계산할 수 있다.
-- 아래 로직은 curate_tour.py build_rows() 의 reasons 계산과 1:1 로 대응한다.
--
-- 유일하게 빠진 규칙: "날짜 파싱 실패 N건".
--   원본 응답의 파싱 실패 여부는 테이블에 남지 않는다. 현재 해당 사유를 가진 행이
--   0건이라 재계산에서 제외해도 결과가 달라지지 않는다.

UPDATE `makestar-dw.makestar_ax.x_tour_announcements`
SET
  needs_review = ARRAY_LENGTH(ARRAY(
    SELECT r FROM UNNEST([
      IF(confidence IS NOT NULL AND confidence < 0.5,
         FORMAT('confidence %.2f < 0.5', confidence), NULL),
      IF(announcement_kind IN ('NEW_CITY', 'SHOW_INFO')
           AND NOT EXISTS (SELECT 1 FROM UNNEST(shows) s WHERE s.event_date IS NOT NULL),
         '일정 공지인데 확정 날짜 없음', NULL),
      IF(announcement_kind IN ('NEW_CITY', 'SHOW_INFO')
           AND EXISTS (SELECT 1 FROM UNNEST(shows) s WHERE s.city IS NULL OR s.city = ''),
         '도시 결측', NULL),
      IF(ARRAY_LENGTH(artist_entity_ids) = 0,
         'entity_master 매칭 실패 - 로스터 등록 필요', NULL)
    ]) AS r
    WHERE r IS NOT NULL
  )) > 0,
  review_reason = NULLIF(ARRAY_TO_STRING(ARRAY(
    SELECT r FROM UNNEST([
      IF(confidence IS NOT NULL AND confidence < 0.5,
         FORMAT('confidence %.2f < 0.5', confidence), NULL),
      IF(announcement_kind IN ('NEW_CITY', 'SHOW_INFO')
           AND NOT EXISTS (SELECT 1 FROM UNNEST(shows) s WHERE s.event_date IS NOT NULL),
         '일정 공지인데 확정 날짜 없음', NULL),
      IF(announcement_kind IN ('NEW_CITY', 'SHOW_INFO')
           AND EXISTS (SELECT 1 FROM UNNEST(shows) s WHERE s.city IS NULL OR s.city = ''),
         '도시 결측', NULL),
      IF(ARRAY_LENGTH(artist_entity_ids) = 0,
         'entity_master 매칭 실패 - 로스터 등록 필요', NULL)
    ]) AS r
    WHERE r IS NOT NULL
  ), '; '), '')
WHERE is_relevant;

-- 투어와 무관한 행은 확인 필요 대상이 아니다.
UPDATE `makestar-dw.makestar_ax.x_tour_announcements`
SET needs_review = FALSE, review_reason = NULL
WHERE NOT is_relevant AND (needs_review OR review_reason IS NOT NULL);
