-- entity_master 로스터 확장 - 투어 모니터링 대상 아티스트 공식 X 계정
-- 2026-09-01. 근거는 notes 컬럼에 남긴다.
-- confirmation_status: CONFIRMED(교차확인) / UNCERTAIN(후보 복수 또는 활성 여부 미확인)
--   UNCERTAIN 은 첫 크롤 실행 후 x_posts_raw 본문을 보고 확정할 것.
--   x_crawl_state.last_run_status 가 ERROR 면 핸들 자체가 틀린 것이다.

MERGE `makestar-dw.makestar_ax.entity_master` T
USING (SELECT * FROM UNNEST([STRUCT<entity_id STRING, artist_subtype STRING, name STRING, name_en STRING,
        x_handle STRING, confirmation_status STRING, notes STRING, aliases ARRAY<STRING>>
  ('bigbang','GROUP','빅뱅','BIGBANG','YG_GlobalVIP','CONFIRMED','Wikidata P2002 와 웹검색이 동일 계정을 지목. BIGBANG GLOBAL VIP',['BIG BANG','빅뱅']),
  ('katseye','GROUP','캣츠아이','KATSEYE','katseyeworld','CONFIRMED','HYBE/Geffen 공식. katseyye/weversekatseye 는 팬·Weverse 계정이라 제외',CAST([] AS ARRAY<STRING>)),
  ('jay_park','SOLO','박재범','Jay Park','JAYBUMAOM','CONFIRMED','본인 계정. 소속사 MORE VISION 은 MOREVISIONKR 로 별도',['JAY PARK','Jay Park','박재범']),
  ('lngshot','GROUP','롱샷','LNGSHOT','LNGSHOT4sho','CONFIRMED','MORE VISION 소속. 미래 공연 24행으로 갭 4위',CAST([] AS ARRAY<STRING>)),
  ('taemin','SOLO','태민','TAEMIN','TAEMIN_BPM','UNCERTAIN','현 소속사 BPM 운영 계정. Taemin_Xoalsox_ 도 후보라 첫 크롤 후 본문 확인 필요',CAST([] AS ARRAY<STRING>)),
  ('verivery','GROUP','베리베리','VERIVERY','the_verivery','CONFIRMED','VERIVERY_OFFICIAL. Jellyfish',CAST([] AS ARRAY<STRING>)),
  ('the_rose','GROUP','더로즈','The Rose','TheRose_0803','CONFIRMED','Wikidata P2002 와 웹검색 일치',['THE ROSE','더로즈']),
  ('omega_x','GROUP','오메가엑스','OMEGA X','OmegaX_official','CONFIRMED','',['OMEGA X','OMEGAX','오메가엑스']),
  ('monsta_x','GROUP','몬스타엑스','MONSTA X','OfficialMONSTAX','CONFIRMED','',['MONSTA X','MONSTAX','몬스타엑스']),
  ('triples','GROUP','트리플에스','tripleS','triplescosmos','CONFIRMED','MODHAUS. 소속사 계정은 officialmodhaus 로 별도',['tripleS','TRIPLES','트리플에스']),
  ('kard','GROUP','카드','KARD','KARD_Official','CONFIRMED','',CAST([] AS ARRAY<STRING>)),
  ('xg','GROUP','XG','XG','XGOfficial_','UNCERTAIN','XG__Official / XGOfficial__ 등 유사 계정 다수. 최근 게시가 있는 쪽을 채택',CAST([] AS ARRAY<STRING>)),
  ('yves','SOLO','이브','YVES','Yves___official','CONFIRMED','전 LOONA',CAST([] AS ARRAY<STRING>)),
  ('pow','GROUP','파우','POW','POW_grid','CONFIRMED','GRID. 일본 계정 pow_grid_jp 는 별도',CAST([] AS ARRAY<STRING>)),
  ('one_pact','GROUP','원팩트','ONE PACT','onepact_','CONFIRMED','',['ONE PACT','ONEPACT','원팩트']),
  ('ampersone','GROUP','앰퍼샌드원','AMPERS&ONE','_AMPERSANDONE_','CONFIRMED','시트 표기 \'AMPERS\' 가 별칭',['AMPERS','AMPERS&ONE','앰퍼샌드원']),
  ('stayc','GROUP','스테이씨','STAYC','STAYC_official','CONFIRMED','',CAST([] AS ARRAY<STRING>)),
  ('young_posse','GROUP','영파씨','YOUNG POSSE','youngposseup','CONFIRMED','',CAST([] AS ARRAY<STRING>)),
  ('leeteuk','SOLO','이특','Leeteuk','special1004','CONFIRMED','SUPER JUNIOR 공식 계정이 생일 축하에 태그한 계정',CAST([] AS ARRAY<STRING>)),
  ('heechul','SOLO','김희철','Heechul','HeeZZinPaang','UNCERTAIN','과거 계정 폐쇄·재개설 이력이 있어 첫 크롤 결과로 활성 여부 확인 필요',CAST([] AS ARRAY<STRING>)),
  ('jo1','GROUP','JO1','JO1','official_jo1','CONFIRMED','LAPONE 공식 계정이 태그한 계정',CAST([] AS ARRAY<STRING>)),
  ('bang_yongguk','SOLO','방용국','Bang Yongguk','BAP_Bangyongguk','CONFIRMED','최근 게시 활동 확인',['BANG YONGGUK','방용국']),
  ('xdinary_heroes','GROUP','엑스디너리 히어로즈','Xdinary Heroes','XH_official','CONFIRMED','JYP',['XDINARY HEROES','Xdinary Heroes','엑스디너리히어로즈']),
  ('mamamoo','GROUP','마마무','MAMAMOO','RBW_MAMAMOO','CONFIRMED','',CAST([] AS ARRAY<STRING>)),
  ('woodz','SOLO','조승연','WOODZ','c_woodzofficial','UNCERTAIN','c_woodofficial 과 표기가 한 글자 차이라 혼재. 첫 크롤 결과로 확정할 것',CAST([] AS ARRAY<STRING>)),
  ('ikon','GROUP','아이콘','iKON','iKONIC_143','UNCERTAIN','143 엔터 이관 후 계정. 팬 커뮤니티 성격이 섞여 있어 확인 필요',CAST([] AS ARRAY<STRING>)),
  ('highlight','GROUP','하이라이트','HIGHLIGHT','Highlight_AUent','CONFIRMED','Around Us',CAST([] AS ARRAY<STRING>)),
  ('cnblue','GROUP','씨엔블루','CNBLUE','official_CNBLUE','CONFIRMED','FNC',CAST([] AS ARRAY<STRING>))
])) S
ON T.entity_id = S.entity_id
WHEN MATCHED THEN UPDATE SET
  x_handle = S.x_handle,
  x_profile_url = CONCAT('https://x.com/', S.x_handle),
  confirmation_status = S.confirmation_status,
  notes = S.notes,
  aliases = S.aliases,
  last_verified_date = CURRENT_DATE('Asia/Seoul'),
  updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (entity_id, entity_type, artist_subtype, name, name_en, x_handle,
    x_profile_url, confirmation_status, notes, aliases, last_verified_date, created_at, updated_at)
  VALUES (S.entity_id, 'ARTIST', S.artist_subtype, S.name, S.name_en, S.x_handle,
    CONCAT('https://x.com/', S.x_handle), S.confirmation_status, S.notes, S.aliases,
    CURRENT_DATE('Asia/Seoul'), CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP());


