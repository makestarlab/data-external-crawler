-- 핸들 정정 - 첫 크롤 팔로워 수 검증에서 걸러진 3건
-- 2026-09-01
--
-- 배운 것: x_crawl_state.last_run_status = 'SUCCESS' 는 핸들이 맞다는 증거가 아니다.
-- 존재하기만 하면 SUCCESS 다. 실제 검증은 팔로워 수로 한다 - 투어를 도는 아티스트의
-- 공식 계정이 팔로워 수천 명일 수는 없다.
--   G_I_DLE       팔로워      0  -> 폐쇄/빈 계정
--   TAEMIN_BPM    팔로워     39  -> 과거 소속사 계정. 현재 활동 공지는 다른 계정에서 나온다
--   HeeZZinPaang  팔로워  3,750  -> 계정명은 맞으나 규모가 안 맞음. 재개설 추정

-- (1) TAEMIN: Taemin_Xoalsox_ 가 실제 활동 공지 계정.
--     2026-27 TAEMIN WORLD TOUR <LiMiNaL> 11개 도시 발표도 이 계정에서 나왔다.
UPDATE `makestar-dw.makestar_ax.entity_master`
SET x_handle = 'Taemin_Xoalsox_',
    x_profile_url = 'https://x.com/Taemin_Xoalsox_',
    confirmation_status = 'CONFIRMED',
    notes = '2026-27 WORLD TOUR <LiMiNaL> 도시 발표를 이 계정에서 공지. 이전에 넣었던 TAEMIN_BPM 은 팔로워 39명으로 활동 공지 계정이 아님',
    last_verified_date = CURRENT_DATE('Asia/Seoul'), updated_at = CURRENT_TIMESTAMP()
WHERE entity_id = 'taemin';

-- (2) (G)I-DLE: 팀명을 i-dle 로 바꾸면서 계정도 official_i_dle 로 옮겼다.
--     시트가 'i-dle' 로 적고 있던 게 오타가 아니라 현재 표기였던 것.
UPDATE `makestar-dw.makestar_ax.entity_master`
SET x_handle = 'official_i_dle',
    x_profile_url = 'https://x.com/official_i_dle',
    confirmation_status = 'CONFIRMED',
    notes = '팀명 변경(G)I-DLE -> i-dle 에 따라 계정 이전. 기존 G_I_DLE 은 팔로워 0으로 사실상 폐쇄. cube_GIDLEoff 는 소속사 운영 구계정',
    aliases = ARRAY(SELECT DISTINCT a FROM UNNEST(ARRAY_CONCAT(IFNULL(aliases,[]),
      ['i-dle','IDLE','I-DLE','아이들','여자아이들','(G)I-DLE','GIDLE'])) a),
    last_verified_date = CURRENT_DATE('Asia/Seoul'), updated_at = CURRENT_TIMESTAMP()
WHERE entity_id = 'gidle';

-- (3) HEECHUL: 계정명은 공식 표기와 맞으나 팔로워 3,750 으로 규모가 안 맞는다.
--     지우지 않고 두되, 활동 공지는 이미 크롤링 중인 SUPER JUNIOR 공식 계정으로 대체 지정한다.
UPDATE `makestar-dw.makestar_ax.entity_master`
SET represented_by_handle = 'SJofficial',
    confirmation_status = 'UNCERTAIN',
    notes = '개인 계정 HeeZZinPaang 은 팔로워 3,750 으로 규모 불일치(재개설 추정). 솔로 활동 공지는 SJofficial 로 대체 수집',
    last_verified_date = CURRENT_DATE('Asia/Seoul'), updated_at = CURRENT_TIMESTAMP()
WHERE entity_id = 'heechul';

-- 크롤 상태에서 옛 핸들 행을 지운다. 안 지우면 틀린 계정을 계속 조회해 과금만 된다.
DELETE FROM `makestar-dw.makestar_ax.x_crawl_state`
WHERE x_handle IN ('TAEMIN_BPM', 'G_I_DLE', 'HeeZZinPaang');


-- ---------------------------------------------------------------------------
-- 로스터 건강 점검 뷰 - 같은 실수를 다음에는 사람 눈이 아니라 쿼리가 잡게 한다
-- ---------------------------------------------------------------------------
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
    WHEN s.x_follower_count < 10000          THEN 'CHECK_핸들의심'
    WHEN DATE_DIFF(CURRENT_DATE('Asia/Seoul'), DATE(s.last_crawled_at), DAY) > 2
                                             THEN 'CHECK_수집중단'
    ELSE 'OK'
  END AS health
FROM `makestar-dw.makestar_ax.x_crawl_state` s
LEFT JOIN (SELECT entity_id, ANY_VALUE(name_en) name_en, ANY_VALUE(confirmation_status) confirmation_status
           FROM `makestar-dw.makestar_ax.entity_master` GROUP BY entity_id) e USING (entity_id);
