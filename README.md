# data-external-crawler

K-pop 아티스트/셀러 X(Twitter) 계정 일일 포스팅 수집 크롤러 + 큐레이션 파이프라인.
GitHub Actions에서 매일 1회 실행되며:

1. **`x_crawler.py`**: X API에서 신규 포스팅을 필터링 없이 수집해 BigQuery
   `makestar-dw.makestar_ax.x_posts_raw`에 적재한다 (ELT 중 L 단계).
2. **`curate_events.py`**: `x_posts_raw`의 미처리 포스팅을 Claude API로 읽어 아티스트명/
   앨범명/판매처/이벤트명을 추출하고, `x_event_announcements`에 적재한다 (ELT 중 T 단계).

## 아키텍처

### 1단계 - 크롤링 (`x_crawler.py`)

- **상태 저장소(단일 소스)**: BigQuery `makestar_ax.x_crawl_state`
  - 계정별 `last_tweet_id`(since_id 워터마크)를 여기서 읽고 실행 후 갱신한다.
  - 로컬 JSON 상태 파일은 사용하지 않는다. GitHub Actions 러너는 매번 새로 뜨는 휘발성
    환경이므로, 상태를 코드/러너가 아닌 BigQuery에 둬야 실행 간 일관성이 보장된다.
- **수집 대상**: `x_crawl_targets.json` (entity_master에서 내보낸 스냅샷, 수동 갱신 필요 —
  아래 "알려진 제약" 참고)
- **적재**: `google-cloud-bigquery`의 `load_table_from_json` (배치 로드 잡, 무료)을 사용한다.
  과거에는 SQL 텍스트를 직접 조립해 INSERT했으나, 이스케이핑 문제가 반복적으로 발생해
  라이브러리 기반 적재로 전환했다.
- **상태 갱신**: 파라미터 바인딩된 MERGE 쿼리 (`ArrayQueryParameter` + `StructQueryParameter`)를
  사용한다. 마찬가지로 SQL 텍스트 조립을 피하기 위함이다.
- **팔로워 일별 스냅샷**: `makestar_ax.x_follower_daily`에 계정별 `public_metrics`를
  `(run_date, x_handle)` 키로 MERGE 업서트한다 (2026-08-19 추가, 아래 참고).

#### 팔로워 추세 테이블 `x_follower_daily`

`x_crawl_state.x_follower_count`는 매 실행 덮어쓰기라 **현재값 스냅샷**만 남는다. 일별 추세를
보려면 날짜별 행이 쌓여야 해서 파티션 테이블을 따로 뒀다. `/2/users/by`는 어차피 매 실행마다
전체 핸들에 대해 호출하므로 **이 테이블 때문에 X API 과금이 늘지는 않는다.**

- 파티션: `run_date`(KST) / 클러스터: `entity_type`, `x_handle`
- 컬럼: `follower_count`, `following_count`, `post_count`, `listed_count`, `like_count`,
  `media_count`, `is_verified` — `like_count`/`media_count`는 X가 안 내려줄 때가 많아 대부분 NULL
- `post_count`는 X가 필드명을 `tweet_count` → `post_count`로 바꾼 이력이 있어 코드에서 둘 다 받는다
- **INSERT가 아니라 MERGE인 이유**: 같은 날 워크플로를 수동 재실행해도 하루 1행만 유지하기 위함.
  재실행하면 그날 행이 더 나중 값으로 갱신된다.
- 스냅샷 행은 **user lookup 성공 직후**에 담는다. 뒤이어 트윗 수집이 실패(`ERROR`)해도 그날
  팔로워 수는 남아서 추세에 구멍이 안 생긴다.
- 전일 대비 증감은 뷰 `x_follower_daily_delta`를 쓴다. 크롤러가 하루 걸러 실패하면
  `days_since_prev`가 2 이상이 되므로, 증감을 해석할 때 **반드시 이 컬럼을 같이 볼 것**
  (2일치 증가분을 하루 증가분으로 오해하기 쉽다).

