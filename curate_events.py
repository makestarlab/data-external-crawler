#!/usr/bin/env python3
"""
큐레이션 단계 (ELT의 T) - raw x_posts_raw -> curated x_event_announcements

x_posts_raw에서 아직 처리되지 않은(is_curated=FALSE) 포스팅을 Claude API로 읽어
다음을 추출한다:
  - artist_name   : 아티스트명
  - album_or_title: 앨범/타이틀명
  - seller_name   : 판매처명
  - event_name    : 이벤트명 (optional)
  - is_relevant   : 굿즈/앨범/이벤트 판매 공지가 맞는지 여부 (잡담/일상 트윗 등은 false)

그리고 "동일 이벤트에 대한 반복 게시"를 event_key/event_group_id로 그룹핑한다:
  - 같은 계정에서 최근(RECENT_WINDOW_DAYS일) 이미 curated된 대표 이벤트 목록을 프롬프트에
    같이 넣어줘서, LLM이 이어지는 공지면 기존 event_key를 재사용하도록 유도한다.
  - 그룹의 첫 게시물만 is_representative=TRUE로 표시한다. 분석 시에는
    `x_event_announcements_curated` 뷰(WHERE is_relevant AND is_representative)를 쓰면 된다.

필요한 환경변수(GitHub Secrets):
  - ANTHROPIC_API_KEY : Claude API 키
  - GCP_SERVICE_ACCOUNT_JSON : BigQuery 인증 (bq_common.py 참고)
"""
import hashlib
import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import anthropic
from google.cloud import bigquery

from bq_common import PROJECT_ID, DATASET, get_bq_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("curate_events")

RAW_TABLE = f"{PROJECT_ID}.{DATASET}.x_posts_raw"
CURATED_TABLE = f"{PROJECT_ID}.{DATASET}.x_event_announcements"
ENTITY_TABLE = f"{PROJECT_ID}.{DATASET}.entity_master"

MODEL = os.environ.get("CURATION_MODEL", "claude-sonnet-5")
RECENT_WINDOW_DAYS = 21   # 그룹 재사용 판단 시 참고할 "최근 대표 이벤트" 조회 기간
MAX_TWEETS_PER_CALL = 40  # 계정당 한 배치의 최대 트윗 수 (통상 하루 0~10건이라 넉넉함)

TOOL_SCHEMA = {
    "name": "extract_event_announcements",
    "description": "각 트윗에 대해 굿즈/앨범/이벤트 판매 공지 여부와 구조화된 정보를 추출한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tweet_id": {"type": "string"},
                        "is_relevant": {
                            "type": "boolean",
                            "description": "굿즈/앨범/공연 티켓/팬미팅 등 판매·이벤트 공지가 맞으면 true. "
                                           "팬과의 잡담, 일상 사진, 단순 인사말 등은 false.",
                        },
                        "artist_name": {"type": ["string", "null"], "description": "관련 아티스트명 (그룹명 우선)"},
                        "album_or_title": {"type": ["string", "null"], "description": "앨범명/굿즈 타이틀명"},
                        "seller_name": {"type": ["string", "null"], "description": "판매처명 (예: Weverse Shop, FANS SHOP)"},
                        "event_name": {"type": ["string", "null"], "description": "이벤트명 (팬미팅/투어명 등, 없으면 null)"},
                        "event_key": {
                            "type": ["string", "null"],
                            "description": "이 이벤트를 식별하는 snake_case 정규화 키 (예: ateez_allinmidzy_lightring). "
                                           "아래 '이미 등록된 최근 이벤트' 목록에 같은 이벤트가 있으면 그 키를 그대로 재사용할 것.",
                        },
                        "confidence": {"type": "number", "description": "추출 확신도 0.0~1.0"},
                        "note": {"type": ["string", "null"], "description": "판단 근거나 애매한 점 (감사용, 짧게)"},
                    },
                    "required": ["tweet_id", "is_relevant"],
                },
            }
        },
        "required": ["results"],
    },
}

