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
import time
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
RECENT_WINDOW_DAYS = 45   # 그룹 재사용 판단 시 참고할 "최근 대표 이벤트" 조회 기간
#   [2026-08-13] 21 -> 45. 같은 이벤트 공지가 21일 넘게 띄엄띄엄 올라오면 뒤엣것이
#   목록에서 빠져 "새 이벤트"로 잡히고, 같은 event_group_id에 대표가 둘 생겼다(29개 그룹).
#
#   45는 실측값이다. 여러 번 공지된 이벤트 619건의 첫 공지~마지막 공지 간격 분포:
#     중앙값 3일 · p75 11일 · p90 26일 · p95 34일 · p99 44일 · 최대 47일
#   21일 창은 상위 10%를 놓치고 있었다. 45일이면 99%를 덮고, 넘는 건 79건 중 2건뿐.
#   60일로 더 늘려도 2건을 더 얻을 뿐이라 목록만 길어진다.
#   중앙값이 3일이라 짧게 잡기 쉬운데, 문제를 만드는 건 항상 꼬리 쪽이다.
MAX_RECENT_PER_HANDLE = 80
#   창을 넓히면 목록이 길어진다(21일 593건 -> 45일 1,209건, EVERLINESHOP 한 계정만 136건).
#   목록이 길수록 모델이 그걸 근거로 통과시키는 오판이 늘어나므로(2026-08-12에 정확도
#   96.7% -> 91.7%로 떨어진 원인) 계정당 상한을 두고 최신순으로 자른다.
MISS_RATE_ALERT = 0.10
#   [2026-08-24] 미처리율이 이 값을 넘으면 워크플로를 실패(빨간불) 처리한다.
#   그전까지는 계정 단위 실패를 전부 삼켜서 Actions가 항상 초록불이었고, 8/22~8/24에
#   미처리율이 0% -> 1.9% -> 6.1% -> 29.8%로 올라가는 동안 아무 신호가 없었다.
#   미처리 행은 다음 실행에서 재시도되므로 데이터 유실은 아니다. "사람이 봐야 한다"는 신호다.
MAX_TWEETS_PER_CALL = 15  # 계정당 한 배치의 최대 트윗 수
#   [2026-08-12] 40 -> 15. 40건을 한 번에 보내면 응답이 max_tokens에 걸려
#   tool_use 블록이 잘리고, 그 배치 40건이 통째로 결과에서 사라졌다.
#   판매처 계정은 채울 필드가 많아 특히 심했다 (EVERLINESHOP 364건 중 360건 유실).

