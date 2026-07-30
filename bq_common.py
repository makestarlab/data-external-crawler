"""BigQuery 인증 공통 모듈 - x_crawler.py / curate_events.py가 공유한다.

필요한 환경변수(GitHub Secrets):
  - GCP_SERVICE_ACCOUNT_JSON : 서비스 계정 JSON 키 파일 "전체 내용"을 그대로 붙여넣은 시크릿.
                               (권장 방식 - client_email/private_key를 따로 잘라서 넣으면
                                개행/따옴표가 깨지기 쉬워 PEM 파싱 에러가 자주 난다)
  구버전 호환용(비권장, 위 방식이 안 될 때만):
  - BQ_SERVICE_ACCOUNT, BQ_PRIVATE_KEY : client_email / private_key를 따로 넣는 방식
"""
import json
import logging
import os

from google.cloud import bigquery
from google.oauth2 import service_account

log = logging.getLogger("bq_common")

PROJECT_ID = "makestar-dw"
DATASET = "makestar_ax"


def _normalize_private_key(raw):
    """복사/붙여넣기 과정에서 흔히 깨지는 패턴들을 방어적으로 복구한다:
    앞뒤 따옴표, \\r\\n, 실제 \\n 이스케이프 등."""
    key = raw.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in ("'", '"'):
        key = key[1:-1]
    key = key.replace("\\r\\n", "\n").replace("\\n", "\n")
    key = key.replace("\r\n", "\n").replace("\r", "\n")
    return key.strip() + "\n"


def get_bq_client():
    sa_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if sa_json:
        # 권장 경로: 서비스 계정 JSON 키 파일 전체를 하나의 시크릿으로 사용.
        # JSON 파싱기가 내부의 \n 이스케이프를 알아서 실제 개행으로 풀어주므로
        # private_key의 개행이 깨질 여지가 없다.
        info = json.loads(sa_json)
    else:
        # 구버전 호환: client_email/private_key를 따로 받는 경로 (문제 재발 시 대비).
        client_email = os.environ["BQ_SERVICE_ACCOUNT"]
        private_key = _normalize_private_key(os.environ["BQ_PRIVATE_KEY"])
        info = {
            "type": "service_account",
            "project_id": PROJECT_ID,
            "private_key": private_key,
            "client_email": client_email,
            "token_uri": "https://oauth2.googleapis.com/token",
        }

    try:
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/bigquery"]
        )
    except ValueError as e:
        # PEM 파싱 실패 시 어느 시크릿이 문제인지 바로 알 수 있도록, 값 자체는
        # 절대 로그에 남기지 않고 형태(길이/개행 유무)만 남긴다.
        pk = info.get("private_key", "")
        log.error(
            "서비스 계정 인증 정보 파싱 실패 (len=%d, has_newline=%s, starts_ok=%s, ends_ok=%s): %s",
            len(pk), "\n" in pk,
            pk.startswith("-----BEGIN"), pk.rstrip().endswith("-----"),
            e,
        )
        raise
    return bigquery.Client(project=PROJECT_ID, credentials=creds)
