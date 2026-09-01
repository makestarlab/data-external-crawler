#!/usr/bin/env python3
"""
투어 공지 큐레이션 (ELT의 T) - x_posts_raw -> x_tour_announcements

아티스트 공식 X 계정 포스팅에서 글로벌 투어·공연 일정을 구조화해 뽑는다.
엑셀 '글로벌 투어 현황' 시트의 공연 일자/공연명/IP/국가/도시/베뉴명/판매 링크에 대응한다.

curate_events.py 와의 관계
  - curate_events.py : "판매처가 특정 음반 구매에 붙여 여는 이벤트" 추출 (기존)
  - curate_tour.py   : "투어·공연 일정" 추출 (이 파일)
  둘은 같은 x_posts_raw 를 읽지만 판정 기준도 출력 스키마도 달라서 분리했다.
  기존 판정 정확도(2026-08-12 사람 라벨 감사 기준)를 건드리지 않기 위함이다.

x_posts_raw.is_curated 를 쓰지 않는 이유
  그 플래그는 curate_events.py 의 진행 상태다. 두 배치가 같은 플래그를 공유하면
  먼저 도는 쪽이 상대의 미처리분을 먹어치운다. 대신 x_tour_announcements 에
  이미 적재된 tweet_id 를 안티조인해서 멱등성을 확보한다.

필요한 환경변수(GitHub Secrets):
  - ANTHROPIC_API_KEY
  - GCP_SERVICE_ACCOUNT_JSON  (bq_common.py 참고)
"""
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import anthropic
from google.cloud import bigquery

