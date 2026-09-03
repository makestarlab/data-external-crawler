#!/usr/bin/env python3
"""'글로벌 투어 현황' 엑셀의 분홍 셀에서 베뉴 수용인원 시드를 뽑는다.

왜 셀 색을 보나
---------------
'공연 규모' 열에는 성격이 다른 두 값이 섞여 있다. 시트 3행 범례가 그걸 말해준다.
  분홍 F4CCCC = 정확한 판매 좌석수를 몰라 참고용으로 적은 '공연장 Full capacity'
  무색        = 그 공연의 실제 판매 좌석수
베뉴 딕셔너리에 쓸 수 있는 건 앞엣것뿐이다. 뒤엣것을 섞으면 같은 공연장이
공연마다 다른 수용인원을 갖게 된다. 색을 잃으면 이 구분이 사라지므로
data_only=False 로 서식까지 읽는다.

정확 일치만 쓴다
----------------
부분 문자열 매칭은 쓰지 않는다. 2026-09-03 실측에서 'THAMMASAT STADIUM' 이
'AT&T STADIUM'(정규화 후 'T STADIUM')에 부분 일치해 65,000 이 붙었다.
실제 탐마삿은 2만대다. 틀린 값은 빈칸보다 나쁘다 - 빈칸은 사람이 채우지만
틀린 값은 그대로 믿는다.

사용:
  python venue_seed_from_excel.py <엑셀경로> [출력 CSV]
"""
import csv
import re
import sys
from collections import defaultdict

import openpyxl

PINK_SUFFIX = "F4CCCC"     # 시트 범례가 지정한 '공연장 Full capacity' 표시색
COL_COUNTRY, COL_CITY, COL_VENUE, COL_CAP = 8, 9, 10, 11
HEADER_ROW = 5


def norm_venue(s):
    """비교용 정규화. 표기 흔들림만 흡수하고 단어는 지우지 않는다.

    단어를 지우면(예: 관사 제거) 서로 다른 공연장이 같은 키가 된다.
    대소문자·괄호·구분기호만 정리한다.
    """
    s = (s or "").upper()
    s = re.sub(r"\(.*?\)", " ", s)          # (Hall A) 같은 부가 표기
    s = re.sub(r"[^A-Z0-9가-힣]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract(path):
    wb_fmt = openpyxl.load_workbook(path)                 # 서식용
    wb_val = openpyxl.load_workbook(path, data_only=True)  # 값용
    ws_f, ws_v = wb_fmt["글로벌 투어 현황"], wb_val["글로벌 투어 현황"]

    agg = defaultdict(lambda: {"caps": set(), "names": set(),
                               "cities": set(), "countries": set()})
    pink_rows = 0
    for r in range(HEADER_ROW + 1, ws_f.max_row + 1):
        venue = ws_v.cell(r, COL_VENUE).value
        cap = ws_v.cell(r, COL_CAP).value
        if not venue or not isinstance(cap, (int, float)) or cap <= 0:
            continue
        rgb = getattr(ws_f.cell(r, COL_CAP).fill.start_color, "rgb", None)
        if not (isinstance(rgb, str) and rgb.upper().endswith(PINK_SUFFIX)):
            continue
        pink_rows += 1
        key = norm_venue(venue)
        if not key:
            continue
        a = agg[key]
        a["caps"].add(int(cap))
        a["names"].add(str(venue).strip())
        if ws_v.cell(r, COL_CITY).value:
            a["cities"].add(str(ws_v.cell(r, COL_CITY).value).strip())
        if ws_v.cell(r, COL_COUNTRY).value:
            a["countries"].add(str(ws_v.cell(r, COL_COUNTRY).value).strip())

    rows = []
    for key, a in sorted(agg.items()):
        caps = sorted(a["caps"])
        rows.append({
            "venue_key": key,
            # 가장 긴 표기를 대표로. 축약형보다 정보가 많다.
            "venue_name": sorted(a["names"], key=lambda x: (-len(x), x))[0],
            "city": sorted(a["cities"])[0] if a["cities"] else "",
            "country": sorted(a["countries"])[0] if a["countries"] else "",
            # 충돌 시 최댓값. 작은 값은 그 공연의 부분 개방일 가능성이 높다.
            "capacity": caps[-1],
            "capacity_values": "|".join(str(c) for c in caps),
            # 2배 이상 벌어지면 사람이 봐야 한다. 부분 개방이 아니라 다른 공연장일 수 있다.
            "needs_review": len(caps) > 1 and caps[-1] >= caps[0] * 2,
            "source": "excel_pink_cell",
        })
    return rows, pink_rows


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    path = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "data/venue_seed.csv"
    rows, pink_rows = extract(path)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    conflicts = [r for r in rows if "|" in r["capacity_values"]]
    review = [r for r in rows if r["needs_review"]]
    print(f"{out} 저장")
    print(f"  분홍 셀 행 {pink_rows} -> 고유 베뉴 {len(rows)}")
    print(f"  값 충돌 {len(conflicts)}곳, 그중 2배 이상 벌어져 확인 필요 {len(review)}곳")
    for r in review:
        print(f"    {r['venue_name']} ({r['city']}) {r['capacity_values']}")


if __name__ == "__main__":
    main()
