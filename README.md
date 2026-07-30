# data-external-crawler

K-pop 아티스트/셀러 X(Twitter) 계정 일일 포스팅 수집 크롤러. GitHub Actions에서 매일 1회 실행되며,
결과를 BigQuery `makestar-dw.makestar_ax.x_posts_raw`에 필터링 없이 적재한다 (ELT 중 L 단계).
큐레이션(유의미한 포스팅 선별)은 별도의 후속 배치가 담당하며 아직 이 저장소에는 포함되어 있지 않다.

## 아키텍처

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

## 필요한 GitHub Secrets

| Secret            | 설명                                              |
|--------------------|---------------------------------------------------|
| `X_BEARER_TOKEN`   | X API v2 Bearer Token                              |
| `GCP_SERVICE_ACCOUNT_JSON` | BigQuery 서비스 계정 **JSON 키 파일 전체 내용**을 그대로 붙여넣은 값 |

`GCP_SERVICE_ACCOUNT_JSON`은 `client_email`/`private_key`를 따로 잘라서 넣지 말고,
GCP 콘솔에서 다운로드한 키 파일(`*.json`)을 **열어서 전체를 그대로 복사해 붙여넣는다.**
따로 잘라 넣으면 `private_key`의 개행이 깨져서 `ValueError: Unable to load PEM file
... MalformedFraming` 에러가 나기 쉽다 (실제로 한 번 겪었던 문제).

GitHub CLI가 있다면 이렇게 파일에서 바로 등록하는 것이 가장 안전하다 (복사/붙여넣기 생략):
```bash
gh secret set GCP_SERVICE_ACCOUNT_JSON --repo makestarlab/data-external-crawler < /path/to/key.json
```

서비스 계정은 `makestar-dw.makestar_ax` 데이터셋에 대해 최소 다음 권한이 필요하다:
- `x_posts_raw`, `x_crawl_state`: 조회 + 데이터 수정 (BigQuery Data Editor 수준)
- 프로젝트 레벨: 쿼리/로드 잡 실행 권한 (BigQuery Job User)

구버전 호환용으로 `BQ_SERVICE_ACCOUNT`(client_email) + `BQ_PRIVATE_KEY`(PEM) 두 시크릿을
따로 넣는 경로도 코드에 남아있지만, 위 문제 때문에 권장하지 않는다.

## 스케줄

매일 23:00 UTC (08:00 KST) 실행. `workflow_dispatch`로 수동 실행도 가능.

## 알려진 제약 / 후속 과제

- `x_crawl_targets.json`은 entity_master의 스냅샷이라 계정이 추가/제외되면 수동으로
  다시 내보내 커밋해야 한다. entity_master를 직접 쿼리하도록 바꾸는 것이 다음 개선 후보.
- 큐레이션(raw → 의미있는 이벤트/공지 선별) 단계는 아직 미구현.
