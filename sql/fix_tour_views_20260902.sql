-- v_tour_shows_latest 수정 + 베뉴 대기 뷰 신설
-- 2026-09-02
--
-- v_tour_shows_latest 가 조회 시점에 깨지고 있었다.
--   Analytic function array_agg does not support DISTINCT
-- ARRAY_AGG(DISTINCT ...) OVER (...) 를 썼는데 BigQuery 는 이 조합을 지원하지 않는다.
-- CREATE VIEW 는 본문을 그때 검증하지 않아서, 만들 때는 통과하고 쓸 때 터진다.
-- 창을 쓰지 말고 일반 집계로 먼저 접은 뒤 조인한다.

CREATE OR REPLACE VIEW `makestar-dw.makestar_ax.v_tour_shows_latest` AS
WITH agg AS (
  SELECT
    show_key,
    MIN(tweet_created_at) AS first_seen_at,
    STRING_AGG(DISTINCT announcement_kind, ', ' ORDER BY announcement_kind) AS announcement_kinds,
    COUNT(*) AS announcement_count
  FROM `makestar-dw.makestar_ax.v_tour_shows`
  GROUP BY show_key
),
pick AS (
  -- 같은 공연의 여러 공지 중 대표 하나를 고른다.
  -- 일자·베뉴가 확정된 공지(SHOW_INFO)를 우선하고, 그 안에서는 베뉴가 있는 것,
  -- 그다음 가장 최근 것.
  SELECT v.*,
    ROW_NUMBER() OVER (
      PARTITION BY v.show_key
      ORDER BY CASE v.announcement_kind
                 WHEN 'SHOW_INFO' THEN 0 WHEN 'NEW_CITY' THEN 1 ELSE 2 END,
               v.venue_name IS NULL,
               v.tweet_created_at DESC
    ) AS rn
  FROM `makestar-dw.makestar_ax.v_tour_shows` v
)
SELECT p.* EXCEPT (rn), a.first_seen_at, a.announcement_kinds, a.announcement_count
FROM pick p JOIN agg a USING (show_key)
WHERE p.rn = 1;


-- 베뉴 대기 큐
-- "투어 발표가 공연장 확정보다 먼저" 인 회차. 사람이 확인할 대상이 아니라
-- 나중에 Ticketmaster 조회나 후속 공지로 채워야 할 대상이다.
-- 확인 필요(needs_review) 와 분리해서 큐가 서로를 가리지 않게 한다.
CREATE OR REPLACE VIEW `makestar-dw.makestar_ax.v_tour_venue_pending` AS
SELECT
  show_key, event_date, tour_name, artist_names, city, country,
  announcement_kinds, first_seen_at, tweet_url,
  DATE_DIFF(event_date, CURRENT_DATE('Asia/Seoul'), DAY) AS days_until_show
FROM `makestar-dw.makestar_ax.v_tour_shows_latest`
WHERE venue_name IS NULL
  AND event_date >= CURRENT_DATE('Asia/Seoul')
ORDER BY event_date;