```sql
-- 최근 2주, 판매처 팔로워 증감 상위
SELECT run_date, x_handle, follower_count, follower_delta, days_since_prev
FROM `makestar-dw.makestar_ax.x_follower_daily_delta`
WHERE entity_type = 'SELLER' AND run_date >= CURRENT_DATE('Asia/Seoul') - 14
ORDER BY run_date DESC, follower_delta DESC
```

### 2단계 - 큐레이션 (`curate_events.py`)

- **입력**: `x_posts_raw` 중 `is_curated IS NOT TRUE`인 행 (계정별로 그룹핑해서 처리).
- **추출**: Claude API(`extract_event_announcements` tool 강제 호출)로 계정당 한 번씩 호출해서
  `artist_name` / `album_or_title` / `seller_name` / `event_name` / `is_relevant`(판매·이벤트
  공지가 맞는지) / `event_key`(그룹핑용 정규화 키)를 추출한다.
  - ARTIST 계정(예: `ATEEZofficial`)은 artist_name을 entity_master 값으로 고정해서 프롬프트에
    넘긴다.
  - SELLER 계정(예: `weverseshop`, `the_FANSSHOP`)은 여러 아티스트의 상품을 올리므로, 본문에서
    실제 아티스트를 읽어내야 한다. entity_master의 전체 아티스트 명단을 참고 목록으로 프롬프트에
    같이 넣어 정규화를 돕는다.
- **동일 이벤트 반복 게시 그룹핑**: 계정별로 최근 `RECENT_WINDOW_DAYS`(기본 21일) 이내의
  대표 이벤트(`is_representative=TRUE`) 목록을 프롬프트에 함께 제공해서, 같은 이벤트가 이어지면
  LLM이 기존 `event_key`를 그대로 재사용하도록 유도한다. 같은 배치 안에서 신규로 처음 등장하는
  `event_key`는 새 `event_group_id`를 만들고 그 중 가장 이른 게시물만 `is_representative=TRUE`로
  표시한다.
  - 분석 시에는 원본 테이블을 직접 필터링(`WHERE is_relevant AND is_representative`)하거나,
    이 조건이 이미 적용된 뷰 `x_event_announcements_curated`를 사용하면 된다.
  - `is_relevant=false`인 행(잡담/일상 트윗 등)도 감사·프롬프트 튜닝 목적으로 테이블에는 남긴다.
- **재시도 안전성**: 계정 하나의 Claude 호출이나 적재가 실패해도 해당 계정의 raw 행은
  `is_curated=FALSE`로 남아 다음 실행에서 자동으로 재처리된다.
- **실패 가시화 (2026-08-24 추가)**: 위 "재시도 안전성"에는 함정이 있다. 실패를 전부 삼키기
  때문에 **Actions는 초록불인데 데이터는 안 들어오는 상태**가 될 수 있다. 실제로 8/22~8/24에
  미처리율이 0% → 1.9% → 6.1% → 29.8%로 올라가는 동안 아무 신호가 없었다. 그래서 세 가지를 넣었다.
  1. API 오류 로그에 **서버가 준 실제 메시지**를 남긴다 (`상세:` 줄). 예전엔 예외 클래스명만
     찍혀서 `BadRequestError`가 크레딧 부족인지 요청 오류인지 구분할 수 없었다.
  2. **4xx는 재시도하지 않는다** (429 제외). 400을 5번씩 다시 치느라 배치당 45초를 버렸다.
  3. 실행 끝에 미처리 계정 목록을 모아 찍고, 미처리율이 `MISS_RATE_ALERT`(기본 10%)를 넘으면
     **워크플로를 실패 처리**한다. 미처리 행은 다음 실행에서 재시도되므로 데이터 유실은 아니고,
     사람이 봐야 한다는 신호다. 시끄러우면 상수만 올리면 된다.