SYSTEM_PROMPT = """당신은 K-pop 아티스트/판매처 공식 X(Twitter) 계정의 포스팅을 분석해서
굿즈·앨범·공연·팬미팅 등의 "판매/이벤트 공지"만 구조화된 정보로 추출하는 어시스턴트입니다.

계정은 두 종류입니다:
- ARTIST 계정: 그 아티스트 자신의 공식 계정. artist_name은 이미 알려져 있음.
- SELLER 계정: 여러 아티스트의 상품을 대신 판매하는 판매처 계정 (예: Weverse Shop, FANS SHOP,
  Ktown4u). 이 경우 트윗 본문에서 실제로 어떤 아티스트의 상품인지 읽어내야 합니다.

반드시 extract_event_announcements 툴을 호출해서 결과를 반환하세요. 확신이 없는 필드는
null로 두고 confidence를 낮게 주세요. 광고/공지가 아닌 일반 트윗(팬과의 소통, 일상, 사진 등)은
is_relevant=false로 표시하고 나머지 필드는 null로 둡니다."""


def _entity_master_has_aliases(bq):
    """entity_master에 aliases 컬럼이 있는지 확인. 컬럼 추가(DDL)와 코드 배포 순서가
    어긋나도 크롤링이 죽지 않도록 방어적으로 조회한다."""
    rows = list(bq.query(f"""
        SELECT COUNT(*) AS c
        FROM `{PROJECT_ID}.{DATASET}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = 'entity_master' AND column_name = 'aliases'
    """).result())
    return rows and rows[0]["c"] > 0


def load_entity_lookup(bq):
    """entity_master 전체를 읽어 (1) 이름->entity_id 역색인, (2) entity_id->표시명,
    (3) SELLER용 아티스트 로스터 텍스트를 만든다.

    역색인 키는 우선순위 순으로 4단계다. 뒤로 갈수록 약한 근거라 앞 단계가 이미 선점한
    키는 덮어쓰지 않는다(setdefault).
      1) name / name_en          - 정식 명칭
      2) 괄호 벗긴 정식 명칭      - entity_master에는 설명을 괄호로 덧붙인 이름이 있다
                                   (예: '애플뮤직(국내 K-pop 판매몰)', 'Apple Music (applemusic.co.kr)').
                                   LLM은 당연히 '애플뮤직'/'Apple Music'으로만 뽑으므로
                                   괄호 형태를 색인하지 않으면 조용히 전부 NULL이 된다.
      3) aliases                 - 수동 등록한 표기 변형 ('SMTOWN', 'Weverse', 'FANS' 등)
      4) x_handle                - LLM이 seller_name을 핸들 그대로 뱉는 경우가 많다
                                   ('JumpUp_ent', 'soundwave_korea', 'MINIRECORD_SHOP')

    aliases 컬럼이 아직 없는 환경에서도 동작한다(있으면 쓰고 없으면 건너뛴다).
    """
    has_aliases = _entity_master_has_aliases(bq)
    alias_col = "aliases" if has_aliases else "CAST(NULL AS ARRAY<STRING>) AS aliases"
    rows = list(bq.query(f"""
        SELECT entity_id, entity_type, name, name_en, x_handle, {alias_col}
        FROM `{ENTITY_TABLE}`
    """).result())

    name_to_id = {}
    id_to_name = {}
    artist_roster_lines = []
    paren_keys = []
    alias_keys = []
    handle_keys = []

    for r in rows:
        val = (r["entity_id"], r["entity_type"])
        for n in (r["name"], r["name_en"]):
            if not n:
                continue
            name_to_id[n.strip().lower()] = val
            m = _PAREN_RE.match(n.strip())
            if m:
                for part in (m.group(1), m.group(2)):
                    if part and part.strip():
                        paren_keys.append((part.strip().lower(), val))
        for a in (r["aliases"] or []):
            if a and a.strip():
                alias_keys.append((a.strip().lower(), val))
        if r["x_handle"]:
            handle_keys.append((r["x_handle"].strip().lower(), val))

        id_to_name[r["entity_id"]] = r["name"]
        if r["entity_type"] == "ARTIST":
            label = r["name"] if not r["name_en"] else f'{r["name"]} ({r["name_en"]})'
            artist_roster_lines.append(label)

    for keys in (paren_keys, alias_keys, handle_keys):
        for key, val in keys:
            name_to_id.setdefault(key, val)

    return name_to_id, id_to_name, "\n".join(sorted(set(artist_roster_lines)))


