-- [2026-09-02] 구형식으로 나간 9건을 새 형식(요약 + 스레드)으로 다시 보내려고 대기 상태로 돌린다.
--
-- 대상: 2026-09-02 17:12 KST 에 채널로 나간 9건.
--   x_tour_notified 에서 channel='#test-delphi', status='SENT', error_note IS NULL 인 행.
--   백필 시드분 108건은 error_note 에 사유가 적혀 있어서 자연히 구분된다.
--
-- 지우지 않고 status 만 바꾸는 이유: 발송 이력은 남겨야 나중에 "이 트윗을 언제
--   몇 번 보냈나" 를 추적할 수 있다. notify_tour_slack.py 의 안티조인은
--   status='SENT' 만 걸러내므로, 다른 값으로 바꾸면 그대로 재발송 대상이 된다.
--
-- 슬랙에 남아 있는 구형식 메시지 9건은 이 SQL 로 사라지지 않는다. 슬랙에서 직접 삭제할 것.

UPDATE `makestar-dw.makestar_ax.x_tour_notified`
SET status = 'SUPERSEDED',
    error_note = '구형식(공지당 1메시지)으로 발송됨. 새 형식 재발송 위해 대기로 되돌림 (requeue_tour_notified_20260902)'
WHERE channel = '#test-delphi'
  AND status = 'SENT'
  AND error_note IS NULL;

-- 확인: 발송 대기 건수 (9건이면 정상)
SELECT COUNT(DISTINCT a.tweet_id) AS pending
FROM `makestar-dw.makestar_ax.x_tour_announcements` a
LEFT JOIN (
  SELECT DISTINCT tweet_id FROM `makestar-dw.makestar_ax.x_tour_notified`
  WHERE channel = '#test-delphi' AND status = 'SENT'
) n USING (tweet_id)
WHERE a.is_relevant AND n.tweet_id IS NULL;