# =============================================================================
# [2026-08-10] 비판매 게시물 필터 (도메인 기반)
# =============================================================================
# 배경: '판매처 미상'(seller_name IS NULL)으로 남는 게시물 344건을 링크 도메인별로
#   조사해보니, 실제 판매글은 거의 없고 대부분 음원 발매 공지 / 음악방송·시상식 투표
#   안내 / 팬커뮤니티(위버스·베리즈·팬더원) 공지 / 콘서트 티켓·라이브뷰잉 공지였다.
#   LLM이 "이벤트성 공지"를 넓게 is_relevant=TRUE로 잡는 탓인데, 이런 건들이
#   대시보드의 "타 판매처 대응" 목록을 오염시킨다.
#
# 적용 범위: 판매처가 끝내 특정되지 않은 게시물(seller_name이 비어 "판매처 미상"으로
#   묶이는 건)만 대상. 판매처가 붙은 게시물은 그 자체로 유효한 신호라 손대지 않는다.
#
# 판정 규칙 — 아래 둘 중 하나면 is_relevant=False:
#   (1) 외부 링크가 하나도 없음
#   (2) 링크는 있는데 (a) 전부 NON_SALES_DOMAIN_RE에 걸리고
#       (b) 구매 경로(/shop, /product, /goods ...) 링크가 하나도 없고
#       (c) 본문에 판매/굿즈 신호 키워드(SALES_KEEP_RE)가 전혀 없음
#
#   (2)는 세 조건을 모두 요구하므로, 위버스 공지라도 본문에 '럭키드로우'/'특전'/
#   '팬사인회'가 있으면 살아남는다(실제로 위버스 공지 중 판매성 건들이 여기 해당).
#
#   [2026-08-10 사용자 확정] (1)은 원래 "판단 근거가 없으니 유지"였는데, 링크 없는 건
#   104건(대표 기준)이 대부분 투표 안내 / 방송 참여·인원체크 / 당첨자 발표 /
#   트랙리스트·컨셉포토 공개라 전부 제외하기로 했다. 실제 판매글도 일부 섞여 있다는 걸
#   알고 내린 결정이다 — 링크 없이 이미지로만 안내한 건들(RT @SKZ_THISANDTHAT 팝업
#   MERCH SALES, RT @mnetplusmerch KCON MD, RT @magazineTheStar 더스타 팬사인회 등).
#   RT 원본 핸들(`RT @xxx:`)을 entity_master.x_handle로 역매핑해 판매처를 붙이면
#   이 손실분 중 상당수를 되살릴 수 있다(미구현).
#
# 주의: 판매처 도메인(yes24, aladin, ktown4u, weverseshop 등)은 절대 넣지 말 것.
#   weverse.io(팬커뮤니티)와 weverseshop.io(판매처)는 다른 도메인이다.
#
# 아티스트/소속사 자체몰(ateez.kqent.com/shop, xikers.kr/shop, nouera-official.com/shop 등)은
#   여기 넣지 않는다. 2026-08-10 사용자 확정: 자체몰은 "타 판매처"가 아니므로 entity_master에
#   판매처로 등록하지 않지만, 실제 상품 판매 공지일 수 있어 is_relevant까지 떨어뜨리진 않는다.
#   결과적으로 seller_name은 계속 비어(=판매처 미상) 남는다 — 의도된 동작이다.
NON_SALES_DOMAIN_RE = re.compile(
    r"("
    # 음원/스트리밍
    r"\.lnk\.to$|^lnk\.to$|^(m2?\.)?melon\.com$|^genie\.co\.kr$|^m?\.?music-flo\.com$"
    r"|^music\.bugs\.co\.kr$|^vibe\.naver\.com$|^open\.spotify\.com$|^music\.youtube\.com$"
    r"|^orcd\.co$|^stationhead\.com$"
    # SNS/미디어/앱스토어
    r"|^youtube\.com$|^youtu\.be$|^(vt\.)?tiktok\.com$|^instagram\.com$|(^|\.)pinterest\.com$"
    r"|^m?\.?entertain\.naver\.com$|^news\.naver\.com$|^x\.com$|^twitter\.com$|^facebook\.com$"
    r"|^threads\.(com|net)$|^docs\.google\.com$|^play\.google\.com$|^apps\.apple\.com$|^linktr\.ee$"
    # 팬커뮤니티/투표앱 (판매처 아님 — 공지 채널)
    r"|(^|\.)weverse\.io$|(^|\.)berriz\.in$|^app\.fans$|^link\.fans$|(^|\.)mnetplus\.world$"
    r"|^mnetplus\.onelink\.me$|(^|\.)fanca\.io$|^fantheone\.com$|(^|\.)idolchamp\.com$"
    r"|(^|\.)linc\.fan$|^pypd\.app$|(^|\.)flybook\.kr$|(^|\.)dayoff\.at$"
    # 티켓/공연
    r"|^ticketmaster\.com$|^a-nation\.net$|^api-liveviewing\.com$|^hybejapan-concert\.com$"
    r"|(^|\.)kcforum\.co\.kr$"
    r")"
)

SHOP_PATH_RE = re.compile(r"/(shop|store|product|products|goods|order|cart|buy|item)(/|\?|$)", re.I)

