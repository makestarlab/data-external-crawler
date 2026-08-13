# 점검 쿼리 모음

기준을 바꾸기 전에 영향 범위를 재고, 바꾼 뒤에 결과를 확인하는 데 쓴다.
프로젝트 `makestar-dw`, 리전 `asia-northeast3`.

## 목차

- [유실 점검 — 가장 먼저](#유실-점검--가장-먼저)
- [사람 정답 대비 정확도](#사람-정답-대비-정확도)
- [현재 상태 한눈에](#현재-상태-한눈에)
- [고아 그룹 점검](#고아-그룹-점검)
- [판정 대상 뽑아보기](#판정-대상-뽑아보기)
- [판매처 표기 흩어짐](#판매처-표기-흩어짐)
- [타이틀 매칭 실패 진단](#타이틀-매칭-실패-진단)
- [판매처별 전환율](#판매처별-전환율)

## 유실 점검 — 가장 먼저

**처리 완료로 표시됐는데 결과 행이 없는 게시물.** 0이 아니면 데이터가 조용히 사라지고 있다.
2026-08-12에 이 값이 2,376이었다.

```sql
SELECT COUNT(*) AS lost, COUNT(DISTINCT x_handle) AS handles
FROM `makestar-dw.makestar_ax.x_posts_raw` r
WHERE r.is_curated
  AND NOT EXISTS (
    SELECT 1 FROM `makestar-dw.makestar_ax.x_event_announcements` a
    WHERE a.tweet_id = r.tweet_id
  );
```

계정별로 보면 원인이 드러난다. **손실 건수가 배치 크기의 배수면 응답 잘림**,
계정 단위로 통째면 그 계정 처리 중 예외다.

```sql
SELECT r.x_handle, COUNT(*) AS curated, COUNTIF(a.tweet_id IS NULL) AS lost
FROM `makestar-dw.makestar_ax.x_posts_raw` r
LEFT JOIN (SELECT DISTINCT tweet_id FROM `makestar-dw.makestar_ax.x_event_announcements`) a
  USING (tweet_id)
WHERE r.is_curated
GROUP BY r.x_handle HAVING lost > 0 ORDER BY lost DESC;
```

미처리로 남은 건도 함께 본다. 재시도 대상이라 정상이지만, 같은 계정이 계속 남으면 버그다.

```sql
SELECT x_handle, COUNT(*) AS pending
FROM `makestar-dw.makestar_ax.x_posts_raw`
WHERE NOT is_curated OR is_curated IS NULL
GROUP BY x_handle ORDER BY pending DESC;
```

## 사람 정답 대비 정확도

**두 지표를 섞지 말 것.** 전후를 비교할 때 반드시 같은 자로 잰다.

- **전체 일치율** = 통과·제외를 통틀어 사람과 판정이 같은 비율
- **정밀도** = 통과시킨 것 중 진짜인 비율. 대시보드 품질에 직결된다

```sql
WITH lab AS (
  SELECT tweet_id, is_relevant AS y
  FROM `makestar-dw.makestar_ax.x_curation_labels_latest`
  WHERE verdict <> 'HOLD'
)
SELECT
  COUNT(*)                                   AS labeled,
  COUNTIF(a.tweet_id IS NOT NULL)             AS matched,
  ROUND(SAFE_DIVIDE(COUNTIF(a.is_relevant = l.y),
        COUNTIF(a.tweet_id IS NOT NULL)) * 100, 1)              AS accuracy,
  ROUND(SAFE_DIVIDE(COUNTIF(a.is_relevant AND l.y),
        COUNTIF(a.is_relevant)) * 100, 1)                        AS precision_pct,
  COUNTIF(a.is_relevant AND NOT l.y)          AS fp,
  COUNTIF(a.tweet_id IS NOT NULL AND NOT a.is_relevant AND l.y) AS fn
FROM lab l
LEFT JOIN `makestar-dw.makestar_ax.x_event_announcements` a USING (tweet_id);
```

`matched`가 `labeled`보다 작으면 그만큼 유실된 것이다. 그 상태의 정확도는 신뢰하지 말 것.

층별로 보면 어디가 약한지 나온다.

```sql
SELECT l.stratum, COUNT(*) AS n,
       ROUND(SAFE_DIVIDE(COUNTIF(a.is_relevant = l.is_relevant), COUNT(*))*100,1) AS acc,
       COUNTIF(a.is_relevant AND NOT l.is_relevant) AS fp,
       COUNTIF(NOT a.is_relevant AND l.is_relevant) AS fn
FROM `makestar-dw.makestar_ax.x_curation_labels_latest` l
JOIN `makestar-dw.makestar_ax.x_event_announcements` a USING (tweet_id)
WHERE l.verdict <> 'HOLD'
GROUP BY ROLLUP(l.stratum) ORDER BY l.stratum;
```

틀린 건을 원문과 함께 보려면.

```sql
SELECT l.stratum, a.x_handle, l.verdict AS 사람, a.is_relevant AS 모델,
       l.reason AS 사람이유, a.extraction_note AS 모델근거,
       REPLACE(SUBSTR(a.tweet_text,1,90),'\n',' ') AS txt
FROM `makestar-dw.makestar_ax.x_curation_labels_latest` l
JOIN `makestar-dw.makestar_ax.x_event_announcements` a USING (tweet_id)
WHERE l.verdict <> 'HOLD' AND a.is_relevant <> l.is_relevant
ORDER BY l.stratum;
```

## 현재 상태 한눈에

```sql
SELECT
  COUNTIF(is_relevant AND is_representative) AS 대표이벤트,
  COUNTIF(is_relevant AND is_representative AND entity_type='SELLER') AS 판매처계정발,
  COUNTIF(is_relevant AND is_representative
          AND (seller_name IS NULL OR TRIM(seller_name)='')) AS 판매처미상,
  COUNTIF(artist_entity_id IS NULL AND is_relevant AND is_representative) AS 아티스트미매핑
FROM `makestar-dw.makestar_ax.x_event_announcements`;
```

## 고아 그룹 점검

대표 게시물이 필터에 걸려 죽으면 같은 그룹의 형제가 통째로 안 보인다.
필터를 바꿀 때마다 확인하고, 0이 아니면 아래 UPDATE로 대표를 승격한다.

```sql
WITH rep AS (
  SELECT DISTINCT event_group_id
  FROM `makestar-dw.makestar_ax.x_event_announcements`
  WHERE is_relevant AND is_representative AND event_group_id IS NOT NULL
)
SELECT COUNT(*) AS orphan_rows, COUNT(DISTINCT event_group_id) AS orphan_groups
FROM `makestar-dw.makestar_ax.x_event_announcements` a
WHERE a.is_relevant AND NOT a.is_representative AND a.event_group_id IS NOT NULL
  AND a.event_group_id NOT IN (SELECT event_group_id FROM rep);
```

승격은 판매처가 붙은 행 우선, 그 다음 이른 날짜순.

```sql
UPDATE `makestar-dw.makestar_ax.x_event_announcements`
SET is_representative = TRUE
WHERE tweet_id IN (
  WITH rep AS (
    SELECT DISTINCT event_group_id FROM `makestar-dw.makestar_ax.x_event_announcements`
    WHERE is_relevant AND is_representative AND event_group_id IS NOT NULL
  ),
  o AS (
    SELECT tweet_id, ROW_NUMBER() OVER (
      PARTITION BY event_group_id
      ORDER BY CASE WHEN seller_name IS NULL OR TRIM(seller_name)='' THEN 1 ELSE 0 END,
               run_date, tweet_created_at, tweet_id) AS rn
    FROM `makestar-dw.makestar_ax.x_event_announcements`
    WHERE is_relevant AND NOT is_representative AND event_group_id IS NOT NULL
      AND event_group_id NOT IN (SELECT event_group_id FROM rep)
  )
  SELECT tweet_id FROM o WHERE rn = 1
);
```

## 판정 대상 뽑아보기

```sql
SELECT x_handle, seller_name, IFNULL(artist_name,'—') AS artist,
       IFNULL(event_name, IFNULL(album_or_title,'—')) AS ev,
       run_date, confidence, extraction_note,
       REPLACE(SUBSTR(tweet_text,1,120),'\n',' ') AS txt
FROM `makestar-dw.makestar_ax.x_event_announcements`
WHERE is_relevant AND is_representative
  AND seller_entity_id = 'hybemerch'   -- 조건은 상황에 맞게
ORDER BY run_date;
```

`extraction_note`를 반드시 함께 본다. 모델이 왜 그렇게 판단했는지가 거기 있다.

## 판매처 표기 흩어짐

같은 판매처가 몇 가지 이름으로 쪼개져 있는지. 대시보드 정규화 규칙을 보강할 후보가 나온다.

```sql
SELECT em.name AS canonical, COUNT(*) AS n,
       STRING_AGG(DISTINCT TRIM(a.seller_name), ' | ' ORDER BY TRIM(a.seller_name)) AS 표기들
FROM `makestar-dw.makestar_ax.x_event_announcements` a
JOIN `makestar-dw.makestar_ax.entity_master` em
  ON em.entity_id = a.seller_entity_id AND em.entity_type='SELLER'
WHERE a.is_relevant AND a.is_representative AND a.entity_type='SELLER'
GROUP BY canonical
HAVING COUNT(DISTINCT TRIM(a.seller_name)) > 1
ORDER BY n DESC;
```

여기서 **명백히 다른 판매처가 한 canonical에 묶여 있으면** 표기 문제가 아니라
`resolve_entity_id`의 별칭 매칭이 느슨한 것이다. 정규화로 덮지 말고 매칭을 고친다.

## 타이틀 매칭 실패 진단

LLM이 뽑은 `album_or_title`이 대시보드 화이트리스트(`lib/artist-seller-data.js`)에
없어서 못 붙는 경우가 매칭 실패의 절반 이상이다. **프롬프트 문제가 아니라
화이트리스트 노후화 문제이므로 프롬프트를 고치지 말 것.**

```sql
SELECT artist_entity_id, TRIM(album_or_title) AS title, COUNT(*) AS n
FROM `makestar-dw.makestar_ax.x_event_announcements`
WHERE is_relevant AND is_representative
  AND album_or_title IS NOT NULL AND TRIM(album_or_title) <> ''
GROUP BY artist_entity_id, title
ORDER BY n DESC;
```

MusicBrainz 정식 표기 확인은 **제목으로** 찾는다 — `artist_display_name`은 99.9%가 NULL이다.

```sql
SELECT title, primary_type, is_core_title, first_release_date, rg_mbid
FROM `makestar-dw.external.musicbrainz_dim_title_full`
WHERE UPPER(TRIM(title)) = 'WILD'
ORDER BY first_release_date DESC;
```

## 판매처별 전환율

수집량 대비 유의미 이벤트 비율. 낮으면 그 계정이 판매 공지를 잘 안 올린다는 뜻이라
타깃 유지 여부 판단에 쓴다.

```sql
SELECT s.x_handle, em.name AS seller,
       IFNULL(p.posts,0) AS 수집,
       IFNULL(e.rel_rep,0) AS 유의미,
       ROUND(SAFE_DIVIDE(e.rel_rep, p.posts)*100,1) AS 전환율
FROM `makestar-dw.makestar_ax.x_crawl_state` s
LEFT JOIN `makestar-dw.makestar_ax.entity_master` em
  ON em.entity_id = s.entity_id AND em.entity_type='SELLER'
LEFT JOIN (SELECT x_handle, COUNT(*) AS posts
           FROM `makestar-dw.makestar_ax.x_posts_raw`
           WHERE entity_type='SELLER' GROUP BY x_handle) p ON p.x_handle = s.x_handle
LEFT JOIN (SELECT x_handle, COUNTIF(is_relevant AND is_representative) AS rel_rep
           FROM `makestar-dw.makestar_ax.x_event_announcements`
           WHERE entity_type='SELLER' GROUP BY x_handle) e ON e.x_handle = s.x_handle
WHERE s.entity_type='SELLER'
ORDER BY 수집 DESC;
```
