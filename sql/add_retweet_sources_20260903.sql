-- [2026-09-03] 리트윗 출처 계정 추가 — eval 오답 근본 원인 대응
--
-- 배경: Sonnet 5 / Terra / Sol 세 모델을 같은 라벨 60건으로 평가했더니,
--   오답이 전부 같은 자리에서 났다. 전부 "잘린 리트윗" 이다.
--     Sonnet 5  오답 1건 중 1건
--     Terra     오답 3건 중 3건
--     Sol       오답 4건 중 3건
--   X API v2 는 리트윗 원문을 그대로 주지 않는다(전체 리트윗의 86.2%가 잘림).
--   판매 링크와 특전 내용이 잘려나간 뒤라 모델이 판단할 근거 자체가 없다.
--   프롬프트로는 못 고친다. 출처 계정을 직접 수집해야 원문이 들어온다.
--
-- 확인된 사례: CRAVITY ReDeFINE 애플뮤직 팬사인 이벤트
--   applemusic_m 원문 (240자, 2026-07-26) -> 이미 수집 중. 정상 판정됨.
--   CRAVITYstarship 리트윗 (144자, 잘림) -> Terra 가 제외. 하지만 원문이 있어 실제 유실은 없다.
--   즉 출처를 수집하고 있으면 리트윗 사본이 잘려도 손실이 없다.
--   반대로 hello82official, WillMusic_X 는 수집하지 않아 원문이 0건이었다. 이게 진짜 유실이다.
--
-- 선정: 리트윗 출처 중 이벤트·공연 공지를 내는 계정. 3건 이상.
--   제외한 것 - 자사 계정(MAKESTARSPACEGN), 음악방송(mnetplus, idolradiokorea),
--   패션(musinsaofficial), 굿즈 전용(HYBE_MERCH), 단일 목적 계정.
--
-- x_crawl_targets.json 도 함께 갱신했다. 2026-08-07 에 겪은 MERGE 버그 때문에
--   두 곳을 같이 건드려야 한다.

INSERT INTO `makestar-dw.makestar_ax.entity_master`
  (entity_id, entity_type, name, name_en, x_handle, x_profile_url,
   confirmation_status, notes, last_verified_date, created_at, updated_at)
SELECT s.entity_id, s.entity_type, s.name, s.name_en, s.x_handle,
       CONCAT('https://x.com/', s.x_handle), s.confirmation_status, s.notes,
       CURRENT_DATE('Asia/Seoul'), CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
FROM UNNEST([
  STRUCT<entity_id STRING, entity_type STRING, name STRING, name_en STRING,
         x_handle STRING, confirmation_status STRING, notes STRING>
  ('fansshop', 'SELLER', 'the_FANSSHOP', 'the_FANSSHOP', 'the_FANSSHOP', 'CONFIRMED', '리트윗 출처 67건. 팬스샵. 스트레이 키즈·ITZY·NMIXX 등 다수 아티스트 이벤트 출처'),
  ('hello82_official', 'SELLER', 'hello82official', 'hello82official', 'hello82official', 'CONFIRMED', '리트윗 출처 35건. hello82 미주 판매처. 2026-09-03 eval 오답의 출처'),
  ('applemusic_kr', 'SELLER', 'applemusic_m', 'applemusic_m', 'applemusic_m', 'CONFIRMED', '리트윗 출처 26건. 애플뮤직 코리아. 팬사인·영상통화 이벤트 주최. eval 오답의 출처'),
  ('melon_music', 'SELLER', 'melon', 'melon', 'melon', 'CONFIRMED', '리트윗 출처 26건. 멜론'),
  ('minive_store', 'SELLER', 'MINIVE_STORE', 'MINIVE_STORE', 'MINIVE_STORE', 'CONFIRMED', '리트윗 출처 14건. 아이브 공식 스토어'),
  ('beatroad_event', 'SELLER', 'beatroadevent', 'beatroadevent', 'beatroadevent', 'CONFIRMED', '리트윗 출처 13건. 비트로드 이벤트 전용 계정 (BEATROAD1 과 별개)'),
  ('kpop_together', 'SELLER', 'KPOP__TOGETHER', 'KPOP__TOGETHER', 'KPOP__TOGETHER', 'CONFIRMED', '리트윗 출처 12건. 케이팝 투게더'),
  ('genie_music', 'SELLER', 'genie_kt', 'genie_kt', 'genie_kt', 'CONFIRMED', '리트윗 출처 11건. 지니뮤직'),
  ('bugs_music', 'SELLER', 'bugs_official_', 'bugs_official_', 'bugs_official_', 'CONFIRMED', '리트윗 출처 11건. 벅스'),
  ('myidol_miim', 'SELLER', 'myidolmiim', 'myidolmiim', 'myidolmiim', 'CONFIRMED', '리트윗 출처 8건. 마이아이돌 미임'),
  ('fantheone', 'SELLER', 'FANTHEONE_offcl', 'FANTHEONE_offcl', 'FANTHEONE_offcl', 'CONFIRMED', '리트윗 출처 6건. 팬디원'),
  ('willmusic', 'SELLER', 'WillMusic_X', 'WillMusic_X', 'WillMusic_X', 'CONFIRMED', '리트윗 출처 5건. 대만 WillMusic. eval 오답의 출처'),
  ('popmerch_cn', 'SELLER', 'popmerchchina', 'popmerchchina', 'popmerchchina', 'CONFIRMED', '리트윗 출처 5건. 중화권 굿즈·음반 판매처'),
  ('seven_eleven', 'SELLER', 'seveneleven_kr', 'seveneleven_kr', 'seveneleven_kr', 'CONFIRMED', '리트윗 출처 5건. 세븐일레븐. 편의점 음반 콜라보 이벤트'),
  ('pandora_music', 'SELLER', 'pandoramusic', 'pandoramusic', 'pandoramusic', 'CONFIRMED', '리트윗 출처 4건. 판도라뮤직'),
  ('yg_select', 'SELLER', 'ygselect', 'ygselect', 'ygselect', 'CONFIRMED', '리트윗 출처 3건. YG SELECT'),
  ('yx_labels', 'PROMOTER', 'YX_LABELS', 'YX_LABELS', 'YX_LABELS', 'CONFIRMED', '리트윗 출처 71건. &TEAM 레이블. 앨범 발매·트레일러 공지 출처'),
  ('applewood', 'PROMOTER', 'applewood_kr', 'applewood_kr', 'applewood_kr', 'CONFIRMED', '리트윗 출처 4건. 대만 공연 프로모터 (PLAVE 가오슝 등)')]) AS s
LEFT JOIN `makestar-dw.makestar_ax.entity_master` e
  ON e.entity_id = s.entity_id OR LOWER(e.x_handle) = LOWER(s.x_handle)
WHERE e.entity_id IS NULL;

-- 확인: 유형별 계정 수 (ARTIST 88 / SELLER 68 / PROMOTER 17 이면 정상)
SELECT entity_type, COUNT(*) AS cnt
FROM `makestar-dw.makestar_ax.entity_master`
WHERE x_handle IS NOT NULL
GROUP BY 1 ORDER BY cnt DESC;