- **응답 형식 복구 (`_coerce_results`, 2026-08-24 추가)**: 모델이 `results`를 규격대로 안 줄 때
  버리기 전에 한 번 파싱해본다. 지금까지 본 변형은 두 가지다.
  - 배열 안에 딕셔너리 대신 **문자열이 섞여** 옴 (2026-08-13)
  - `results` 자체가 **배열을 JSON 직렬화한 문자열**로 옴 (2026-08-24)

  둘 다 HTTP 200이고 내용도 멀쩡한데 형식만 어긋난 경우다. 예전엔 두 번째를 배치째로 버렸고,
  **재시도해도 모델이 매번 같은 형태로 답해서 영원히 안 풀렸다** (NCTsmtown_127 10건이
  8/23부터 매일 유실). 파싱해도 안 되면(응답이 잘렸을 때 등) 그때 포기하되, 받은 값 앞부분을
  로그에 남긴다.

## 필요한 GitHub Secrets

| Secret            | 설명                                              |
|--------------------|---------------------------------------------------|
| `X_BEARER_TOKEN`   | X API v2 Bearer Token                              |
| `GCP_SERVICE_ACCOUNT_JSON` | BigQuery 서비스 계정 **JSON 키 파일 전체 내용**을 그대로 붙여넣은 값 |
| `ANTHROPIC_API_KEY` | 큐레이션 단계(Claude API)용 키 |

`GCP_SERVICE_ACCOUNT_JSON`은 `client_email`/`private_key`를 따로 잘라서 넣지 말고,
GCP 콘솔에서 다운로드한 키 파일(`*.json`)을 **열어서 전체를 그대로 복사해 붙여넣는다.**
따로 잘라 넣으면 `private_key`의 개행이 깨져서 `ValueError: Unable to load PEM file
... MalformedFraming` 에러가 나기 쉽다 (실제로 한 번 겪었던 문제).

GitHub CLI가 있다면 이렇게 파일에서 바로 등록하는 것이 가장 안전하다 (복사/붙여넣기 생략):
```bash
gh secret set GCP_SERVICE_ACCOUNT_JSON --repo makestarlab/data-external-crawler < /path/to/key.json
gh secret set ANTHROPIC_API_KEY --repo makestarlab/data-external-crawler
```

서비스 계정은 `makestar-dw.makestar_ax` 데이터셋에 대해 최소 다음 권한이 필요하다:
- `x_posts_raw`, `x_crawl_state`, `x_event_announcements`, `entity_master`: 조회 + 데이터 수정
  (BigQuery Data Editor 수준 - `entity_master`는 조회만 하면 되지만 편의상 같은 role로 충분)
- 프로젝트 레벨: 쿼리/로드 잡 실행 권한 (BigQuery Job User)

구버전 호환용으로 `BQ_SERVICE_ACCOUNT`(client_email) + `BQ_PRIVATE_KEY`(PEM) 두 시크릿을
따로 넣는 경로도 코드에 남아있지만, 위 문제 때문에 권장하지 않는다.

`curate_events.py`의 모델은 기본값 `claude-sonnet-5`(Claude Sonnet 5)이다.

**모델 선택 경위 (2026-07-30)**: ax팀 피드백대로 처음엔 Haiku 4.5로 시작했다가, 실제
백로그(raw 396건)를 Haiku 4.5와 Sonnet 5 양쪽으로 큐레이션해서 `extraction_model` 컬럼
기준으로 비교했다.
- 관련성 판단 정확도: Haiku가 confidence 1.0으로 자신 있게 "관련 없음" 처리한 사례 중
  실제로는 프리오더/재입고 공지인 게 여러 건 발견됨 (Sonnet은 정확히 잡아냄). 반대로
  Sonnet이 놓친 건 대부분 애매한 회고성 콘텐츠라 명백한 오답은 아니었음.
- 아티스트/판매처 엔티티 인식률: 두 모델 다 비슷한 수준 (미해결 건은 대부분 `entity_master`
  로스터 커버리지 부족 때문이지 모델 차이가 아니었음).
