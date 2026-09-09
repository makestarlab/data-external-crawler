-- [2026-09-09] 웹 검색 조사로 확정한 공식 X 계정 반영
--
-- 배경: 글로벌 투어 데이터베이스 시트의 IP 표기를 entity_master 와 대조해
--       X 계정이 없던 57팀을 추려낸 뒤, 각 팀의 공식 계정을 웹 검색으로 조사했다.
--       조사 결과는 확신도로 나눠 처리한다.
--         높음(45팀)  -> x_handle 부여 + CONFIRMED, 수집 대상에 즉시 추가
--         중간(8팀)   -> 핸들을 넣지 않고 notes 에 후보만 남긴 뒤 UNCERTAIN, 팀 확인 대기
--         계정없음(4팀) -> NO_PERSONAL_ACCOUNT + represented_by_handle 로 대체 계정 지정
--
-- 핸들을 확신 없이 넣으면 조용한 계정을 붙잡고 X API 비용만 쓰게 되므로,
-- 확신도 '중간'은 일부러 수집 대상에서 뺐다.
--
-- 실행 완료: 37 UPDATE + 8 INSERT + 3 NO_PERSONAL_ACCOUNT + 5 UNCERTAIN
-- x_crawl_targets.json 도 같은 커밋에서 186 -> 231 개로 갱신했다.

-- 1) 기존 entity_master 아티스트 37팀에 x_handle 부여
UPDATE `makestar-dw.makestar_ax.entity_master` t SET x_handle = s.x_handle, x_profile_url = CONCAT('https://x.com/', s.x_handle), confirmation_status = 'CONFIRMED', last_verified_date = DATE '2026-09-09', notes = IFNULL(t.notes, s.note), updated_at = CURRENT_TIMESTAMP() FROM (SELECT * FROM UNNEST([ STRUCT('just_b' AS entity_id, 'JUSTB_Official' AS x_handle, '블루닷엔터 공식. 일본 계정 JUSTB_offcl_jp 별도' AS note), STRUCT('xlov' AS entity_id, 'XLOV_official' AS x_handle, '소속사 운영 공식, 2026년까지 활동' AS note), STRUCT('onewe' AS entity_id, 'official_ONEWE' AS x_handle, 'RBW 공식, onewe.co.kr 연동' AS note), STRUCT('lucy' AS entity_id, 'BANDLUCY_mystic' AS x_handle, '미스틱스토리 직접 운영 확인' AS note), STRUCT('dragon_pony' AS entity_id, 'DragonPony_' AS x_handle, '안테나 소속, 팬클럽 공지 게시' AS note), STRUCT('junhee' AS entity_id, 'official_junhee' AS x_handle, 'H&P엔터 솔로 활동 계정' AS note), STRUCT('unis' AS entity_id, 'UNIS_offcl' AS x_handle, 'F&F 운영. 글로벌 서브 UNIS_glbl 도 공식' AS note), STRUCT('qwer' AS entity_id, 'official_QWER' AS x_handle, '데뷔 초부터 운영' AS note), STRUCT('kiss_of_life' AS entity_id, 'KISSOFLIFE_S2' AS x_handle, '데뷔 시 공식 채널 오픈 공지 게시' AS note), STRUCT('yena' AS entity_id, 'YENA_OFFICIAL' AS x_handle, '유튜브 채널과 동일 핸들' AS note), STRUCT('82major' AS entity_id, '82MAJOR_231011' AS x_handle, '숫자는 데뷔일(2023.10.11)' AS note), STRUCT('allhours' AS entity_id, 'ALL_H_OURS' AS x_handle, '소속사 공지·컴백 스케줄 게시 계정' AS note), STRUCT('ifeye' AS entity_id, 'ifeye_official' AS x_handle, '공식 프로필 시리즈 게시' AS note), STRUCT('everglow' AS entity_id, 'everglow_offcl' AS x_handle, '2025-09 신규 개설. 구 위에화 계정 EVERGLOW_twt 는 사용 중단' AS note), STRUCT('apink' AS entity_id, 'Apink_2011' AS x_handle, 'IST엔터 운영, 위키데이터 등재' AS note), STRUCT('younite' AS entity_id, 'YOUNITE_offcl' AS x_handle, '브랜뉴뮤직 메인. YOUNITE_twt(멤버 소통)·YOUNITE_JP 별도' AS note), STRUCT('ioi' AS entity_id, 'ioi_10th' AS x_handle, '2026-03 데뷔 10주년 재결합용 신규 개설. 구 계정 ioi_official_ 비활성' AS note), STRUCT('tempest' AS entity_id, 'TPST__official' AS x_handle, '그룹 공식. TPST_twt 는 멤버 소통용' AS note), STRUCT('asc2nt' AS entity_id, 'ASC2NT_OFFICIAL' AS x_handle, 'NEWWAYS COMPANY 소속' AS note), STRUCT('oneus' AS entity_id, 'official_ONEUS' AS x_handle, 'RBW 공식' AS note), STRUCT('fromis_9' AS entity_id, 'realfromis_9' AS x_handle, '메인 공식. 일본 fromis9_japan 별도' AS note), STRUCT('baby_dont_cry' AS entity_id, 'BabyDONTCry_BDC' AS x_handle, 'P NATION 소속, 2025-06 데뷔' AS note), STRUCT('lim_young_min' AS entity_id, 'LYM_offcl' AS x_handle, 'NXN엔터테인먼트 소속, 현재 운영 중' AS note), STRUCT('the_boyz' AS entity_id, 'THEBOYZ_officl' AS x_handle, '원헌드레드 이적 후 현재 계정. 구 IST_THEBOYZ' AS note), STRUCT('mark_tuan' AS entity_id, 'marktuan' AS x_handle, '본인 인증 계정, 공식 링크트리 연결' AS note), STRUCT('lightsum' AS entity_id, 'CUBE_LIGHTSUM' AS x_handle, '해체 아님. 2026-08 데뷔 5주년, 정상 운영 중' AS note), STRUCT('keyvitup' AS entity_id, 'KEYVITUP_inkode' AS x_handle, 'INKODE 엔터 운영. Keyveatz 와 별개 팀' AS note), STRUCT('rescene' AS entity_id, 'RESCENEofficial' AS x_handle, '나무위키 공식 SNS. RESCENE_twt 는 멤버 자체 운영' AS note), STRUCT('nowz' AS entity_id, 'CUBE_NOWZ' AS x_handle, '2025-06 NOWADAYS→NOWZ 개명 후 계정' AS note), STRUCT('lee_chang_sub' AS entity_id, 'LeeCS_Official' AS x_handle, '판타지오 운영. 본인 개인계정 LeeCS_BTOB 별도' AS note), STRUCT('idntt' AS entity_id, 'idntt_cosmo' AS x_handle, '모드하우스(MODHAUS) 소속' AS note), STRUCT('bi' AS entity_id, 'BI_131official' AS x_handle, '131레이블 운영. 본인 개인계정 shxx131bi131 별도' AS note), STRUCT('keyveatz' AS entity_id, 'Keyveatz' AS x_handle, 'AOMG 소속 2026-06 데뷔. KEYVITUP 과 무관' AS note), STRUCT('yoon_sanha' AS entity_id, 'YOONSANHA_offcl' AS x_handle, '판타지오 운영' AS note), STRUCT('sunmi' AS entity_id, 'official_sunmi_' AS x_handle, '2026년까지 공식 프로모션 채널' AS note), STRUCT('jinho' AS entity_id, 'jinhojo_x' AS x_handle, '현 소속사 S27M 이 태그하는 본인 계정. 구 PTGjinho 비활성' AS note), STRUCT('bambam' AS entity_id, 'BamBam1A' AS x_handle, '본인 인증 계정, 투어 공지 주 채널' AS note) ])) s WHERE t.entity_id = s.entity_id AND t.entity_type = 'ARTIST';