from bq_common import PROJECT_ID, DATASET, get_bq_client
# 로스터 해소 로직과 응답 정규화는 기존 것을 그대로 쓴다.
#   load_entity_lookup / resolve_entity_id : entity_master 4단계 역색인 + 별칭 처리
#   _coerce_results : 모델이 results 를 규격대로 안 줄 때의 복구 로직.
#     2026-08-13 / 08-24 에 curate_events 쪽에서 이미 겪은 변형들이 정리돼 있다.
#     [2026-09-01] curate_tour 첫 실행이 'str' object has no attribute 'get' 로 죽었다.
#     같은 변형인데 이 파일에서 재사용을 안 해서 그대로 다시 밟았다.
from curate_events import (
    load_entity_lookup,
    resolve_entity_id,
    chunked,
    _coerce_results,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("curate_tour")

RAW_TABLE = f"{PROJECT_ID}.{DATASET}.x_posts_raw"
TOUR_TABLE = f"{PROJECT_ID}.{DATASET}.x_tour_announcements"

MODEL = os.environ.get("TOUR_CURATION_MODEL", "claude-sonnet-5")
BATCH_SIZE = 25          # 한 번의 Claude 호출에 넣을 트윗 수
LOOKBACK_DAYS = int(os.environ.get("TOUR_LOOKBACK_DAYS", "14"))
CONFIDENCE_REVIEW_THRESHOLD = 0.75
MAX_RETRIES = 4

# ---------------------------------------------------------------------------
# 프리필터
# ---------------------------------------------------------------------------
# 아티스트 계정 포스팅 전량을 Claude 에 보내면 대부분이 일상 트윗이라 낭비다.
# 2026-07-01~2026-09-01 실측: 아티스트 계정 48개, 리트윗 제외 10,963건.
#   키워드 또는 예매처 도메인 포함        1,730건 (15.8%, 하루 27건)  <- 채택
#   위 조건에 날짜 표기까지 요구            415건 ( 3.8%, 하루  7건)  <- 미채택
# 좁은 쪽을 안 쓰는 이유: "Tickets Open Now!" 처럼 날짜 없이 예매 오픈만 알리는 공지가
# 통째로 빠진다. 실제로 Stray Kids RUN IT SINGAPORE 티켓 오픈 공지(2026-08-31)가 그 형태다.
# 정규식은 재현율 우선이고, 정밀도는 Claude 가 is_relevant 로 거른다.
TOUR_KEYWORDS = (
    r"tour|concert|fan\s?meet|fan\s?con|fanmeeting|showcase|live in|"
    r"ticket|on sale|presale|pre-sale|sold out|venue|arena|stadium|dome|hall|"
    r"투어|콘서트|공연|팬미팅|팬콘|쇼케이스|티켓|예매|매진|선예매|공연장"
)
TICKET_VENDORS = (
    r"ticketmaster|interpark|melon|yes24|livenation|live nation|ticketlink|weverse|"
    r"tixcraft|damai|lawson|eplus|nol\s?ticket|nolticket|klook|ticketek|myticket|"
    r"viagogo|bookmyshow|cityline|ibon|kkday"
)

VENDOR_LABELS = {
    "ticketmaster": "Ticketmaster", "livenation": "Live Nation", "live nation": "Live Nation",
    "interpark": "Interpark", "melon": "Melon Ticket", "yes24": "YES24",
    "ticketlink": "TicketLink", "weverse": "Weverse", "tixcraft": "tixcraft",
    "damai": "damai", "lawson": "Lawson Ticket", "eplus": "e+",
    "nolticket": "NOL Ticket", "nol ticket": "NOL Ticket", "klook": "Klook",
    "ticketek": "Ticketek", "cityline": "Cityline", "ibon": "ibon", "kkday": "KKday",
}

TOOL_SCHEMA = {
    "name": "extract_tour_announcements",
    "description": "각 트윗에서 투어·공연 일정 공지 여부와 구조화된 일정 정보를 추출한다.",
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
                            "description": "<판정기준>의 is_relevant 조건에 해당하면 true. "
                                           "현장 안내·공연 후기·음원 홍보·멤버십 안내는 false.",
                        },
                        "announcement_kind": {
                            "type": ["string", "null"],
                            "enum": ["NEW_TOUR", "NEW_CITY", "SHOW_INFO", "TICKET_OPEN",
                                     "SOLD_OUT", "SCHEDULE_CHANGE", "OTHER", None],
                        },
                        "tour_name": {"type": ["string", "null"],
                                      "description": "공식 투어명 원문. 도시 접미사는 떼고 상위 투어명만."},
                        "event_type": {
                            "type": ["string", "null"],
                            "enum": ["콘서트/투어", "팬미팅", "팬콘", "뮤직 페스티벌",
                                     "시상식", "쇼케이스", "기타", None],
                        },
                        "artist_names": {"type": "array", "items": {"type": "string"},
                                         "description": "공지에 등장한 아티스트명. 합동이면 여럿."},
                        "shows": {
                            "type": "array",
                            "description": "회차 목록. 날짜 범위는 날짜마다 한 원소로 펼칠 것.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "event_date": {"type": ["string", "null"],
                                                   "description": "YYYY-MM-DD. 모르면 null."},
                                    "date_text": {"type": ["string", "null"], "description": "원문 날짜 표기"},
                                    "city": {"type": ["string", "null"]},
                                    "country": {"type": ["string", "null"],
                                                "description": "영문 대문자 국가명. 예: JAPAN, HONG KONG"},
                                    "venue_name": {"type": ["string", "null"]},
                                },
                            },
                        },
                        "ticket_opens": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": ["string", "null"]},
                                    "opens_at_text": {"type": ["string", "null"]},
                                    "timezone_note": {"type": ["string", "null"]},
                                },
                            },
                        },
                        "confidence": {"type": "number", "description": "0.0~1.0"},
                        "note": {"type": ["string", "null"], "description": "판단 근거 한 줄 (감사용)"},
                    },
                    "required": ["tweet_id", "is_relevant"],
                },
            }
        },
        "required": ["results"],
    },
}