- 비용: Sonnet 5가 Haiku 4.5보다 2~3배 비싸지만, 이 파이프라인 물량(하루 30~50콜, 짧은
  프롬프트)에서는 월 몇 달러 수준 차이라 무시 가능.

위 근거로 기본값을 Sonnet 5로 승격했다. 다시 Haiku로 비교하고 싶으면 코드 수정 없이
GitHub Actions의 **Actions 탭 > X Crawler Daily > Run workflow**에서 `curation_model`
입력값에 `claude-haiku-4-5-20251001`을 넣고 수동 실행하면 된다(비워두면 기본값 Sonnet 5 사용).

## 스케줄

매일 21:00 UTC (06:00 KST) 실행. `workflow_dispatch`로 수동 실행도 가능.

## 과거 데이터 백필 (일회성, 2026-07-30 추가)

정규 일일 크롤링(`X Crawler Daily`)은 2026-07-29부터 시작됐다. 그 이전 기간의 경쟁사
이벤트 공지를 소급 수집하기 위해 `x_crawler_backfill.py` + `X Crawler Backfill (one-off)`
워크플로를 추가했다.

- **스케줄 없음** - Actions 탭 > **X Crawler Backfill (one-off)** > **Run workflow**에서
  수동으로만 실행한다.
- 입력값: `start_date`(기본 2026-07-01), `max_pages`(계정당 최대 페이지 수, 기본 20 =
  최대 2000건), `curation_model`(기본 Sonnet 5).
- 정규 크롤러와의 차이:
  - `since_id` 워터마크를 무시하고 항상 `start_date`부터 조회하며, **`x_crawl_state`는
    갱신하지 않는다** (정규 크롤러의 워터마크를 과거로 되돌리지 않기 위함).
  - `x_posts_raw`에 이미 있는 tweet_id는 걸러내고 신규 행만 적재해서, 정규 크롤링 기간과
    겹쳐도 중복 적재되지 않는다.
  - 백필이 끝나면 같은 워크플로 안에서 바로 `curate_events.py`까지 실행해서 큐레이션까지
    한 번에 끝낸다.
- **제약**: X API `GET /2/users/:id/tweets`는 계정당 최근 3200건까지만 반환하므로, 한 달치
  백필에는 20페이지(2000건)면 충분하지만 트윗이 매우 많은 판매처 계정은 이론상 그 이전
  데이터를 API로 아예 가져올 수 없을 수 있다.
- **알려진 이슈 (2026-07-30 첫 백필 실행)**: 72개 계정 x 한 달치 백로그를 큐레이션하는 데
  Claude 호출이 많아 원래 타임아웃(60분)을 넘겨 `Error: The operation was canceled.`로
  워크플로가 중간에 잘렸다. 처리 못한 계정의 raw 행은 `is_curated`가 `NULL`로 남아있어
  데이터 유실은 없고, **`X Crawler Daily`(또는 백필 워크플로)를 다시 실행하면 큐레이션이
  남은 부분부터 자동으로 이어서 처리된다** (`curate_events.py`는 매 실행마다 `is_curated
  IS NOT TRUE`인 행 전체를 다시 훑으므로 계정 단위로 재시작 가능). 재발 방지로 두 워크플로의
  `timeout-minutes`를 90/180분으로 늘려뒀다.

## 알려진 제약 / 후속 과제

- `x_crawl_targets.json`은 entity_master의 스냅샷이라 계정이 추가/제외되면 수동으로
  다시 내보내 커밋해야 한다. entity_master를 직접 쿼리하도록 바꾸는 것이 다음 개선 후보.
- 큐레이션의 이벤트 그룹핑은 "같은 계정, 최근 21일" 범위 내에서만 기존 키 재사용을 시도한다.
  다른 계정(예: 아티스트 본인 계정과 판매처 계정)이 같은 이벤트를 각자 공지하는 경우는 현재
  버전에서는 서로 다른 그룹으로 잡힌다 (계정 간 교차 그룹핑은 후속 과제).