-- 2) 마스터 미등록 8팀 신규 등록
INSERT INTO `makestar-dw.makestar_ax.entity_master` (entity_id, entity_type, artist_subtype, name, name_en, aliases, x_handle, x_profile_url, confirmation_status, notes, last_verified_date, created_at, updated_at) VALUES ('akmu', 'ARTIST', 'GROUP', '악뮤', 'AKMU', ['악동뮤지션', 'AKMU', 'Akdong Musician'], 'official_akmu', CONCAT('https://x.com/', 'official_akmu'), 'CONFIRMED', '나무위키 인포박스 등재', DATE '2026-09-09', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()), ('evan', 'ARTIST', 'SOLO', '에반', 'EVAN', [], 'h_evva_n', CONCAT('https://x.com/', 'h_evva_n'), 'CONFIRMED', '빌리프랩 공식 아티스트 페이지 링크. ENHYPEN 출신 솔로', DATE '2026-09-09', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()), ('sandara_park', 'ARTIST', 'SOLO', '산다라박', 'Sandara Park', ['2NE1 산다라박', 'Dara'], 'krungy21', CONCAT('https://x.com/', 'krungy21'), 'CONFIRMED', '본인 계정. 구 소속사 계정 SANDARAxABYSS 는 계약 만료', DATE '2026-09-09', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()), ('shaun', 'ARTIST', 'SOLO', '숀', 'SHAUN', [], 'shaunthehuman', CONCAT('https://x.com/', 'shaunthehuman'), 'CONFIRMED', '나무위키 인포박스, 인스타·틱톡과 동일', DATE '2026-09-09', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()), ('bol4', 'ARTIST', 'SOLO', '볼빨간사춘기', 'BOL4', ['볼빨간사춘기', 'BOL4', 'Bolbbalgan4'], 'BOL4_Official', CONCAT('https://x.com/', 'BOL4_Official'), 'CONFIRMED', '한국 공식. 일본 전용 bol4_japan 별도', DATE '2026-09-09', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()), ('befirst', 'ARTIST', 'GROUP', 'BE:FIRST', 'BE:FIRST', ['BEFIRST', '비퍼스트'], 'BEFIRSTofficial', CONCAT('https://x.com/', 'BEFIRSTofficial'), 'CONFIRMED', 'BMSG 일본 공식. 영어권 BEFIRST_global 별도', DATE '2026-09-09', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()), ('cosmosy', 'ARTIST', 'GROUP', 'cosmosy', 'cosmosy', ['코스모시'], 'cosmosy_x', CONCAT('https://x.com/', 'cosmosy_x'), 'CONFIRMED', '소속사 보도자료에 명기. NTT도코모 스튜디오&라이브 소속', DATE '2026-09-09', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()), ('girlset', 'ARTIST', 'GROUP', 'GIRLSET', 'GIRLSET', ['걸셋'], 'GIRLSETofficial', CONCAT('https://x.com/', 'GIRLSETofficial'), 'CONFIRMED', 'JYP America 소속, 2025-08 데뷔', DATE '2026-09-09', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP());

