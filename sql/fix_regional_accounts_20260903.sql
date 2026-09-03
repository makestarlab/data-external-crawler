-- [2026-09-03] 아티스트 지역 계정 6개를 PROMOTER 에서 떼어낸다.
--
-- 문제: Terra 첫 프로덕션 실행에서 TWS 일본 공연 공지가 "프로모터 공지인데
--   아티스트 특정 실패" 로 확인 필요에 올라왔다. 출처는 @TWS_PLEDIS_JP.
--   그런데 이건 TWS 본인 계정이다. 남의 공연을 알리는 자리가 아니다.
--
--   원인은 내가 PROMOTER 라는 한 바구니에 성격이 다른 둘을 담은 것이다.
--     (가) 아티스트 지역 계정  - xikers_jp, RIIZE_JPN, plave_jp,
--                              ATEEZofficialjp, NCT_OFFICIAL_JP, TWS_PLEDIS_JP
--     (나) 제3자 프로모터·레이블 - hello82PRESENTS, SMTOWNGLOBAL, YX_LABELS 등
--   curate_tour 는 PROMOTER 에 대해 "본문에서 아티스트를 못 뽑으면 계정 엔티티를
--   쓰지 않는다"는 규칙을 적용한다. (나)에는 맞지만 (가)에는 틀리다.
--   (가)는 계정 주인이 곧 아티스트라 자기참조가 정답이다.
--
-- 처리: (가) 6개를 entity_master 에서 지우고, x_crawl_targets.json 에서
--   본체 아티스트의 entity_id 를 그대로 쓰도록 바꿨다.
--   Ktown4u_com / Ktown4u_main 처럼 한 엔티티가 두 핸들을 갖는 기존 방식과 같다.
--   이렇게 하면 지역 계정 포스팅이 처음부터 본체 아티스트로 귀속된다.
--
-- x_crawl_state 도 지워야 한다. 남겨두면 옛 entity_id(tws_jp 등)로 계속 적재된다.

DELETE FROM `makestar-dw.makestar_ax.entity_master`
WHERE entity_id IN ('xikers_jp', 'riize_jpn', 'plave_jp', 'ateez_jp', 'nct_jp', 'tws_jp');

DELETE FROM `makestar-dw.makestar_ax.x_crawl_state`
WHERE entity_id IN ('xikers_jp', 'riize_jpn', 'plave_jp', 'ateez_jp', 'nct_jp', 'tws_jp');

-- 이미 적재된 포스팅의 귀속도 바로잡는다. 안 하면 이 계정들의 과거 글이
-- 영원히 정체불명 엔티티로 남는다.
UPDATE `makestar-dw.makestar_ax.x_posts_raw`
SET entity_id = CASE LOWER(x_handle)
      WHEN 'xikers_jp'       THEN 'xikers'
      WHEN 'riize_jpn'       THEN 'riize'
      WHEN 'plave_jp'        THEN 'plave'
      WHEN 'ateezofficialjp' THEN 'ateez'
      WHEN 'nct_official_jp' THEN 'nct127'
      WHEN 'tws_pledis_jp'   THEN 'tws'
    END,
    entity_type = 'ARTIST'
WHERE LOWER(x_handle) IN ('xikers_jp','riize_jpn','plave_jp',
                          'ateezofficialjp','nct_official_jp','tws_pledis_jp');

-- 잘못 뽑힌 TWS 건은 다시 추출하도록 지운다. 안티조인이 풀려 다음 실행에서 재시도된다.
DELETE FROM `makestar-dw.makestar_ax.x_tour_announcements`
WHERE LOWER(x_handle) = 'tws_pledis_jp';

-- 확인: PROMOTER 11 / 지역 계정 포스팅이 본체 아티스트로 귀속됐는지
SELECT entity_type, COUNT(*) AS cnt
FROM `makestar-dw.makestar_ax.entity_master`
WHERE x_handle IS NOT NULL GROUP BY 1 ORDER BY cnt DESC;

SELECT x_handle, entity_id, entity_type, COUNT(*) AS posts
FROM `makestar-dw.makestar_ax.x_posts_raw`
WHERE LOWER(x_handle) IN ('xikers_jp','riize_jpn','plave_jp',
                          'ateezofficialjp','nct_official_jp','tws_pledis_jp')
GROUP BY 1,2,3 ORDER BY 1;