SALES_KEEP_RE = re.compile(
    r"(판매|구매|예약|예판|선주문|pre-?order|응모|팬사인회|사인회|영상통화|영통"
    r"|럭키\s*드로우|lucky\s*draw|럭드|특전|特典|\bmd\b|굿즈|merch|입고|당첨|추첨"
    r"|pop-?up|팝업|special\s*gift|fan\s*sign|meet\s*[n&]\s*greet|밈앤그릿|video\s*call"
    r"|\bkit\b|키트|아카이브북|archiving\s*book|photo\s*book|포토북|화보집"
    r"|응원봉|light\s*stick|라이트스틱|이용권|sound\s*coin|시즌\s*그리팅|season.?s?\s*greeting"
    r"|\bstore\b|스토어)",
    re.I,
)

_TWEET_MEDIA_URL_RE = re.compile(r"^https?://(x|twitter)\.com/[^/]+/status/\d+/(photo|video)/", re.I)
_HOST_RE = re.compile(r"^https?://([^/?#]+)", re.I)


def extract_link_urls(*entities_jsons):
    """entities_json(들)에서 외부 링크 URL을 뽑는다.

    트윗 본문의 링크는 전부 t.co 단축 URL이라 도메인 판정에 쓸 수 없다. 원본 도메인은
    entities.urls[].unwound_url(리다이렉트 최종 목적지) 또는 expanded_url에만 들어있다.
    RT는 본문이 잘리므로 호출부에서 원본 트윗의 entities_json도 같이 넘긴다.
    트윗 자체의 첨부 사진/영상 링크(x.com/.../photo/1)는 링크로 치지 않는다.
    """
    urls = []
    for ej in entities_jsons:
        if not ej:
            continue
        try:
            data = json.loads(ej) if isinstance(ej, str) else ej
        except (ValueError, TypeError):
            continue
        for u in (data or {}).get("urls") or []:
            url = u.get("unwound_url") or u.get("expanded_url")
            if url and not _TWEET_MEDIA_URL_RE.match(url):
                urls.append(url)
    return urls


def looks_non_sales(urls, tweet_text):
    """제외 대상이면 True."""
    if not urls:
        return True                       # 링크 없음 → 제외 (2026-08-10 사용자 확정)
    if any(SHOP_PATH_RE.search(u) for u in urls):
        return False                      # 구매 경로 링크가 하나라도 있으면 유지
    for u in urls:
        m = _HOST_RE.match(u)
        host = re.sub(r"^www\.", "", m.group(1).lower()) if m else ""
        if not NON_SALES_DOMAIN_RE.search(host):
            return False                  # 판매처일 수 있는 도메인이 섞여 있으면 유지
    if SALES_KEEP_RE.search(tweet_text or ""):
        return False                      # 본문에 판매/굿즈 신호가 있으면 유지
    return True

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
                            "description": "<판정기준>의 세 질문에 모두 YES 일 때만 true. "
                                           "① 특정 음반(앨범/EP/싱글)에 붙어 있는가 "
                                           "② 판매처가 특정되는가 "
                                           "③ 구매자에게 응모·특전이 주어지는가. "
                                           "하나라도 아니면 false. 굿즈·MD·팝업·공연·음원 프로모션은 false.",
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

SYSTEM_PROMPT_BASE = """당신은 K-pop 아티스트/판매처 공식 X(Twitter) 계정의 포스팅에서
"판매처가 특정 음반의 구매에 붙여서 여는 이벤트 공지"만 골라내 구조화하는 어시스턴트입니다.

계정은 두 종류입니다:
- ARTIST 계정: 그 아티스트 자신의 공식 계정. artist_name은 이미 알려져 있음.
- SELLER 계정: 여러 아티스트의 상품을 대신 판매하는 판매처 계정 (예: Weverse Shop, FANS SHOP,
  Ktown4u). 이 경우 트윗 본문에서 실제로 어떤 아티스트의 상품인지 읽어내야 합니다.

아래 <판정기준>을 그대로 적용하세요. 기준에 없는 것을 임의로 통과시키지 마세요.
반드시 extract_event_announcements 툴을 호출해서 결과를 반환하고, is_relevant=false 인 건은
note에 세 질문 중 어디서 걸렸는지를 한 줄로 남기세요."""

