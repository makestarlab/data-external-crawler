-- [2026-09-02] 리트윗 출처 계정 15개를 PROMOTER 로 추가 + 김희철 핸들 제거
--
-- 근거 (2026-07-01~2026-09-02 실측):
--   아티스트 계정 리트윗 4,138건 중 자기 리트윗은 1건. 나머지 전부가 타 계정 리트윗이다.
--   "자기 리트윗이라 중복" 이라는 전제가 틀렸다.
--   투어 키워드에 걸리는 리트윗 566건 중 417건(74%)이 우리가 수집하지 않는 118개 계정에서 왔다.
--
--   그런데 리트윗을 살려도 소용이 없다. 리트윗 본문의 86.2% 가 잘려 있다(평균 137자, 끝이 '…').
--   X API v2 가 리트윗 원문을 그대로 주지 않기 때문이다. 날짜·도시·베뉴가 잘린 텍스트로는
--   추출이 안 된다. 그래서 리트윗을 켜는 대신 출처 계정을 직접 수집한다.
--
-- 선정 기준: 리트윗 3건 이상 출처 중 공연 일정을 발표하는 계정만.
--   굿즈(HYBE_MERCH), 시상식(thefact_TMA), 음악방송(mnetplus, MnetMcountdown),
--   패션지(arenahommeplus) 는 제외했다. 투어 일정이 나오는 자리가 아니다.
--
-- entity_type 을 SELLER 가 아니라 PROMOTER 로 두는 이유:
--   SELLER 는 curate_events 에서 경쟁사 이벤트 판정 로직을 타는데, 이 계정들은
--   판매처가 아니라 소식의 출처다. 별도 타입으로 둬야 curate_tour 만 태울 수 있다.

INSERT INTO `makestar-dw.makestar_ax.entity_master`
  (entity_id, entity_type, artist_subtype, name, name_en, x_handle, x_profile_url,
   confirmation_status, notes, last_verified_date, created_at, updated_at)
SELECT s.entity_id, s.entity_type, s.artist_subtype, s.name, s.name_en, s.x_handle,
       CONCAT('https://x.com/', s.x_handle), s.confirmation_status, s.notes,
       CURRENT_DATE('Asia/Seoul'), CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
FROM UNNEST([
  STRUCT<entity_id STRING, entity_type STRING, artist_subtype STRING, name STRING,
         name_en STRING, x_handle STRING, confirmation_status STRING, notes STRING>
  ('xikers_jp', 'PROMOTER', NULL, 'xikers_jp', 'xikers_jp', 'xikers_jp', 'CONFIRMED', 'xikers 일본 공식. 일본 공연 일정 출처'),
  ('riize_jpn', 'PROMOTER', NULL, 'RIIZE_JPN', 'RIIZE_JPN', 'RIIZE_JPN', 'CONFIRMED', 'RIIZE 일본 공식'),
  ('plave_jp', 'PROMOTER', NULL, 'plave_jp', 'plave_jp', 'plave_jp', 'CONFIRMED', 'PLAVE 일본 공식'),
  ('ateez_jp', 'PROMOTER', NULL, 'ATEEZofficialjp', 'ATEEZofficialjp', 'ATEEZofficialjp', 'CONFIRMED', 'ATEEZ 일본 공식'),
  ('nct_jp', 'PROMOTER', NULL, 'NCT_OFFICIAL_JP', 'NCT_OFFICIAL_JP', 'NCT_OFFICIAL_JP', 'CONFIRMED', 'NCT 일본 공식'),
  ('tws_jp', 'PROMOTER', NULL, 'TWS_PLEDIS_JP', 'TWS_PLEDIS_JP', 'TWS_PLEDIS_JP', 'CONFIRMED', 'TWS 일본 공식'),
  ('hello82', 'PROMOTER', NULL, 'hello82PRESENTS', 'hello82PRESENTS', 'hello82PRESENTS', 'CONFIRMED', '미주 K-pop 공연 프로모터'),
  ('amaze_kr', 'PROMOTER', NULL, 'AMAZE_KR', 'AMAZE_KR', 'AMAZE_KR', 'CONFIRMED', '국내외 공연 기획사'),
  ('leo_presents', 'PROMOTER', NULL, 'LeoPresents', 'LeoPresents', 'LeoPresents', 'CONFIRMED', '공연 프로모터'),
  ('wanxing', 'PROMOTER', NULL, 'Wanxing_ent', 'Wanxing_ent', 'Wanxing_ent', 'CONFIRMED', '중화권 공연 기획사'),
  ('smtown_global', 'PROMOTER', NULL, 'SMTOWNGLOBAL', 'SMTOWNGLOBAL', 'SMTOWNGLOBAL', 'CONFIRMED', 'SM 글로벌 공식'),
  ('beliftlab', 'PROMOTER', NULL, 'BELIFTLAB', 'BELIFTLAB', 'BELIFTLAB', 'CONFIRMED', 'BELIFT LAB 공식'),
  ('bighit_music', 'PROMOTER', NULL, 'BIGHIT_MUSIC', 'BIGHIT_MUSIC', 'BIGHIT_MUSIC', 'CONFIRMED', 'BIGHIT MUSIC 공식'),
  ('fnc_ent', 'PROMOTER', NULL, 'fnfent_official', 'fnfent_official', 'fnfent_official', 'CONFIRMED', 'FNC 엔터테인먼트 공식'),
  ('weverse_official', 'PROMOTER', NULL, 'weverseofficial', 'weverseofficial', 'weverseofficial', 'CONFIRMED', 'Weverse 공식. 예매 오픈 공지')]) AS s
LEFT JOIN `makestar-dw.makestar_ax.entity_master` e ON e.entity_id = s.entity_id
WHERE e.entity_id IS NULL;


-- 김희철: HeeZZinPaang 은 팔로워 3,750 으로 공식계정이 아니다.
--   (신규 로스터 29계정 중 유일하게 기존 로스터 최솟값 27,470 을 크게 밑돈다)
--   슈퍼주니어 공식계정(SJofficial)이 이미 대체 계정으로 지정돼 있으므로 핸들만 비운다.
--   핸들을 남겨두면 크롤러가 엉뚱한 계정을 계속 긁는다.
UPDATE `makestar-dw.makestar_ax.entity_master`
SET x_handle = NULL,
    x_profile_url = NULL,
    confirmation_status = 'NO_PERSONAL_ACCOUNT',
    notes = '개인계정 HeeZZinPaang 은 팔로워 3,750 으로 공식 활동 공지용이 아님. SJofficial 경유',
    represented_by_handle = 'SJofficial',
    last_verified_date = CURRENT_DATE('Asia/Seoul'), updated_at = CURRENT_TIMESTAMP()
WHERE entity_id = 'heechul';

-- 크롤 상태에서도 제거하지 않으면 다음 실행에서 계속 조회한다.
DELETE FROM `makestar-dw.makestar_ax.x_crawl_state`
WHERE LOWER(x_handle) = 'heezzinpaang';

-- 확인
SELECT entity_type, COUNT(*) AS cnt
FROM `makestar-dw.makestar_ax.entity_master` GROUP BY 1 ORDER BY cnt DESC;