_PAREN_RE = re.compile(r"^(.*?)\s*\((.*)\)\s*$")


def resolve_entity_id(name, name_to_id, expect_type=None):
    """이름 -> entity_id 조회. artist_roster에는 "한글명 (영문명)" 형태로 넘기는데,
    LLM이 그 형태를 그대로 echo하는 경우가 많아서 (예: "있지 (ITZY)") 정확히
    "이름" 또는 "영문명" 단독 문자열로만 인덱싱된 name_to_id에서 못 찾는 문제가 있었다.
    괄호를 벗겨 양쪽 다 재시도한다."""
    if not name:
        return None
    candidates = [name.strip()]
    m = _PAREN_RE.match(name.strip())
    if m:
        candidates.append(m.group(1).strip())
        candidates.append(m.group(2).strip())
    for cand in candidates:
        if not cand:
            continue
        hit = name_to_id.get(cand.lower())
        if hit and (expect_type is None or hit[1] == expect_type):
            return hit[0]
    return None


def fetch_uncurated_by_handle(bq):
    rows = list(bq.query(f"""
        SELECT tweet_id, x_handle, entity_id, entity_type, run_date, tweet_text, tweet_url, tweet_created_at
        FROM `{RAW_TABLE}`
        WHERE is_curated IS NOT TRUE
        ORDER BY x_handle, tweet_created_at
    """).result())
    by_handle = defaultdict(list)
    for r in rows:
        by_handle[r["x_handle"]].append(dict(r))
    return by_handle


