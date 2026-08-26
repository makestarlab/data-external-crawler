<!--
  data-external-crawler/prompts/classification_rules.md
  판정 기준의 단일 출처. curate_events.py 가 실행 시 이 파일을 읽어
  SYSTEM_PROMPT 에 그대로 끼워 넣는다. 사람이 읽는 문서이자 프롬프트 본문이다.
  버전: rules-2026-08-12b
  근거: 2026-08-12 사람이 매긴 정답 라벨 60건(x_curation_labels). 60건 전부가 아래 3문 테스트로 설명된다.
-->

# 판정 기준

## 최근 이벤트 목록의 용도 — 먼저 못 박아 둔다

프롬프트에는 "이미 등록된 최근 이벤트 목록"이 함께 들어온다.
이건 **같은 이벤트의 반복 공지에 `event_key` 를 맞춰주기 위한 것이지, 통과의 근거가 아니다.**

목록에 있다는 이유로 통과시키지 마라. 그 목록은 과거 판정의 결과일 뿐이고
틀린 것이 섞여 있다 — 실제로 투어 MD, 콘서트 도시별 공지, 시상식, 팬미팅 VOD 가
"등록된 이벤트"로 들어가 있다. 목록을 근거로 삼으면 과거의 오판이 그대로 대물림된다.

순서는 항상 이렇다.
**① 아래 기준으로 통과 여부를 먼저 정한다 → ② 통과한 건에 한해 목록에서 `event_key` 를 찾는다.**
목록을 먼저 보고 판정하지 않는다.

## 먼저 볼 것 — 후속 트윗인가

판매처는 이벤트를 알린 뒤에 링크만 따로 올리는 습관이 있다.
`🔗 이벤트 상품 : https://…` 한 줄짜리 트윗이 그것이다. 이런 글은 본문만으로는
아래 세 질문에 답할 수 없지만 **엄연히 그 이벤트의 일부다.**

해당하는 유형 — 이벤트 링크만 붙인 후속 글, 응모 마감 D-DAY 리마인드,
특전 프리뷰 공개, 당첨자 발표, 일정 변경 안내.

**두 조건이 다 맞을 때만** 이 예외를 쓴다.

1. 본문이 링크나 짧은 안내뿐이라 그것만으로는 세 질문에 답할 수 없다
2. 최근 이벤트 목록이나 같은 배치의 다른 트윗에서 원 이벤트를 찾았고,
   **그 원 이벤트가 세 질문을 통과하는 유형이다** — 즉 특정 판매처가 특정 음반에
   붙여서 연 응모형 행사다

2번을 확인하지 않으면 목록의 오판이 그대로 따라온다.
투어 MD·콘서트·팬미팅 VOD 의 후속 글은 이 예외에 해당하지 않는다.
원 이벤트를 못 찾았거나 그것이 통과 유형이 아니면 제외한다.

찾았으면 `is_relevant=true`, 그 `event_key` 를 재사용하고
`artist_name` · `album_or_title` · `seller_name` 을 원 이벤트에서 가져와 채운다.

2026-08-12 평가에서 놓친 3건 중 2건이 이 유형이었다.

## 세 가지 질문 — 전부 YES 여야 통과

위 후속 트윗에 해당하지 않으면 아래로 판정한다.
이 셋 중 하나라도 NO 면 `is_relevant=false` 다.

1. **특정 음반에 붙어 있는가?** — 앨범·EP·싱글이 지목돼야 한다.
   굿즈·MD·의류·팝업 머치·투어 상품·팬클럽 상품은 음반이 아니다.
2. **판매처가 특정되는가?** — 어느 판매처에서 사는 건지 본문에 있어야 한다.
   "여러 판매처 링크 모음"은 특정된 게 아니다.
3. **구매자에게 응모나 특전이 주어지는가?** — 그 음반을 사면 얻는 것이 있어야 한다.
   팬사인회 응모권, 영상통화, 포토카드 특전, 선주문 혜택 같은 것.

세 질문을 통과한 게시물은 실제로 전부 같은 모양이었다 —
**특정 판매처가 특정 앨범에 붙여서 여는 응모형 행사, 또는 그 앨범의 예약판매.**

애매하면 제외한다. 낮은 확신으로 통과시키지 않는다.
2026-08-12 감사에서 모델이 통과시킨 30건 중 21건이 오탐이었다.
**놓치는 것보다 잘못 잡는 게 훨씬 많다.** 망설여지면 제외가 정답이다.

## 이벤트로 인정하는 것

- 팬사인회 — 대면 / 영상통화 / 1:1 포토 / 네컷 / 밈앤그릿 / 特典会
- 럭키드로우, 응모자 전원 특전, 당첨자 추첨 특전
- 음반 예약판매·선주문 — **판매처가 특정될 때만**
- 판매처 단독반·독점반, 판매처 한정 포토카드 (음반 구성품일 때)