# 판정 기준의 단일 출처. 사람이 읽는 문서이자 프롬프트 본문이라 코드가 아니라 파일로 둔다.
# 기준을 바꿀 때 이 파일만 고치면 되고, 스킬 문서와 프롬프트가 갈라지지 않는다.
# 근거: 2026-08-12 사람 라벨 60건 감사. 모델 통과 30건 중 21건이 오탐이었고
# 그 21건이 전부 기준 파일의 "세 질문"으로 설명된다.
RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "prompts", "classification_rules.md")
_SYSTEM_PROMPT_CACHE = None


def build_system_prompt():
    """SYSTEM_PROMPT_BASE + prompts/classification_rules.md 를 합쳐 돌려준다."""
    global _SYSTEM_PROMPT_CACHE
    if _SYSTEM_PROMPT_CACHE is not None:
        return _SYSTEM_PROMPT_CACHE
    try:
        with open(RULES_PATH, encoding="utf-8") as f:
            rules = f.read().strip()
    except OSError:
        log.warning("판정 기준 파일을 못 읽었습니다: %s — 기준 없이 진행합니다", RULES_PATH)
        rules = ""
    _SYSTEM_PROMPT_CACHE = (
        SYSTEM_PROMPT_BASE + "\n\n<판정기준>\n" + rules + "\n</판정기준>"
        if rules else SYSTEM_PROMPT_BASE
    )
    return _SYSTEM_PROMPT_CACHE


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
    # entities_json은 도메인 기반 비판매 필터(looks_non_sales)에서만 쓴다. RT는 본문이
    # 잘려 링크가 사라지므로 referenced_tweet_id로 원본 트윗의 entities_json도 같이 가져온다
    # (원본이 우리 수집 대상이 아니면 NULL — 그 경우 필터는 그냥 판단을 보류한다).
    rows = list(bq.query(f"""
        SELECT r.tweet_id, r.x_handle, r.entity_id, r.entity_type, r.run_date,
               r.tweet_text, r.tweet_url, r.tweet_created_at,
               r.entities_json, ref.entities_json AS ref_entities_json
        FROM `{RAW_TABLE}` r
        LEFT JOIN (
          SELECT tweet_id, ANY_VALUE(entities_json) AS entities_json
          FROM `{RAW_TABLE}` GROUP BY tweet_id
        ) ref ON ref.tweet_id = r.referenced_tweet_id
        WHERE r.is_curated IS NOT TRUE
        ORDER BY r.x_handle, r.tweet_created_at
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
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY x_handle ORDER BY run_date DESC, tweet_created_at DESC
        ) <= {MAX_RECENT_PER_HANDLE}
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


def _err_detail(e, limit=400):
    """예외에서 서버가 실제로 준 메시지를 뽑아낸다.

    [2026-08-24] 이전엔 `type(e).__name__`만 로그에 남겼다. 그래서 8/22~8/24에 배치가
      줄줄이 죽었을 때 로그에 `BadRequestError`만 찍히고, 요청이 잘못된 건지 크레딧이
      떨어진 건지 한도에 걸린 건지 구분할 방법이 없었다. 원인을 못 좁히면 고칠 수도 없다.
    """
    parts = []
    status = getattr(e, "status_code", None)
    if status is not None:
        parts.append(f"HTTP {status}")
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        if err.get("type"):
            parts.append(err["type"])
        msg = err.get("message") or body.get("message")
        if msg:
            parts.append(str(msg))
    if len(parts) <= 1:
        parts.append(str(e))
    return " | ".join(parts)[:limit]


def _is_permanent(e):
    """재시도해도 결과가 같은 오류인가.

    4xx 중 재시도가 의미 있는 건 429(rate limit)뿐이다. 400/401/403은 같은 요청을 다시
    보내봐야 같은 답이 온다. 실제로 2026-08-24 실행에서 400을 계정마다 5번씩 다시 쳐서
    배치당 45초(3+6+12+24)를 버렸고, 그 바람에 뒤쪽 계정들이 통째로 밀렸다.
    """
    status = getattr(e, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status != 429


def call_claude(client, x_handle, entity_type, known_artist_name, artist_roster, recent_events, tweets):
    recent_block = "없음"
    if recent_events:
        recent_block = "\n".join(
            f'- key: {e["event_key"]} | artist: {e["artist_name"]} | title: {e["album_or_title"]} | event: {e["event_name"]}'
            for e in recent_events
        )

    # 링크 도메인을 모델에 같이 넘긴다. 본문 링크는 전부 t.co 라 모델이 도메인을 볼 수 없고,
    # 판정 기준의 "링크 도메인" 절이 그대로 사문화되기 때문이다.
    def _link_hosts(t):
        urls = extract_link_urls(t.get("entities_json"), t.get("ref_entities_json"))
        hosts = sorted({re.sub(r"^www\.", "", m.group(1).lower())
                        for u in urls for m in [_HOST_RE.match(u)] if m})
        return ", ".join(hosts) if hosts else "링크 없음"

    tweets_block = "\n".join(
        f'- tweet_id: {t["tweet_id"]} | 작성일: {t["tweet_created_at"]}\n'
        f'  본문: {t["tweet_text"]}\n'
        f'  링크 도메인: {_link_hosts(t)}'
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

    # [2026-08-12] 재시도. 예전엔 429/529 한 번이면 예외가 위로 올라가
    #   그 계정 전체가 스킵됐다 (2026-08-12 재큐레이션에서 11개 계정 1,709건이 이렇게 남음).
    #   호출 수가 많은 계정일수록 확률이 높아 판매처가 집중적으로 당했다.
    resp = None
    for attempt in range(5):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=16000,   # [2026-08-12] 4096 -> 16000. 응답이 잘려 배치가 통째로 유실됐다
                system=build_system_prompt(),
                tools=[TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "extract_event_announcements"},
                messages=[{"role": "user", "content": user_msg}],
            )
            break
        except Exception as e:
            detail = _err_detail(e)
            # [2026-08-24] 400 계열은 재시도해도 같은 답이 온다. 즉시 포기하고,
            #   무엇보다 서버가 준 메시지를 그대로 남긴다.
            if _is_permanent(e):
                log.error("%s: Claude 호출 실패 - 재시도해도 같은 오류 (%s). 이 배치 %d건 미처리. 상세: %s",
                          x_handle, type(e).__name__, len(tweets), detail)
                return []
            if attempt == 4:
                log.error("%s: Claude 호출 5회 모두 실패 (%s). 이 배치 %d건은 미처리로 남깁니다. 상세: %s",
                          x_handle, type(e).__name__, len(tweets), detail)
                return []
            wait = 2 ** attempt * 3          # 3, 6, 12, 24초
            log.warning("%s: Claude 호출 실패 (%s), %d초 후 재시도 (%d/5). 상세: %s",
                        x_handle, type(e).__name__, wait, attempt + 1, detail)
            time.sleep(wait)
    if getattr(resp, "stop_reason", None) == "max_tokens":
        log.error("%s: 응답이 max_tokens에서 잘렸습니다. 트윗 %d건이 누락될 수 있습니다.",
                  x_handle, len(tweets))
    for block in resp.content:
        if block.type == "tool_use" and block.name == "extract_event_announcements":
            raw_results = block.input.get("results") or []
            if not isinstance(raw_results, list):
                log.error("%s: results가 배열이 아닙니다 (%s). 이 배치 %d건 미처리.",
                          x_handle, type(raw_results).__name__, len(tweets))
                return []
            # [2026-08-13] 모델이 배열 안에 딕셔너리 대신 문자열을 하나 섞어 보내는 일이
            #   드물게 있다. 그대로 두면 build_curated_rows에서 TypeError가 나고
            #   계정 하나가 통째로 스킵된다 (EVERLINESHOP 366건, NCTsmtown 382건 등).
            #   여기서 걸러내면 나머지 정상 항목은 살고, 걸러진 트윗은
            #   결과에 안 담겼으므로 다음 실행에서 자동 재시도된다.
            results, bad = [], 0
            for r in raw_results:
                if isinstance(r, dict) and r.get("tweet_id"):
                    results.append(r)
                else:
                    bad += 1
            if bad:
                log.warning("%s: 형식이 어긋난 결과 %d건을 건너뜁니다 (예: %r)",
                            x_handle, bad,
                            next((r for r in raw_results
                                  if not (isinstance(r, dict) and r.get("tweet_id"))), None))
            return results
    log.warning("%s: 응답에 tool_use 블록이 없습니다. 트윗 %d건 결과 없음.", x_handle, len(tweets))
    return []


def build_curated_rows(x_handle, raw_by_tweet_id, extractions, recent_events, name_to_id, id_to_name, extracted_at):
    existing_keys = {e["event_key"]: e["event_group_id"] for e in recent_events if e["event_key"]}
    new_group_first_seen = {}  # event_key -> group_id (이번 배치에서 처음 만든 신규 그룹)
    rows = []

    for res in extractions:
        # [2026-08-13] 이중 방어. 여기서 터지면 계정 하나가 통째로 날아간다.
        if not isinstance(res, dict) or not res.get("tweet_id"):
            log.warning("%s: 형식이 어긋난 결과 항목을 건너뜁니다: %r", x_handle, res)
            continue

        raw = raw_by_tweet_id.get(res["tweet_id"])
        if raw is None:
            log.warning("알 수 없는 tweet_id가 결과에 포함됨: %s", res["tweet_id"])
            continue

        is_relevant = bool(res.get("is_relevant"))
        note = res.get("note")
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

        # [2026-08-10] seller_entity_id는 위 폴백으로 채워지는데 seller_name은 LLM이 뱉은
        #   원문 그대로라 NULL로 남는 경우가 있었다. 대시보드는 seller_name으로 판매처를
        #   묶기 때문에, 판매처가 특정됐는데도 "판매처 미상"으로 표시되는 문제가 있었다
        #   (2026-08-10 기준 14건: everlineshop, kqshop, musicart, fans_shop, weverseshop, applemusic).
        #   entity_master의 정식 명칭으로 채운다.
        seller_name = res.get("seller_name")
        if not (seller_name or "").strip() and seller_entity_id:
            seller_name = id_to_name.get(seller_entity_id)

        # [2026-08-12] 이 필터의 적용 범위를 "전체"로 넓히지 말 것.
        #   링크가 없으면 제외하는 규칙이라, 이미지만 붙은 진짜 판매 공지가 같이 죽는다.
        #   실제로 사람 라벨에서 정답 처리된 MusicKorea 응모 마감 리마인드가
        #   이미지 링크뿐이라 "링크없음"으로 걸린다. 판정은 프롬프트가 하고,
        #   이 필터는 판매처조차 못 잡은 잔여분만 정리하는 백스톱으로 남긴다.
        # [2026-08-10] 판매처가 끝내 미상인 게시물에만 비판매 필터를 적용한다.
        #   LLM이 "이벤트성 공지"를 넓게 잡아서 음원 발매 / 시상식·음악방송 투표 /
        #   방송 참여·인원체크 / 콘서트 공지까지 is_relevant=TRUE로 들어오는데,
        #   판매처까지 특정된 건은 그 자체로 유효한 신호이므로 건드리지 않는다.
        if is_relevant and not (seller_name or "").strip():
            _urls = extract_link_urls(raw.get("entities_json"), raw.get("ref_entities_json"))
            if looks_non_sales(_urls, raw["tweet_text"]):
                is_relevant = False
                _hosts = sorted({re.sub(r"^www\.", "", _HOST_RE.match(u).group(1).lower())
                                 for u in _urls if _HOST_RE.match(u)})
                note = f"[비판매 필터 {','.join(_hosts) if _hosts else '링크없음'}] {note or '-'}"

        # 그룹핑은 위 필터로 is_relevant가 확정된 뒤에 한다. 필터에 걸린 게시물이
        # 먼저 대표(is_representative=TRUE) 자리를 차지해버리면, 같은 이벤트의 진짜
        # 판매 공지가 대표 자리를 못 잡아 대시보드에서 통째로 사라진다.
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
            "seller_name": seller_name,
            "seller_entity_id": seller_entity_id,
            "event_name": res.get("event_name"),
            "event_key": event_key,
            "is_relevant": is_relevant,
            "confidence": res.get("confidence"),
            "extraction_note": note,
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
    failures = []   # (x_handle, 미처리 건수, 전체 건수)

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
                # [2026-08-12] 배치 단위로 예외를 가둔다. 예전엔 배치 하나가 죽으면
                #   그 계정 전체가 스킵돼, 트윗이 많은 판매처일수록 통째로 빠졌다.
                try:
                    results = call_claude(
                        client, x_handle, entity_type, known_artist_name,
                        artist_roster, recent_events, batch
                    )
                except Exception:
                    log.exception("%s: 배치 %d건 실패 - 미처리로 남기고 다음 배치 진행",
                                  x_handle, len(batch))
                    continue
                all_extractions.extend(results)

            extracted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            raw_by_tweet_id = {t["tweet_id"]: t for t in tweets}
            rows = build_curated_rows(
                x_handle, raw_by_tweet_id, all_extractions, recent_events, name_to_id, id_to_name, extracted_at
            )

            load_curated_rows(bq, rows)

            # [2026-08-12] 배치 전체를 무조건 완료 처리하면 안 된다.
            #   모델 응답이 잘리거나 일부 tweet_id가 빠져서 돌아오면, 그 트윗은
            #   결과가 없는데도 is_curated=TRUE가 찍혀 영원히 사라진다.
            #   실제로 결과가 돌아온 것만 완료 처리하고, 나머지는 다음 실행에서 다시 집는다.
            done_ids = {r["tweet_id"] for r in rows}
            missing = [t["tweet_id"] for t in tweets if t["tweet_id"] not in done_ids]
            if missing:
                failures.append((x_handle, len(missing), len(tweets)))
                log.warning("%s: %d/%d건이 결과에 없어 미처리로 남깁니다 (다음 실행에서 재시도)",
                            x_handle, len(missing), len(tweets))
            mark_curated(bq, list(done_ids))

            total_processed += len(tweets)
            total_relevant += sum(1 for r in rows if r["is_relevant"])
            log.info("%s: %d건 처리, %d건 유의미", x_handle, len(tweets), sum(1 for r in rows if r["is_relevant"]))
        except Exception:
            # 계정 하나 실패해도 나머지 계정은 계속 처리한다. 실패한 계정의 raw 행은
            # is_curated=FALSE로 남아있으므로 다음 실행에서 자동으로 재시도된다.
            failures.append((x_handle, len(tweets), len(tweets)))
            log.exception("%s 큐레이션 실패 - is_curated=FALSE로 남겨두고 다음 실행에서 재시도", x_handle)

    log.info("큐레이션 완료: %d개 계정 대상, %d건 처리, %d건 유의미한 이벤트로 추출",
              len(by_handle), total_processed, total_relevant)

    total_tweets = sum(len(v) for v in by_handle.values())
    total_missing = sum(m for _, m, _ in failures)
    miss_rate = (total_missing / total_tweets) if total_tweets else 0.0

    if failures:
        # [2026-08-24] 실패를 마지막에 한 번 더 모아 찍는다. 계정별 경고는 수백 줄 로그
        #   중간에 묻혀서, 실제로 아무도 눈치채지 못한 채 3일이 지났다.
        log.error("미처리 계정 %d개 / 트윗 %d건 (전체 %d건 중 %.1f%%):",
                  len(failures), total_missing, total_tweets, 100.0 * miss_rate)
        for handle, missing_n, total_n in sorted(failures, key=lambda x: -x[1]):
            log.error("  - %s: %d/%d건 미처리", handle, missing_n, total_n)

    if miss_rate > MISS_RATE_ALERT:
        raise RuntimeError(
            f"미처리율 {100.0 * miss_rate:.1f}%가 임계치 {100.0 * MISS_RATE_ALERT:.0f}%를 "
            f"넘었습니다 (미처리 {total_missing}/{total_tweets}건). 로그의 '미처리 계정' 목록과 "
            f"'상세:' 줄의 API 오류 메시지를 확인하세요."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("큐레이션 실행 중 처리되지 않은 예외 발생")
        sys.exit(1)