-- WONHO 는 이미 entity_master 에 있으나 x_handle 이 비어 있었다.
UPDATE `makestar-dw.makestar_ax.entity_master`
SET x_handle = 'official__wonho',
    x_profile_url = 'https://x.com/official__wonho',
    confirmation_status = 'CONFIRMED',
    notes = 'Highline Entertainment 솔로 계약 후 개설한 공식 계정',
    last_verified_date = CURRENT_DATE('Asia/Seoul'), updated_at = CURRENT_TIMESTAMP()
WHERE entity_id = 'wonho';


-- (G)I-DLE: 핸들은 이미 있는데 시트 표기가 'i-dle' 이라 역색인에서 안 잡혔다.
-- 이 별칭이 없으면 시트의 19행이 조용히 NULL 로 빠진다.
UPDATE `makestar-dw.makestar_ax.entity_master`
SET aliases = ARRAY(SELECT DISTINCT a FROM UNNEST(ARRAY_CONCAT(IFNULL(aliases,[]),
      ['i-dle','IDLE','I-DLE','아이들','여자아이들'])) a),
    updated_at = CURRENT_TIMESTAMP()
WHERE entity_id = 'gidle';


-- Young K: 솔로 투어 공지가 본인 계정이 아니라 DAY6 공식 계정에서 나온다.
-- (2026 Young K Solo Tour <YOUNGEST> 발표도 @day6official 게시)
-- day6official 은 이미 크롤링 중이므로 신규 계정 추가 없이 대체 계정만 지정한다.
MERGE `makestar-dw.makestar_ax.entity_master` T
USING (SELECT 'young_k' AS entity_id) S ON T.entity_id = S.entity_id
WHEN NOT MATCHED THEN INSERT (entity_id, entity_type, artist_subtype, name, name_en,
    x_handle, represented_by_handle, confirmation_status, notes, aliases,
    last_verified_date, created_at, updated_at)
  VALUES ('young_k','ARTIST','SOLO','영케이','Young K', NULL, 'day6official',
    'NO_PERSONAL_ACCOUNT','솔로 투어 공지가 DAY6 공식 계정으로 나온다. 본인 계정 from_youngk 는 활동 공지용이 아님',
    ['YOUNG K','영케이','강영현'], CURRENT_DATE('Asia/Seoul'), CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP());
