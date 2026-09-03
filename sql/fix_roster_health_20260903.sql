-- [2026-09-03] 팔로워 의심 기준을 계정 유형별로 나눈다.
--
-- 문제: 기준이 "팔로워 1만 미만" 단일값이었다. 아이돌 공식계정을 염두에 두고 만든 값인데
--   판매처에까지 그대로 적용돼서, 첫 전체 크롤 후 40건이 한꺼번에 CHECK_핸들의심 으로
--   떴다. 그런데 40건이 전부 SELLER/PROMOTER 다. 아티스트는 0건.
--
-- 실측 분포를 보면 애초에 같은 잣대를 댈 대상이 아니었다.
--   ARTIST    98개  중앙값 47.9만  최솟값 11,542   1만 미만 0건
--   SELLER    71개  중앙값  9,339  최솟값    183   1만 미만 37건 (52%)
--   PROMOTER  17개  중앙값 12.9만  최솟값  5,152   1만 미만 3건
--
--   중소 음반 판매처가 팔로워 수천인 건 정상이다. MusicKorea(9,789)나
--   Whosfan Store(6,543)를 "핸들 의심" 으로 띄우는 건 잘못된 경보다.
--
-- 경보가 40건 뜨면 아무도 안 본다. 확인 필요 큐를 62% -> 28% 로 줄였던 것과 같은 이유다.
--   보라고 만든 목록에 볼 필요 없는 게 섞이면 목록 자체를 안 보게 된다.
--
-- 새 기준:
--   ARTIST            1만 미만  - 아이돌 공식계정 치고 적으면 다른 계정일 가능성이 높다.
--                                 실제로 이 기준이 김희철(3,750), (여자)아이들(0),
--                                 태민 구핸들(39)을 잡아냈다.
--   SELLER/PROMOTER   500 미만  - 이 밑이면 계정 자체가 잘못됐거나 사실상 죽은 계정이다.

CREATE OR REPLACE VIEW `makestar-dw.makestar_ax.v_roster_health` AS
SELECT
  s.entity_type, s.x_handle, s.entity_id, e.name_en,
  e.confirmation_status,
  s.x_follower_count AS followers,
  s.last_run_status, s.last_crawled_at,
  DATE_DIFF(CURRENT_DATE('Asia/Seoul'), DATE(s.last_crawled_at), DAY) AS days_since_crawl,
  CASE
    WHEN s.last_run_status <> 'SUCCESS'      THEN 'ERROR_수집실패'
    WHEN s.x_follower_count IS NULL          THEN 'CHECK_팔로워없음'
    WHEN s.entity_type = 'ARTIST'
         AND s.x_follower_count < 10000      THEN 'CHECK_핸들의심'
    WHEN s.entity_type <> 'ARTIST'
         AND s.x_follower_count < 500        THEN 'CHECK_핸들의심'
    WHEN DATE_DIFF(CURRENT_DATE('Asia/Seoul'), DATE(s.last_crawled_at), DAY) > 2
                                             THEN 'CHECK_수집중단'
    ELSE 'OK'
  END AS health
FROM `makestar-dw.makestar_ax.x_crawl_state` s
LEFT JOIN (SELECT entity_id, ANY_VALUE(name_en) name_en,
                  ANY_VALUE(confirmation_status) confirmation_status
           FROM `makestar-dw.makestar_ax.entity_master` GROUP BY entity_id) e USING (entity_id);


-- 확인: CHECK_핸들의심 이 40건에서 3건으로 줄어야 한다 (전부 SELLER).
SELECT health, entity_type, COUNT(*) AS cnt
FROM `makestar-dw.makestar_ax.v_roster_health`
GROUP BY 1, 2 ORDER BY 1, cnt DESC;
