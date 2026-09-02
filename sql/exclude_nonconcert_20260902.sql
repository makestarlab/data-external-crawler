-- [2026-09-02] 공연이 아닌 행사 4건을 대상에서 제외한다.
--
-- 배경: 30일 백필은 판정 규칙을 고치기 전에 돌았다. 오늘 커밋한
--   prompts/tour_extraction_rules.md 에 "전시회, 패션쇼는 대상 아님" 을 명시했지만,
--   이미 적재된 행에는 소급 적용되지 않는다.
--
-- 대상 (실측으로 확인한 4건):
--   - NCT 10TH ANNIVERSARY EXHIBITION : NEO DIMENSION  (전시회, 2건 - 한국어판/영어판)
--   - 제43회 도쿄 걸즈 컬렉션 (TGC)                     (패션쇼, 2건)
--
-- 삭제하지 않고 is_relevant 를 내리는 이유: 행을 지우면 안티조인이 풀려서
--   다음 실행 때 같은 트윗을 다시 Claude 로 보낸다. 돈만 쓰고 결과는 같다.
--   플래그만 내리면 슬랙 발송·v_tour_shows 양쪽에서 동시에 빠진다.

UPDATE `makestar-dw.makestar_ax.x_tour_announcements`
SET
  is_relevant = FALSE,
  needs_review = FALSE,
  review_reason = NULL,
  note = CONCAT(IFNULL(note, ''),
                ' [2026-09-02 수동 제외: 공연이 아닌 행사(전시회/패션쇼)]')
WHERE tweet_id IN (
  '2094745011019722840',  -- NCT EXHIBITION (영문)
  '2094745004367614448',  -- NCT EXHIBITION (한글)
  '2094688420270604532',  -- 도쿄 걸즈 컬렉션
  '2092537539953586183'   -- 도쿄 걸즈 컬렉션
);

-- 확인: 슬랙 발송 대기 건수 (9건이면 정상)
SELECT COUNT(DISTINCT a.tweet_id) AS pending
FROM `makestar-dw.makestar_ax.x_tour_announcements` a
LEFT JOIN (
  SELECT DISTINCT tweet_id FROM `makestar-dw.makestar_ax.x_tour_notified`
  WHERE channel = '#test-delphi' AND status = 'SENT'
) n USING (tweet_id)
WHERE a.is_relevant AND n.tweet_id IS NULL;