SYSTEM_PROMPT_BASE = """당신은 K-pop 아티스트 공식 X(Twitter) 계정의 포스팅에서
"글로벌 투어·공연 일정 공지"만 골라내 구조화하는 어시스턴트입니다.

이 결과는 미주유럽사업팀이 아티스트 방문 시점에 맞춰 자체 팬 이벤트를 기획할지 판단하는 데
쓰입니다. 즉 필요한 것은 "언제, 어느 도시, 어느 공연장에서, 무슨 공연이 열리는가"입니다.

아래 <판정기준>을 그대로 적용하세요. 기준에 없는 것을 임의로 통과시키지 마세요.
반드시 extract_tour_announcements 툴을 호출해서 결과를 반환하고, 입력으로 받은 모든
tweet_id 에 대해 결과를 하나씩 돌려주세요."""

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "prompts", "tour_extraction_rules.md")
_SYSTEM_PROMPT_CACHE = None


def build_system_prompt():
    """SYSTEM_PROMPT_BASE + prompts/tour_extraction_rules.md 를 합쳐 돌려준다."""
    global _SYSTEM_PROMPT_CACHE
    if _SYSTEM_PROMPT_CACHE is not None:
        return _SYSTEM_PROMPT_CACHE
    try:
        with open(RULES_PATH, encoding="utf-8") as f:
            rules = f.read().strip()
    except OSError:
        log.warning("판정 기준 파일을 못 읽었습니다: %s - 기준 없이 진행합니다", RULES_PATH)
        rules = ""
    _SYSTEM_PROMPT_CACHE = (
        SYSTEM_PROMPT_BASE + "\n\n<판정기준>\n" + rules + "\n</판정기준>" if rules
        else SYSTEM_PROMPT_BASE
    )
    return _SYSTEM_PROMPT_CACHE


def fetch_candidate_posts(bq):
    """아직 투어 큐레이션이 안 된 아티스트 포스팅을 프리필터까지 걸어서 가져온다.

    x_tour_announcements 안티조인으로 멱등성을 잡는다. 같은 날 워크플로를 두 번 돌려도
    이미 적재된 트윗은 다시 Claude 에 보내지 않는다.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    rows = list(bq.query(
        f"""
        SELECT r.tweet_id, r.x_handle, r.entity_id, r.tweet_text, r.tweet_url,
               r.tweet_created_at, r.entities_json
        FROM `{RAW_TABLE}` r
        LEFT JOIN (SELECT DISTINCT tweet_id FROM `{TOUR_TABLE}`) d USING (tweet_id)
        WHERE r.entity_type = 'ARTIST'
          AND NOT IFNULL(r.is_retweet, FALSE)
          AND r.run_date >= @cutoff
          AND d.tweet_id IS NULL
          AND r.tweet_text IS NOT NULL
          AND (REGEXP_CONTAINS(LOWER(r.tweet_text), r'{TOUR_KEYWORDS}')
               OR REGEXP_CONTAINS(LOWER(r.tweet_text), r'{TICKET_VENDORS}'))
        ORDER BY r.x_handle, r.tweet_created_at
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("cutoff", "DATE", cutoff),
        ]),
    ).result())
    by_handle = defaultdict(list)
    for r in rows:
        by_handle[r["x_handle"]].append(dict(r))
    return by_handle


def extract_urls(entities_json, tweet_text):
    """엔티티 JSON 의 expanded_url 을 우선 쓰고, 없으면 본문의 t.co 링크를 줍는다.
    expanded_url 이 있어야 예매처 판별(ticket_vendor)이 되므로 순서가 중요하다."""
    urls = []
    if entities_json:
        try:
            ents = json.loads(entities_json)
            for u in ents.get("urls", []) or []:
                v = u.get("expanded_url") or u.get("unwound_url") or u.get("url")
                if v:
                    urls.append(v)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
    if not urls and tweet_text:
        urls = re.findall(r"https?://t\.co/\w+", tweet_text)
    # 중복 제거하되 순서는 유지한다
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def detect_vendor(urls, tweet_text):
    haystack = " ".join(urls).lower() + " " + (tweet_text or "").lower()
    for pat, label in VENDOR_LABELS.items():
        if pat in haystack:
            return label
    return None


def norm_token(s):
    return re.sub(r"[^a-z0-9가-힣]+", "", (s or "").lower())


