#!/usr/bin/env python3
"""x_crawl_targets.json 무결성 검사.

2026-08-07 에 x_crawl_state MERGE 버그를 겪은 뒤로, 계정을 추가할 때
x_crawl_targets.json 과 entity_master 를 함께 갱신해야 한다. 손으로 하다 보니
2026-09-03 에 hello82PRESENTS(PROMOTER)가 hello82shop(SELLER)과 같은 entity_id 를
써서 한 엔티티에 타입이 둘 붙는 일이 있었다. 여기서 잡는다.
"""
import json
import sys
from collections import Counter, defaultdict

FAIL = []
def check(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

tg = json.load(open("x_crawl_targets.json", encoding="utf-8"))

print("[구성]")
counts = Counter(t["entity_type"] for t in tg)
print("  ", dict(counts), "총", len(tg))
check(len(tg) > 0, "비어 있지 않음")
check(set(counts) <= {"ARTIST", "SELLER", "PROMOTER"},
      f"알려진 entity_type 만 사용 (현재 {sorted(counts)})")

print("\n[필수 키]")
bad_keys = [t for t in tg if set(t) != {"x_handle", "entity_id", "entity_type"}]
check(not bad_keys, f"모든 항목이 x_handle/entity_id/entity_type 만 가짐 (위반 {len(bad_keys)})")
check(all(t.get("x_handle") and t.get("entity_id") for t in tg), "빈 값 없음")

print("\n[중복]")
handles = [t["x_handle"].lower() for t in tg]
dup_h = [h for h, n in Counter(handles).items() if n > 1]
check(not dup_h, f"핸들 중복 없음 (중복: {dup_h})")

# 한 엔티티가 여러 핸들을 갖는 건 정상이다 (Ktown4u_com / Ktown4u_main 처럼).
# 그러나 같은 entity_id 에 타입이 갈리면 entity_master 에서 어느 쪽이 맞는지
# 알 수 없게 된다. 이게 실제로 났던 사고다.
by_id = defaultdict(set)
for t in tg:
    by_id[t["entity_id"]].add(t["entity_type"])
conflict = {k: sorted(v) for k, v in by_id.items() if len(v) > 1}
check(not conflict, f"같은 entity_id 에 타입이 둘 이상인 경우 없음 (충돌: {conflict})")

print("\n[핸들 형식]")
import re
bad_fmt = [t["x_handle"] for t in tg if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", t["x_handle"])]
check(not bad_fmt, f"X 핸들 규칙(영숫자·밑줄 15자 이내) 준수 (위반: {bad_fmt})")

print()
if FAIL:
    print("실패:", FAIL)
    sys.exit(1)
print("전체 통과")