def fetch_recent_representatives(bq, handles):
    if not handles:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_WINDOW_DAYS)).date().isoformat()
    rows = list(bq.query(
        f"""
        SELECT x_handle, event_key, event_group_id, artist_name, album_or_title, event_name
        FROM `{CURATED_TABLE}`
        WHERE is_representative AND run_date >= @cutoff AND x_handle IN UNNEST(@handles)
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("cutoff", "DATE", cutoff),
            bigquery.ArrayQueryParameter("handles", "STRING", handles),
        ]),
    ).result())
    by_handle = defaultdict(list)
    for r in rows:
        by_handle[r["x_handle"]].append(dict(r))
    return by_handle


def make_group_id(x_handle, event_key):
    return hashlib.sha1(f"{x_handle}|{event_key}".encode("utf-8")).hexdigest()[:20]


def call_claude(client, x_handle, entity_type, known_artist_name, artist_roster, recent_events, tweets):
    recent_block = "없음"
    if recent_events:
        recent_block = "\n".join(
            f'- key: {e["event_key"]} | artist: {e["artist_name"]} | title: {e["album_or_title"]} | event: {e["event_name"]}'
            for e in recent_events
        )

    tweets_block = "\n".join(
        f'- tweet_id: {t["tweet_id"]} | 작성일: {t["tweet_created_at"]}\n  본문: {t["tweet_text"]}'
        for t in tweets
    )

    context_lines = [f"계정: @{x_handle} ({entity_type} 계정)"]
    if known_artist_name:
        context_lines.append(f"이 계정은 아티스트 본인 계정이며, artist_name은 '{known_artist_name}'로 고정하세요.")
    if entity_type == "SELLER":
        context_lines.append("이 계정은 여러 아티스트 상품을 파는 판매처입니다. 아래는 참고용 아티스트 명단입니다:")
        context_lines.append(artist_roster)

    user_msg = (
        "\n".join(context_lines)
        + f"\n\n이미 등록된 최근({RECENT_WINDOW_DAYS}일 이내) 이벤트 목록 (같은 이벤트면 event_key를 재사용):\n"
        + recent_block
        + "\n\n분석할 신규 포스팅 목록:\n"
        + tweets_block
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "extract_event_announcements"},
        messages=[{"role": "user", "content": user_msg}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "extract_event_announcements":
            return block.input.get("results", [])
    return []


def build_curated_rows(x_handle, raw_by_tweet_id, extractions, recent_events, name_to_id, extracted_at):
    existing_keys = {e["event_key"]: e["event_group_id"] for e in recent_events if e["event_key"]}
    new_group_first_seen = {}  # event_key -> group_id (이번 배치에서 처음 만든 신규 그룹)
    rows = []

    for res in extractions:
        raw = raw_by_tweet_id.get(res["tweet_id"])
        if raw is None:
            log.warning("알 수 없는 tweet_id가 결과에 포함됨: %s", res["tweet_id"])
            continue

        is_relevant = bool(res.get("is_relevant"))
        event_key = res.get("event_key")
        group_id = None
        is_representative = False

        if is_relevant and event_key:
            if event_key in existing_keys:
                group_id = existing_keys[event_key]
                is_representative = False
            elif event_key in new_group_first_seen:
                group_id = new_group_first_seen[event_key]
                is_representative = False
            else:
                group_id = make_group_id(x_handle, event_key)
                new_group_first_seen[event_key] = group_id
                is_representative = True

        artist_entity_id = resolve_entity_id(res.get("artist_name"), name_to_id, "ARTIST")
        seller_entity_id = resolve_entity_id(res.get("seller_name"), name_to_id, "SELLER")

        # [2026-08-07] SELLER 계정이 자기 계정에 올린 공지면 판매처는 그 계정 자신이다.
        #   seller_name은 LLM이 본문에서 읽어낸 자유 텍스트라 표기 흔들림이 심한데
        #   (핸들 그대로, 지점명 붙임('비트로드 홍대점'), 대소문자/띄어쓰기 변형 등),
        #   raw 행에는 어느 계정에서 수집했는지가 entity_id로 이미 정확히 들어있다.
        #   이름 매칭이 먼저 성공하면 그걸 존중하고(판매처가 타 판매처를 언급하는 경우 대비),
        #   실패했을 때만 계정 자신으로 폴백한다.
        #
        #   [한계] 판매처가 entity_master에 없는 제3자 판매처를 언급한 경우는 이 폴백이
        #   틀린다(예: @MINIRECORD_SHOP이 올린 seller_name='OLIVEYOUNG' 공지가 minirecord로
        #   잡힘). 2026-08-07 기준 전체 677건 중 6건(0.9%) 수준이고, 폴백 없이는 52%가
        #   NULL로 비는 것과 비교하면 감수할 만하다. 원문 seller_name은 그대로 보존되므로
        #   나중에 감사·보정이 가능하다.
        if seller_entity_id is None and raw["entity_type"] == "SELLER":
            seller_entity_id = raw["entity_id"]

        rows.append({
            "event_group_id": group_id,
            "is_representative": is_representative,
            "run_date": raw["run_date"].isoformat() if hasattr(raw["run_date"], "isoformat") else raw["run_date"],
            "tweet_id": raw["tweet_id"],
            "x_handle": x_handle,
            "entity_id": raw["entity_id"],
            "entity_type": raw["entity_type"],
            "source_type": "ARTIST_DIRECT" if raw["entity_type"] == "ARTIST" else "SELLER_DIRECT",
            "artist_name": res.get("artist_name"),
            "artist_entity_id": artist_entity_id,
            "album_or_title": res.get("album_or_title"),
            "seller_name": res.get("seller_name"),
            "seller_entity_id": seller_entity_id,
            "event_name": res.get("event_name"),
            "event_key": event_key,
            "is_relevant": is_relevant,
            "confidence": res.get("confidence"),
            "extraction_note": res.get("note"),
            "tweet_text": raw["tweet_text"],
            "tweet_url": raw["tweet_url"],
            "tweet_created_at": raw["tweet_created_at"].strftime("%Y-%m-%dT%H:%M:%SZ")
                if hasattr(raw["tweet_created_at"], "strftime") else raw["tweet_created_at"],
            "extracted_at": extracted_at,
            "extraction_model": MODEL,
        })
    return rows


def load_curated_rows(bq, rows):
    if not rows:
        return
    table = bq.get_table(CURATED_TABLE)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=table.schema,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = bq.load_table_from_json(rows, CURATED_TABLE, job_config=job_config)
    job.result()
    if job.errors:
        raise RuntimeError(f"BQ load job errors: {job.errors}")
    log.info("x_event_announcements에 %d행 적재 완료", len(rows))


def mark_curated(bq, tweet_ids):
    if not tweet_ids:
        return
    query = f"""
    UPDATE `{RAW_TABLE}`
    SET is_curated = TRUE, curated_at = CURRENT_TIMESTAMP()
    WHERE tweet_id IN UNNEST(@tweet_ids)
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("tweet_ids", "STRING", tweet_ids),
    ])
    bq.query(query, job_config=job_config).result()
    log.info("x_posts_raw %d건 is_curated=TRUE 처리", len(tweet_ids))