- LLM 추출 결과의 품질(특히 앨범명/이벤트명 정확도)은 실제 데이터로 검증이 더 필요하다.
  `extraction_note`/`confidence` 필드로 감사하면서 프롬프트를 다듬어갈 것.
- `entity_master` 로스터가 아직 작아서(81건 -> 이번에 신규 추가분 포함해도 여전히 부족),
  SELLER 계정이 언급하는 아티스트 중 상당수가 `artist_entity_id`로 못 이어진다. 실제로
  Haiku/Sonnet 비교 테스트에서 관련 있음으로 판단된 SELLER 공지의 약 2/3이 이 케이스였다
  (ONF, N.Flying, WONHO, Young K 등 다수). 모델을 바꿔도 해결 안 되는, 순수 데이터
  커버리지 문제라 로스터를 넓히는 게 우선순위 높은 후속 작업.
- `RVsmtown`(Red Velvet) 계정이 그룹이 아니라 멤버 개인(`redvelvet_irene`)으로만 매핑돼
  있던 걸 2026-07-30에 그룹 엔티티(`redvelvet`)를 신규 추가해서 바로잡았다. 비슷하게
  "그룹 공식 계정인데 멤버 개인 엔티티만 등록돼 있는" 케이스가 다른 아티스트에도 있을 수
  있으니 점검이 필요하다.

---

## 3단계 - 투어 공지 큐레이션 (`curate_tour.py`)

아티스트 공식 X 계정 포스팅에서 **글로벌 투어·공연 일정**을 뽑아
`makestar_ax.x_tour_announcements` 에 적재한다. 미주유럽사업팀의
`글로벌 투어 현황` 시트(공연 일자·공연명·IP·국가·도시·베뉴명·판매 링크)에 대응한다.

### 왜 만들었나 - 이미 수집해 놓고 안 읽고 있었다

2026-08-24, 2026-08-25 에 Stray Kids 공식 계정이 올린 공지다. 둘 다 `x_posts_raw` 에
그날 바로 적재됐다.

```
World Tour <RUN IT BANGKOK>   / 2027.01.16 (SAT) - 01.17 (SUN) @ Impact Arena
World Tour <RUN IT SINGAPORE> / 2027.03.06 (SAT) - 03.07 (SUN) @ Singapore Indoor Stadium
```

같은 시점 수기 시트의 Stray Kids 최종 작성일은 2026-07-20, 최종 공연일은 2026-12-12 이었다.
**네 회차 전부 시트에 없었다.** 게다가 태국·싱가포르는 Ticketmaster Discovery API 가 커버하지
않는 구간이라, 이 경로가 아니면 사람이 인스타그램에서 우연히 보는 수밖에 없다.

이미 크롤링 중인 계정이므로 **X API 추가 과금은 0원**이다.

### 판정 기준

`prompts/tour_extraction_rules.md` 가 단일 출처다. 프롬프트 본문이자 사람이 읽는 문서다.
`prompts/classification_rules.md`(음반 구매 연계 이벤트 판정)와는 **완전히 별개**다.

### 프리필터

아티스트 계정 포스팅 전량을 Claude 에 보내면 대부분이 일상 트윗이라 낭비다.
2026-07-01~2026-09-01 실측(아티스트 48계정, 리트윗 제외 10,963건):

| 필터 | 통과 | 비율 | 하루 |
|---|---|---|---|
| 키워드 또는 예매처 도메인 | 1,730 | 15.8% | 27건 |
| 위 + 날짜 표기까지 요구 | 415 | 3.8% | 7건 |

**앞쪽을 쓴다.** 뒤쪽은 `Tickets Open Now!` 처럼 날짜 없이 예매 오픈만 알리는 공지를 통째로
놓친다 (Stray Kids RUN IT SINGAPORE 티켓 오픈 공지가 정확히 그 형태). 정규식은 재현율만
담당하고 정밀도는 Claude 의 `is_relevant` 가 맡는다.

### `is_curated` 를 쓰지 않는 이유

