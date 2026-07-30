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