-- 3) 본인 명의 X 계정이 없는 3팀: 대체 계정 지정
UPDATE `makestar-dw.makestar_ax.entity_master` t
SET confirmation_status = 'NO_PERSONAL_ACCOUNT', represented_by_handle = s.rep,
    notes = s.note, last_verified_date = DATE '2026-09-09', updated_at = CURRENT_TIMESTAMP()
FROM (SELECT * FROM UNNEST([
  STRUCT('young_k' AS entity_id, 'day6official' AS rep, '2026-09-09 조사: JYP 공식 프로필에 그룹 계정만. 개인 X 계정 없음(인스타그램만). DAY6 공식으로 커버' AS note),
  STRUCT('kino' AS entity_id, CAST(NULL AS STRING) AS rep, '2026-09-09 조사: 공식 SNS 는 인스타그램(831x10)/유튜브/틱톡뿐. X 검색 결과는 전부 팬계정' AS note),
  STRUCT('moon_byul' AS entity_id, 'RBW_MAMAMOO' AS rep, '2026-09-09 조사: 솔로 공식 X 없음. 솔로 활동도 마마무 공식으로 공지. 일본 한정 MOONBYUL_JP 존재' AS note)
])) s
WHERE t.entity_id = s.entity_id AND t.entity_type = 'ARTIST';

-- 4) 확신도 '중간' 5팀: 후보만 남기고 수집 대상에서 제외
UPDATE `makestar-dw.makestar_ax.entity_master` t
SET confirmation_status = 'UNCERTAIN', notes = s.note,
    last_verified_date = DATE '2026-09-09', updated_at = CURRENT_TIMESTAMP()
FROM (SELECT * FROM UNNEST([
  STRUCT('heechul' AS entity_id, '2026-09-09 조사 후보: heezzinpang (2013년 본인 개설). 최근 활동은 인스타그램 중심이라 휴면 가능성. 팀 확인 대기' AS note),
  STRUCT('yerin' AS entity_id, '2026-09-09 조사 후보: YERIN_OFFICIAL_ 또는 official_yerin. 2025년 A-Side Company 이적으로 계정 변경 가능성. 팀 확인 대기' AS note),
  STRUCT('jay_b' AS entity_id, '2026-09-09 조사 후보: jaybnow_hr (본인 인증 개인계정). 소속사 운영 공식 X 는 미확인. 팀 확인 대기' AS note),
  STRUCT('rocky' AS entity_id, '2026-09-09 조사 후보: p_rockyent. kprofiles 에만 기재되고 위키 인포박스에는 없음. 팀 확인 대기' AS note),
  STRUCT('nct_mark' AS entity_id, '2026-09-09 조사: 2026-04 NCT 탈퇴 후 1인 기획사 Upper Room 설립. 소속사 계정 upperroomlabel 이 유일하고 개인 명의 계정 없음. 팀 확인 대기' AS note)
])) s
WHERE t.entity_id = s.entity_id AND t.entity_type = 'ARTIST';

-- 마스터에 아직 넣지 않은 3팀 (확신도 중간, 신규): Jessi(jessicah_oo), 다영/우주소녀(DAYOUNG_offcl), 이무진(BPM_LMJ)
-- 팀 확인 후 2번 블록과 같은 방식으로 INSERT 한다.