`x_posts_raw.is_curated` 는 `curate_events.py` 의 진행 상태다. 두 배치가 한 플래그를
공유하면 먼저 도는 쪽이 상대의 미처리분을 먹어치운다. 대신 `x_tour_announcements` 에
이미 적재된 `tweet_id` 를 안티조인해서 멱등성을 잡는다. 같은 날 두 번 돌려도 중복 호출이 없다.

### 출력 테이블과 뷰

| 이름 | 용도 |
|---|---|
| `x_tour_announcements` | 공지 1건 = 1행. `shows` 배열에 회차가 들어간다 |
| `v_tour_shows` | 회차 단위로 펼친 뷰. 시트 한 행 = 이 뷰 한 행 |
| `v_tour_shows_latest` | 같은 공연의 반복 공지를 `show_key` 로 접은 뷰 |
| `v_tour_review_queue` | 확인 필요 큐. 대시보드 '입력/검토' 탭이 읽는다 |

한 공지가 여러 회차를 담는 경우가 흔해서 `shows` 를 배열로 뒀다. 날짜 범위
(`2027.03.06 - 03.07`)는 날짜마다 한 원소로 펼친다.

`ticket_opens.opens_at_text` 는 **문자열로 남긴다.** 선예매 시각이 지역별 현지시각이라
TIMESTAMP 로 강제 변환하면 반드시 어긋난다. 원문의 기준(`Local Time` / `KST`)은
`timezone_note` 에 따로 적는다.

### 확인 필요(`needs_review`) 판정

다음 중 하나라도 걸리면 사람에게 보낸다.

- `confidence < 0.75`
- 일정 공지(`NEW_TOUR`/`NEW_CITY`/`SHOW_INFO`)인데 확정 날짜가 하나도 없음
- `SHOW_INFO` 인데 공연장 결측
- 도시 결측
- **`entity_master` 매칭 실패** - 로스터에 없는 아티스트다. 이게 조용한 NULL 로 빠지면
  커버리지가 떨어진 걸 아무도 모른다. 반드시 눈에 보이게 한다
- 모델이 준 날짜가 파싱 실패

### 비용

| 항목 | 산출 | 월 |
|---|---|---|
| Claude 추출 (Sonnet 5) | 27건/일 × 30 = 810건, 25건씩 배치 = 33콜. 콜당 input 3.7K / output 2.5K | **$1.1** |
| 신규 계정 7개 X API | 7계정 × 4건/일 × 30 × $0.005 | **$4.2** |
| BigQuery / GitHub Actions | 배치 로드 잡, 무료 한도 내 | $0 |
| **합계** | | **$5.3 (약 7,300원)** |

### 최초 실행 순서

```bash
# 1. 테이블과 뷰 생성 (1회)
bq query --use_legacy_sql=false < sql/x_tour_announcements.sql

# 2. 과거분 백필 - Actions 탭 > Tour Curation Daily > Run workflow > lookback_days=90
#    또는 로컬에서
TOUR_LOOKBACK_DAYS=90 python curate_tour.py

# 3. 이후 매일 06:40 KST 자동 실행
```

### 회귀 테스트

```bash
python test_curate_tour.py
```

Claude API / BigQuery 없이 순수 함수만 검증한다. 입력은 실제 적재된 공지 원문이다.
스키마를 바꿀 때 이 테스트가 깨지는지 먼저 확인할 것.

### 알려진 한계

- `detect_vendor` 는 URL 과 영문 표기만 본다. "NOL 티켓에서 예매" 같은 한글 표기는 못 잡는다
- 온라인 전용 스트리밍 시청권(Beyond LIVE, Mnet Plus)은 오프라인 공연 정보가 같이 없으면
  `is_relevant=false` 로 뺀다. 미주유럽사업팀이 보는 건 실물 공연이라서다
- 연도가 생략된 날짜(`12.05`)는 트윗 작성일 기준 가장 가까운 미래로 추론한다.
  연말·연초 공지에서 틀릴 수 있어 `date_text` 에 원문을 남겨 뒀다