def make_show_key(artist, event_date, city):
    raw = f"{norm_token(artist)}|{event_date or ''}|{norm_token(city)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def parse_iso_date(s):
    """모델이 YYYY-MM-DD 로 주기로 돼 있지만, 어긋난 값이 오면 버린다.
    잘못된 날짜를 시트에 넣는 것보다 비워두고 확인 큐로 보내는 편이 낫다."""
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s.strip())
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    # 발표 시점 기준으로 5년 넘게 떨어진 날짜는 파싱 오류로 본다 (연도 추론 실패 등)
    today = datetime.now(timezone.utc).date()
    if not (today - timedelta(days=730) <= d <= today + timedelta(days=1095)):
        return None
    return d.isoformat()


def call_claude(client, x_handle, artist_roster, tweets):
    """계정 단위로 묶어서 한 번 호출한다. 같은 아티스트의 연속 공지를 한 문맥에서 보게 하려는 것."""
    lines = []
    for t in tweets:
        created = t["tweet_created_at"].isoformat() if t.get("tweet_created_at") else ""
        body = (t.get("tweet_text") or "").replace("\n", " / ")
        lines.append(f"[tweet_id={t['tweet_id']}] (작성 {created})\n{body}")
    # load_entity_lookup 은 로스터를 "한글명 (영문명)" 줄바꿈 문자열로 돌려준다.
    # 그대로 넣으면 프롬프트가 길어져 캐시 미스가 늘고 오판도 늘어나므로 줄 수로 자른다.
    roster_hint = ""
    if artist_roster:
        lines = [ln for ln in artist_roster.splitlines() if ln.strip()][:300]
        if lines:
            roster_hint = ("\n\n<등록된 아티스트명 목록 - artist_names 는 가급적 이 표기를 따를 것>\n"
                           + "\n".join(lines))
    user_msg = (f"계정: @{x_handle}\n"
                f"아래 {len(tweets)}건의 트윗 각각에 대해 결과를 돌려주세요."
                f"{roster_hint}\n\n" + "\n\n".join(lines))

    delay = 2
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=8000,
                system=build_system_prompt(),
                tools=[TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "extract_tour_announcements"},
                messages=[{"role": "user", "content": user_msg}],
            )
            for block in resp.content:
                if block.type == "tool_use":
                    raw = block.input.get("results", [])
                    coerced = _coerce_results(raw, x_handle)
                    if coerced is None:
                        log.error("@%s: results 를 리스트로 복구하지 못했습니다 (type=%s) - "
                                  "이 배치는 적재하지 않고 다음 실행에서 재시도됩니다",
                                  x_handle, type(raw).__name__)
                        return []
                    return coerced
            log.warning("@%s: tool_use 블록이 없습니다 - 빈 결과로 처리", x_handle)
            return []
        except (anthropic.APIStatusError, anthropic.APIConnectionError, anthropic.RateLimitError) as e:
            status = getattr(e, "status_code", None)
            # 400/401/403 은 재시도해도 같은 결과다. 나머지는 백오프 후 재시도.
            if status in (400, 401, 403) or attempt == MAX_RETRIES:
                log.error("@%s: Claude 호출 실패 (attempt %d/%d, status=%s): %s",
                          x_handle, attempt, MAX_RETRIES, status, str(e)[:300])
                return []
            log.warning("@%s: Claude 호출 재시도 %d/%d (status=%s, %ds 대기)",
                        x_handle, attempt, MAX_RETRIES, status, delay)
            time.sleep(delay)
            delay *= 2
    return []


