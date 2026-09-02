-- 빈 추출 결과 삭제 - 2026-09-01 변수 섀도잉 사고로 들어간 192행
--
-- 왜 지워야 하나
--   curate_tour.py 는 x_tour_announcements 에 이미 있는 tweet_id 를 안티조인으로 제외한다.
--   그래서 내용이 없는 이 192행을 남겨두면 해당 트윗들이 영원히 재처리 대상에서 빠진다.
--   사고 당시 프롬프트에 트윗 본문이 안 들어가서 모델이 전부
--   "실제 트윗 본문이 제공되지 않아 판단 불가" 로 답한 결과물이다. 보존 가치가 없다.
--
-- 안전장치
--   판정 결과가 조금이라도 담긴 행은 건드리지 않도록 다섯 조건을 전부 만족할 때만 지운다.
--   실행 전 아래 SELECT 로 건수를 먼저 확인할 것. 정상 추출이 섞여 있으면 0 이 아닌 값이 나온다.

-- 확인용 (지우기 전에 먼저 실행)
SELECT
  COUNTIF(NOT is_relevant AND announcement_kind IS NULL AND tour_name IS NULL
          AND ARRAY_LENGTH(shows) = 0 AND event_type IS NULL) AS will_delete,
  COUNTIF(is_relevant OR announcement_kind IS NOT NULL OR tour_name IS NOT NULL
          OR ARRAY_LENGTH(shows) > 0 OR event_type IS NOT NULL) AS will_keep
FROM `makestar-dw.makestar_ax.x_tour_announcements`;

-- 삭제
DELETE FROM `makestar-dw.makestar_ax.x_tour_announcements`
WHERE NOT is_relevant
  AND announcement_kind IS NULL
  AND tour_name IS NULL
  AND ARRAY_LENGTH(shows) = 0
  AND event_type IS NULL;

-- 발송 이력도 같이 정리 (해당 행은 is_relevant=false 라 발송된 적은 없지만 방어적으로)
DELETE FROM `makestar-dw.makestar_ax.x_tour_notified`
WHERE tweet_id NOT IN (SELECT tweet_id FROM `makestar-dw.makestar_ax.x_tour_announcements`);
