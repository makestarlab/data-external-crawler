-- [2026-09-08] 프로모터 계정 공지에서 계정 핸들이 아티스트명으로 들어간 행을 지운다.
--
-- 증상: applewood_kr, Wanxing_ent, hello82PRESENTS, LeoPresents, BIGHIT_MUSIC 이
--   artist_names 자리에 들어왔다. 투어명에 KIM JI WON, EUNHYUK 이 적혀 있는데도.
--
-- 원인은 모델이 아니라 우리 프롬프트였다. build_user_message 가 계정 유형과 무관하게
--   "이 계정은 {계정주}본인의 공식 계정입니다. artist_names 는 ['{계정주}'] 로 두세요"
--   를 넣고 있었다. 프로모터 계정에도 그대로 들어갔으니 모델은 시킨 대로 한 것이다.
--   curate_tour.py 에 is_promoter 를 넣어 프로모터에는 반대 문구를 쓰도록 고쳤다.
--
-- 지우면 안티조인이 풀려 다음 실행에서 고친 프롬프트로 다시 추출된다.
-- 함께 들어간 배우 팬미팅(김지원)은 새로 추가한 '배우 제외' 규칙에서 걸러진다.
--
-- 실행 결과: 40행 삭제 (투어 공지 7건 + is_relevant=false 33건)

DELETE FROM `makestar-dw.makestar_ax.x_tour_announcements` a
WHERE LOWER(a.x_handle) IN (
        SELECT LOWER(x_handle) FROM `makestar-dw.makestar_ax.entity_master`
        WHERE entity_type = 'PROMOTER' AND x_handle IS NOT NULL)
  AND EXISTS (SELECT 1 FROM UNNEST(a.artist_names) n
              WHERE LOWER(n) = LOWER(a.x_handle));

-- 확인: 프로모터 계정 공지 중 아직 핸들이 아티스트명인 행 (0 이어야 한다)
SELECT COUNT(*) AS still_wrong
FROM `makestar-dw.makestar_ax.x_tour_announcements` a
WHERE LOWER(a.x_handle) IN (
        SELECT LOWER(x_handle) FROM `makestar-dw.makestar_ax.entity_master`
        WHERE entity_type = 'PROMOTER' AND x_handle IS NOT NULL)
  AND EXISTS (SELECT 1 FROM UNNEST(a.artist_names) n
              WHERE LOWER(n) = LOWER(a.x_handle));