**언어는 판정에 영향을 주지 않는다.** 중국어·일본어·영어 공지도 같은 기준으로 본다.
해외 판매처(WillMusic, hello82, Ktown4u 등)가 응모 기간과 함께 올린 음반 이벤트는
본문이 한국어가 아니어도 인정한다. 2026-08-12 평가에서 놓친 3건 중 1건이
`NEXZ 2nd Single Album [Mmchk] SPECIAL EVENT in TAIPEI`(중국어 RT)였다.

앨범 구성품이 굿즈 형태여도 **그 자체가 음반의 한 버전이면** 인정한다.
예: `ARIRANG (NORMAL x CALVIN KLEIN SLEEPWEAR)` 는 ARIRANG 앨범의 패키지 버전이다.
반대로 `BTS POP UP : ARIRANG Official Merch`(자켓·후디·인형)는 굿즈라 제외한다.
가르는 기준은 **음반 SKU 가 붙는가**다.

## 이벤트가 아닌 것

2026-08-12 오탐 21건에서 나온 유형이다. 여기 해당하면 제외한다.

| 유형 | 어긋난 질문 | 실제 오탐 사례 |
|---|---|---|
| 굿즈·MD·머치·의류 | 1 | Ktown4u 투어 MD, 소녀시대 19주년 MD, BLACKPINK 헤리티지 컬렉션, BTS 팝업 머치 |
| 팝업스토어 | 1 | 오픈·현장·관람 전부. TWICE 팝업스토어 DAY2 |
| 공연·투어 | 1 | 개최 안내, 회차 추가, 선예매, 플레이가이드 선행, 팬콘, 라이브뷰잉, 팬미팅 VOD, 전시 티켓(SFMOMA) |
| 팬클럽·멤버십 상품 | 1 | NMIXX NSWER 폴라로이드, 팬키트, 가입 특전, 부스 운영 |
| 잡지·화보집 | 1 | 더스타, ELLE, Maps, DICON |
| 아티스트 계정의 종합 프리오더 | 2 | 스키즈 `THIS & THAT` PRE-ORDER, WayV `Vision Wings` Pre-order. 판매처가 특정 안 되고 어차피 각 판매처가 따로 올린다 |
| 음원·스트리밍 프로모션 | 3 | pre-save, pre-add, 멜론 뮤직웨이브, 플레이리스트 커버, 발매 소식 |
| 판매처 운영 공지 | 3 | 입고 일정 변경, 여름 휴업, 배송 지연, 점검 |
| 재미성·참여형 이벤트 | 3 | 영수증 맞히기, 퀴즈, 틱톡 해시태그 캠페인, 챌린지 |
| 투표·시상식 | 3 | 엠카운트다운 사전투표, 뮤직뱅크, 아이돌챔프, Mnet Plus |
| 방송 출연·참여 | 3 | 출연 예고, 인원 체크, 음악방송 참여 안내 |
| 콘텐츠 | 3 | MV·트레일러 티저, 비하인드, 컨셉포토, 언박싱, 자체 예능, 일상 사진 |
| 비K-pop 상품 | 1 | 종합몰의 도서·잡화 등 |

## 리트윗

원본 계정 기준으로 판단한다. 아티스트 계정이 판매처 공지를 리트윗하면 그건 판매처의 이벤트다
(애플뮤직 팬사인회를 CRAVITY 계정이 RT → 인정). 판매처 계정이 아티스트 콘텐츠를
리트윗하면 이벤트가 아니다. 본문이 140자에서 잘리므로 원본 트윗의 entities 도 함께 본다.

## 투어 타이틀이 앨범명과 같을 때

`BTS WORLD TOUR 'ARIRANG'` 의 ARIRANG 은 앨범명과 같지만 그 게시물은 투어 공지다.
이벤트로도, 그 앨범의 이벤트로도 잡지 않는다.
같은 함정: `PUREFLOW`(르세라핌), `BLOOD SAGA`(엔하이픈), `NEO CITY`(NCT127), `RUN IT SEOUL`(스키즈).

## 링크가 판정에 미치는 영향

링크는 **보조 신호**다. 위 3문 테스트를 뒤집지 못한다.
본문 링크는 전부 `t.co` 라 그대로는 못 쓴다. 원 도메인은 `entities_json` 의
`unwound_url` / `expanded_url` 에 있다.

1. **링크가 하나도 없으면 제외한다.**
2. 경로에 `/shop` `/store` `/product` `/products` `/goods` `/order` `/cart` `/buy` `/item`
   이 있으면 판매 신호다. 도메인이 아래 비판매 목록에 있어도 이것만으로 제외하지 않는다.
