#!/usr/bin/env python3
"""로스터 아티스트의 X·인스타그램·공식사이트 링크를 Wikidata 에서 모아 적재한다.

미주유럽사업팀이 손으로 만들던 세 리스트를 자동화한다.
  X(트위터) 계정        Wikidata P2002
  인스타그램 계정        Wikidata P2003
  공식 사이트           Wikidata P856

왜 Wikidata 인가
----------------
[2026-09-08] 먼저 우리가 이미 가진 데이터에서 뽑아보려 했다. 아티스트 트윗의
링크 28,165개 중 instagram.com 링크가 1,149개 있었지만, 대부분이 개별 게시물
(/p/, /reel/) 이라 계정 핸들이 나온 아티스트는 88명 중 3명뿐이었다.
X 데이터로는 안 되는 길이다.

Wikidata 는 사람 손으로 검증된 식별자를 세 종류 다 갖고 있고, 무료에 호출 제한도
사실상 없다. 우리 로스터의 X 핸들(P2002)을 키로 역조회하면 같은 인물의
인스타그램(P2003)과 공식사이트(P856)가 한 번에 딸려 온다.

네트워크 주의
-------------
클라우드 컨테이너와 데스크톱 VM 에서는 query.wikidata.org 가 막혀 있다(403).
GitHub Actions 에서 돌려야 한다. 크롤러가 이미 거기서 도니 같은 자리다.

Wikidata 이용 규약상 User-Agent 에 연락처를 넣어야 한다. 익명 요청은 차단된다.

환경변수
  WIKIDATA_USER_AGENT : 기본값은 아래 UA. 연락처를 바꾸려면 지정한다.
  ACCOUNT_LIST_OUT    : CSV 출력 경로 (기본 account_lists.csv)
  ACCOUNT_LIST_DRY_RUN: 1 이면 BigQuery 적재 없이 CSV 만 만든다
"""
import csv
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request

from google.cloud import bigquery

from bq_common import DATASET, PROJECT_ID, get_bq_client

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

ENDPOINT = "https://query.wikidata.org/sparql"
UA = os.environ.get(
    "WIKIDATA_USER_AGENT",
    "makestar-tour-monitor/1.0 (https://github.com/makestarlab/data-external-crawler; kch@makestar.com)")
TABLE = f"{PROJECT_ID}.{DATASET}.artist_account_links"
# 한 번에 넣는 핸들 수. VALUES 절이 너무 길면 엔드포인트가 타임아웃난다.
CHUNK = 40


def fetch_roster(bq):
    """entity_master 에서 X 핸들이 있는 아티스트를 가져온다."""
    return list(bq.query(f"""
        SELECT entity_id, name, name_en, x_handle
        FROM `{PROJECT_ID}.{DATASET}.entity_master`
        WHERE entity_type = 'ARTIST' AND x_handle IS NOT NULL
        ORDER BY entity_id
    """).result())


def sparql(handles):
    """X 핸들 목록으로 인스타그램·공식사이트를 역조회한다.

    P2002 는 대소문자를 구분해 저장돼 있어 우리 표기와 다를 수 있다.
    그래서 소문자로 맞춰 비교하고, 매칭은 호출부에서 소문자 키로 한다.
    """
    values = " ".join('"%s"' % h.replace('"', '') for h in handles)
    q = f"""
    SELECT ?tw ?item ?itemLabel ?ig ?site WHERE {{
      VALUES ?tw {{ {values} }}
      ?item wdt:P2002 ?tw .
      OPTIONAL {{ ?item wdt:P2003 ?ig }}
      OPTIONAL {{ ?item wdt:P856 ?site }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,ko". }}
    }}"""
    url = ENDPOINT + "?format=json&query=" + urllib.parse.quote(q)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)["results"]["bindings"]


def main():
    bq = get_bq_client()
    roster = fetch_roster(bq)
    log.info("로스터 아티스트 %d명 (X 핸들 보유)", len(roster))

    by_handle = {r["x_handle"].lower(): r for r in roster}
    found = {}
    handles = [r["x_handle"] for r in roster]
    for i in range(0, len(handles), CHUNK):
        part = handles[i:i + CHUNK]
        for attempt in range(3):
            try:
                rows = sparql(part)
                break
            except Exception as e:
                if attempt == 2:
                    log.error("Wikidata 조회 실패 (%d/%d번째 묶음): %s",
                              i // CHUNK + 1, (len(handles) + CHUNK - 1) // CHUNK, e)
                    rows = []
                    break
                wait = 5 * (attempt + 1)
                log.warning("Wikidata 재시도 %d/3 (%d초 대기): %s", attempt + 1, wait, e)
                time.sleep(wait)
        for b in rows:
            tw = b.get("tw", {}).get("value", "")
            key = tw.lower()
            if key not in by_handle:
                continue
            cur = found.setdefault(key, {"ig": None, "site": None,
                                         "qid": None, "label": None})
            if b.get("ig") and not cur["ig"]:
                cur["ig"] = b["ig"]["value"]
            if b.get("site") and not cur["site"]:
                cur["site"] = b["site"]["value"]
            if b.get("item") and not cur["qid"]:
                cur["qid"] = b["item"]["value"].rsplit("/", 1)[-1]
            if b.get("itemLabel") and not cur["label"]:
                cur["label"] = b["itemLabel"]["value"]
        # 엔드포인트에 부담을 주지 않는다. 무료로 쓰는 공공 서비스다.
        time.sleep(1.5)
        log.info("  %d/%d 묶음 처리", i // CHUNK + 1, (len(handles) + CHUNK - 1) // CHUNK)

    out_rows = []
    for r in roster:
        f = found.get(r["x_handle"].lower(), {})
        out_rows.append({
            "entity_id": r["entity_id"],
            "name": r["name"],
            "name_en": r["name_en"],
            "x_handle": r["x_handle"],
            "x_url": f"https://x.com/{r['x_handle']}",
            "instagram_handle": f.get("ig"),
            "instagram_url": f"https://instagram.com/{f['ig']}" if f.get("ig") else None,
            "official_site": f.get("site"),
            "wikidata_qid": f.get("qid"),
            "wikidata_label": f.get("label"),
        })

    out = os.environ.get("ACCOUNT_LIST_OUT", "account_lists.csv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    ig = sum(1 for r in out_rows if r["instagram_handle"])
    site = sum(1 for r in out_rows if r["official_site"])
    qid = sum(1 for r in out_rows if r["wikidata_qid"])
    log.info("%s 저장 | %d명 중 Wikidata 매칭 %d · 인스타그램 %d · 공식사이트 %d",
             out, len(out_rows), qid, ig, site)

    if os.environ.get("ACCOUNT_LIST_DRY_RUN") == "1":
        log.info("DRY RUN - BigQuery 적재 생략")
        return

    # 전량 교체. 이 표는 이력이 아니라 현재 상태 스냅샷이다.
    job = bq.load_table_from_json(
        out_rows, TABLE,
        job_config=bigquery.LoadJobConfig(
            write_disposition="WRITE_TRUNCATE",
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            autodetect=True))
    job.result()
    if job.errors:
        raise RuntimeError(f"BigQuery 적재 오류: {job.errors}")
    log.info("%s 에 %d행 적재 완료", TABLE, len(out_rows))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