def build_rows(x_handle, raw_by_id, results, name_to_id, run_date, extracted_at):
    rows = []
    for res in results:
        if not isinstance(res, dict):
            # _coerce_results 가 파싱을 시도한 뒤에도 문자열로 남은 항목. 여기서 죽으면
            # 그 계정 배치 전체가 유실되므로, 해당 항목만 버리고 나머지는 살린다.
            # 버린 트윗은 x_tour_announcements 에 안 들어가므로 다음 실행에서 자동 재시도된다.
            log.warning("@%s: results 항목이 dict 가 아닙니다 (type=%s) - 이 항목만 건너뜁니다: %.120s",
                        x_handle, type(res).__name__, str(res))
            continue
        tid = str(res.get("tweet_id") or "")
        raw = raw_by_id.get(tid)
        if raw is None:
            # 모델이 없는 tweet_id 를 지어낸 경우. 조용히 버리면 원인 추적이 안 되므로 남긴다.
            log.warning("@%s: 입력에 없는 tweet_id 를 반환했습니다: %s", x_handle, tid)
            continue

        is_relevant = bool(res.get("is_relevant"))
        artist_names = [a for a in (res.get("artist_names") or []) if a]
        entity_ids = []
        for a in artist_names:
            eid = resolve_entity_id(a, name_to_id, expect_type="ARTIST")
            if eid and eid not in entity_ids:
                entity_ids.append(eid)
        # 계정 자기참조 폴백: 아티스트 계정이 자기 투어를 알리는 게 대부분이므로,
        # 본문에서 이름을 못 뽑았으면 계정 소유 엔티티를 쓴다 (기존 크롤러와 같은 로직).
        if not entity_ids and raw.get("entity_id"):
            entity_ids = [raw["entity_id"]]

        urls = extract_urls(raw.get("entities_json"), raw.get("tweet_text"))
        vendor = detect_vendor(urls, raw.get("tweet_text"))
        primary_artist = artist_names[0] if artist_names else (raw.get("entity_id") or x_handle)

        shows, dropped_dates = [], 0
        for s in (res.get("shows") or []):
            iso = parse_iso_date(s.get("event_date"))
            if s.get("event_date") and iso is None:
                dropped_dates += 1
            shows.append({
                "show_key": make_show_key(primary_artist, iso, s.get("city")),
                "event_date": iso,
                "date_text": s.get("date_text"),
                "city": s.get("city"),
                "country": (s.get("country") or "").upper().strip() or None,
                "venue_name": s.get("venue_name"),
            })

        conf = res.get("confidence")
        conf = float(conf) if isinstance(conf, (int, float)) else None

        reasons = []
        kind = res.get("announcement_kind")
        if conf is not None and conf < CONFIDENCE_REVIEW_THRESHOLD:
            reasons.append(f"confidence {conf:.2f} < {CONFIDENCE_REVIEW_THRESHOLD}")
        if kind in ("NEW_TOUR", "NEW_CITY", "SHOW_INFO") and not any(s["event_date"] for s in shows):
            reasons.append("일정 공지인데 확정 날짜 없음")
        if kind == "SHOW_INFO" and any(not s.get("venue_name") for s in shows):
            reasons.append("SHOW_INFO 인데 공연장 결측")
        if any(not s.get("city") for s in shows):
            reasons.append("도시 결측")
        if not entity_ids:
            # 조용한 NULL 이 커버리지를 갉아먹는 지점. 반드시 사람이 보게 만든다.
            reasons.append("entity_master 매칭 실패 - 로스터 등록 필요")
        if dropped_dates:
            reasons.append(f"날짜 파싱 실패 {dropped_dates}건")

        rows.append({
            "run_date": run_date,
            "tweet_id": tid,
            "x_handle": x_handle,
            "entity_id": raw.get("entity_id"),
            "artist_names": artist_names,
            "artist_entity_ids": entity_ids,
            "tour_name": res.get("tour_name"),
            "event_type": res.get("event_type"),
            "announcement_kind": kind,
            "shows": shows,
            "ticket_opens": [{
                "label": o.get("label"),
                "opens_at_text": o.get("opens_at_text"),
                "timezone_note": o.get("timezone_note"),
            } for o in (res.get("ticket_opens") or [])],
            "ticket_urls": urls,
            "ticket_vendor": vendor,
            "is_relevant": is_relevant,
            "confidence": conf,
            "needs_review": bool(is_relevant and reasons),
            "review_reason": "; ".join(reasons) if (is_relevant and reasons) else None,
            "note": res.get("note"),
            "tweet_text": raw.get("tweet_text"),
            "tweet_url": raw.get("tweet_url"),
            "tweet_created_at": raw["tweet_created_at"].isoformat() if raw.get("tweet_created_at") else None,
            "extracted_at": extracted_at,
            "extraction_model": MODEL,
        })
    return rows


