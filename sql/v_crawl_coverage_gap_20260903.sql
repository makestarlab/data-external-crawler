-- [2026-09-03] 크롤 커버리지 구멍을 상시로 드러내는 뷰.
--
-- 왜 필요한가: 계정을 추가하려면 entity_master(데이터)와 x_crawl_targets.json(코드)
--   두 곳을 같이 건드려야 한다. 2026-08-07 에 x_crawl_state MERGE 버그로 한 번 데었고,
--   2026-09-03 에 또 났다 - entity_master 에는 있는데 크롤 대상에 없어 한 번도 안 긁힌
--   계정이 13개였다(아티스트 10, 판매처 3). 전부 2026-07-30 에 웹 리서치로 자동 추가된
--   것들인데 JSON 반영을 빠뜨린 것이다.
--
--   그중 yglobalmusic 은 노트에 "메이크스타와 거래 관계 있음" 이라고 적힌 판매처였다.
--   경쟁사 모니터링 대상인데 한 달 넘게 한 번도 안 봤다는 뜻이다.
--
-- 조용히 새는 종류의 사고라 사람이 알아채기 어렵다. 그래서 뷰로 만들어 둔다.
--   x_crawl_state 에 행이 없다 = 크롤러가 그 계정을 한 번도 대상으로 삼지 않았다
--   = x_crawl_targets.json 에 없다.
--
-- 이 뷰가 비어 있지 않으면 x_crawl_targets.json 을 갱신해야 한다는 신호다.

CREATE OR REPLACE VIEW `makestar-dw.makestar_ax.v_crawl_coverage_gap` AS
SELECT
  e.entity_type,
  e.entity_id,
  e.name,
  e.x_handle,
  e.confirmation_status,
  DATE(e.created_at, 'Asia/Seoul') AS registered_on,
  DATE_DIFF(CURRENT_DATE('Asia/Seoul'), DATE(e.created_at, 'Asia/Seoul'), DAY) AS days_unwatched,
  SUBSTR(IFNULL(e.notes, ''), 1, 120) AS notes
FROM `makestar-dw.makestar_ax.entity_master` e
LEFT JOIN (
  SELECT DISTINCT LOWER(x_handle) AS h FROM `makestar-dw.makestar_ax.x_crawl_state`
) s ON s.h = LOWER(e.x_handle)
WHERE e.x_handle IS NOT NULL
  AND s.h IS NULL
  -- 개인 계정이 없어 그룹 계정을 대신 적어둔 멤버 엔티티는 대상이 아니다.
  -- 그룹 계정 자체는 따로 크롤되고 있다.
  AND IFNULL(e.confirmation_status, '') <> 'NO_PERSONAL_ACCOUNT'
ORDER BY days_unwatched DESC, e.entity_type, e.x_handle;


-- 확인: 지금은 44건이 나온다.
--   days_unwatched 27~35 인 13건 = 이번에 발견한 진짜 구멍
--   days_unwatched 0   인 31건 = 오늘 추가한 출처·프로모터 계정 (아직 첫 크롤 전)
-- x_crawl_targets.json 을 반영한 크롤을 한 번 돌리면 전부 0 이 된다.
-- 평소에 볼 때는 days_unwatched 가 큰 것부터 보면 된다. 오래 방치된 게 진짜 사고다.
SELECT
  CASE WHEN days_unwatched >= 7 THEN '방치됨' ELSE '추가 직후(정상)' END AS bucket,
  entity_type, COUNT(*) AS gap
FROM `makestar-dw.makestar_ax.v_crawl_coverage_gap`
GROUP BY 1, 2 ORDER BY 1, gap DESC;
