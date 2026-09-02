-- [2026-09-02] 슬랙 첫 발송 전, 백필 분량을 "이미 보낸 것"으로 표시한다.
--
-- 문제: 30일치 백필로 투어 공지 120건이 쌓여 있다. 슬랙 알림을 그냥 켜면
--   하루 상한(TOUR_NOTIFY_MAX=20) 때문에 6일 동안 지난 뉴스가 밀려 나온다.
--   테스트 채널이 과거 소식으로 가득 차면 정작 새 소식을 못 알아본다.
--
-- 처리: 2026-09-01 이전 트윗은 발송 완료로 기록만 남긴다(실제 발송 없음).
--   9월 1일자 12건은 남겨두어 첫 실행에서 실제 메시지를 확인한다.
--
-- status='SEEDED' 로 넣으면 안 된다. notify_tour_slack.py 의 안티조인이
--   status='SENT' 만 걸러내기 때문이다. 대신 error_note 에 사유를 남겨
--   나중에 "진짜 보낸 것"과 구분할 수 있게 한다.

INSERT INTO `makestar-dw.makestar_ax.x_tour_notified`
  (tweet_id, channel, sent_at, status, error_note)
SELECT DISTINCT
  a.tweet_id,
  '#test-delphi' AS channel,
  CURRENT_TIMESTAMP() AS sent_at,
  'SENT' AS status,
  '백필 분량 - 실제 발송 안 함 (seed_tour_notified_20260902)' AS error_note
FROM `makestar-dw.makestar_ax.x_tour_announcements` a
LEFT JOIN (
  SELECT DISTINCT tweet_id
  FROM `makestar-dw.makestar_ax.x_tour_notified`
  WHERE channel = '#test-delphi' AND status = 'SENT'
) n USING (tweet_id)
WHERE a.is_relevant
  AND n.tweet_id IS NULL
  AND DATE(a.tweet_created_at) < DATE '2026-09-01';

-- 확인: 남은 발송 대기 건수 (12건이면 정상)
SELECT COUNT(DISTINCT a.tweet_id) AS pending
FROM `makestar-dw.makestar_ax.x_tour_announcements` a
LEFT JOIN (
  SELECT DISTINCT tweet_id
  FROM `makestar-dw.makestar_ax.x_tour_notified`
  WHERE channel = '#test-delphi' AND status = 'SENT'
) n USING (tweet_id)
WHERE a.is_relevant AND n.tweet_id IS NULL;