def load_rows(bq, rows):
    """배치 로드 잡(무료)으로 적재한다. streaming insert 는 행당 최소 1KB 과금이라 쓰지 않는다."""
    if not rows:
        return
    table = bq.get_table(TOUR_TABLE)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=table.schema,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = bq.load_table_from_json(rows, TOUR_TABLE, job_config=job_config)
    job.result()
    if job.errors:
        raise RuntimeError(f"BigQuery 로드 잡 오류: {job.errors}")
    log.info("x_tour_announcements 에 %d행 적재 완료", len(rows))


def main():
    bq = get_bq_client()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    name_to_id, _id_to_name, artist_roster = load_entity_lookup(bq)
    by_handle = fetch_candidate_posts(bq)
    if not by_handle:
        log.info("투어 큐레이션할 신규 포스팅 없음")
        return

    total_in = sum(len(v) for v in by_handle.values())
    log.info("대상 %d계정 / %d건 (최근 %d일, 프리필터 통과분)",
             len(by_handle), total_in, LOOKBACK_DAYS)

    run_date = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    extracted_at = datetime.now(timezone.utc).isoformat()

    all_rows, calls, broken = [], 0, []
    for x_handle, tweets in by_handle.items():
        # 계정 단위로 예외를 가둔다.
        # [2026-09-01] 한 계정에서 터진 예외가 main 까지 올라가면서, 그 앞에서 이미 Claude
        # 호출을 마친 계정들의 결과까지 통째로 버려졌다. 토큰은 쓰고 남은 건 없는 상태.
        # 한 계정이 실패해도 나머지는 적재하고, 실패한 계정은 안티조인 덕에 다음 실행에서
        # 자동으로 다시 대상이 된다.
        try:
            raw_by_id = {t["tweet_id"]: t for t in tweets}
            for batch in chunked(tweets, BATCH_SIZE):
                results = call_claude(client, x_handle, artist_roster, batch)
                calls += 1
                all_rows.extend(build_rows(x_handle, raw_by_id, results,
                                           name_to_id, run_date, extracted_at))
        except Exception:
            log.exception("@%s 처리 중 예외 - 이 계정만 건너뜁니다", x_handle)
            broken.append(x_handle)

    load_rows(bq, all_rows)

    relevant = [r for r in all_rows if r["is_relevant"]]
    shows = sum(len(r["shows"]) for r in relevant)
    dated = sum(1 for r in relevant for s in r["shows"] if s["event_date"])
    review = sum(1 for r in relevant if r["needs_review"])
    log.info("Claude 호출 %d회 | 입력 %d건 -> 투어 공지 %d건 | 회차 %d개(날짜 확정 %d) | 확인 필요 %d건",
             calls, total_in, len(relevant), shows, dated, review)

    kinds = defaultdict(int)
    for r in relevant:
        kinds[r["announcement_kind"] or "UNKNOWN"] += 1
    if kinds:
        log.info("공지 유형: %s", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))

    if relevant and review / len(relevant) > 0.5:
        # 절반 넘게 확인 필요로 나오면 프롬프트나 로스터에 문제가 생긴 것이다.
        log.warning("확인 필요 비율이 %.0f%% 입니다 - 프롬프트/로스터 점검 필요",
                    review / len(relevant) * 100)

    if broken:
        # 적재는 이미 끝났으므로 성공분은 남는다. 그래도 워크플로는 실패로 띄워서
        # 계정별 예외가 조용히 반복되지 않게 한다.
        log.error("예외로 건너뛴 계정 %d개: %s", len(broken), ", ".join(broken))
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("투어 큐레이션 실패")
        sys.exit(1)