def chunked(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def main():
    bq = get_bq_client()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    name_to_id, id_to_name, artist_roster = load_entity_lookup(bq)
    by_handle = fetch_uncurated_by_handle(bq)

    if not by_handle:
        log.info("큐레이션할 신규 포스팅 없음")
        return

    handles = list(by_handle.keys())
    recent_by_handle = fetch_recent_representatives(bq, handles)

    total_processed = 0
    total_relevant = 0

    for x_handle, tweets in by_handle.items():
        entity_type = tweets[0]["entity_type"]
        known_artist_name = None
        if entity_type == "ARTIST":
            # 자기 자신 계정이므로 entity_master에서 이름을 바로 가져온다.
            known_artist_name = id_to_name.get(tweets[0]["entity_id"])

        recent_events = recent_by_handle.get(x_handle, [])

        try:
            all_extractions = []
            for batch in chunked(tweets, MAX_TWEETS_PER_CALL):
                results = call_claude(
                    client, x_handle, entity_type, known_artist_name, artist_roster, recent_events, batch
                )
                all_extractions.extend(results)

            extracted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            raw_by_tweet_id = {t["tweet_id"]: t for t in tweets}
            rows = build_curated_rows(
                x_handle, raw_by_tweet_id, all_extractions, recent_events, name_to_id, extracted_at
            )

            load_curated_rows(bq, rows)
            mark_curated(bq, [t["tweet_id"] for t in tweets])

            total_processed += len(tweets)
            total_relevant += sum(1 for r in rows if r["is_relevant"])
            log.info("%s: %d건 처리, %d건 유의미", x_handle, len(tweets), sum(1 for r in rows if r["is_relevant"]))
        except Exception:
            # 계정 하나 실패해도 나머지 계정은 계속 처리한다. 실패한 계정의 raw 행은
            # is_curated=FALSE로 남아있으므로 다음 실행에서 자동으로 재시도된다.
            log.exception("%s 큐레이션 실패 - is_curated=FALSE로 남겨두고 다음 실행에서 재시도", x_handle)

    log.info("큐레이션 완료: %d개 계정 대상, %d건 처리, %d건 유의미한 이벤트로 추출",
              len(by_handle), total_processed, total_relevant)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("큐레이션 실행 중 처리되지 않은 예외 발생")
        sys.exit(1)