3. 링크가 **전부** 비판매 도메인이면 제외한다.

**비판매 도메인** — 음원(melon·genie·music-flo·bugs·vibe·spotify·`*.lnk.to`·orcd·stationhead),
SNS·미디어(youtube·tiktok·instagram·pinterest·facebook·threads·naver 기사·docs.google·앱스토어·linktr.ee),
팬커뮤니티·투표앱(weverse.io·berriz.in·app.fans·mnetplus·fanca·fantheone·idolchamp·linc.fan·pypd·flybook),
티켓(ticketmaster·a-nation·liveviewing·hybejapan-concert·kcforum)

**비판매 목록에 절대 넣으면 안 되는 도메인** (실제 판매처다) —
yes24 · aladin · ktown4u · **weverseshop.io / shop.weverse.io** (weverse.io 와 다름) ·
everlineshop · withmuu · musickorea.asia · minirecord.store · soundwave · applemusic.co.kr ·
jumpupent · fanplee · musicndrama · musicart.kr · beatroad · dearmymuse · nymusickr ·
mubeatmall.shop · kpop2gether · higher.market · mnetplusmerch · tblshop · kmonstar · dailyduck · nemoz.shop

## 추출 규칙

| 필드 | 규칙 |
|---|---|
| `artist_name` | 로스터에 없어도 본문 그대로 채운다. 비우면 나중에 등록해도 소급이 안 된다 |
| `album_or_title` | **앨범/EP/싱글 타이틀만.** 투어명·팬미팅명·팝업명·MD명은 넣지 않는다 |
| `seller_name` | 본문에 명시된 판매처만. 없으면 비운다 — 추측 금지 |
| `event_type` | 팬사인회 / 영상통화 / 럭키드로우 / 응모특전 / 예약판매 / 단독한정 / 기타 |
| `event_name` | 이벤트 고유명. 타이틀과 섞지 않는다 |
| `event_key` | 같은 이벤트의 반복 공지를 묶는 키. **아래 `event_key 쪼개는 기준` 을 따른다** |
| `extraction_note` | 세 질문 중 어디서 걸렸는지 또는 왜 통과인지 한 줄. 감사할 때 이것만 남는다 |

### `event_key` 쪼개는 기준

`event_key` 는 **같은 회차·같은 형태의 공지만** 묶는다. 아래 중 하나라도 다르면 **다른 키**다.

| 다르면 키를 나눈다 | 예 |
|---|---|
| 참여 형태 | 대면 팬사인회 / 영상통화 / 럭키드로우 / 포토카드 응모 / 쇼케이스 초대 |
| 영상통화 방식 | 1:1 / 단체(그룹) / 7:1 |
| 회차·차수 | 1차 / 2차, 1회차 / 2회차 |
| 도시·장소 | 서울 / 부산, 온라인 / 오프라인 |
| 판매처 | 판매처가 다르면 무조건 다른 이벤트다 |

키 형식은 `아티스트_타이틀_판매처_형태[_방식][_회차]` 로 소문자 스네이크케이스.

```
classy_reboot_everline_fansign_offline
classy_reboot_everline_photocard_7to1
artms_hyperego_applemusic_videocall_group
artms_hyperego_applemusic_videocall_1to1
```

**같은 앨범·같은 판매처라도 형태가 다르면 별개 이벤트다.** 형태를 뭉뚱그린 키
(`..._fansign_videocall` 처럼 둘을 한 키에 넣은 것)를 쓰지 말 것. 2026-08-26에
이것 때문에 8개 그룹에 대표가 둘씩 생겨 대시보드에서 뒤엣것이 가려질 뻔했다
(클라씨 `대면/단체영통` vs `카페/7:1 포토`, ARTMS `단체영통` vs `1:1영통`).

반대로 **같은 이벤트를 며칠에 걸쳐 반복 공지**하는 것 — 마감 임박, 당첨자 안내,
`🔗` 링크만 붙은 후속 글 — 은 **같은 키**다. 날짜로 키를 나누지 말 것.

`album_or_title` 이 확실치 않으면 `external.musicbrainz_dim_title_full` 에서 `title` 로 조회해
`primary_type`(Album/EP/Single)과 `first_release_date` 가 있는지 확인한다.
`artist_display_name` 은 99.9%가 NULL 이니 아티스트명으로 찾지 말 것.

## 추적하지 않는 판매처

Makestar / 메이크스타(자사) · HYBE MERCH(투어 MD·팝업 위주) ·
아티스트/소속사 자체몰(ateez.kqent.com, xikers.kr, ygselect, starship-square, fncstore 등) ·
종합 커머스(알라딘·무신사·G마켓·올리브영)

여기 해당해도 **데이터는 남긴다.** 제외는 조회 시점에 한다.
