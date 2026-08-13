# 링크 도메인·키워드 신호 목록

2026-08-11 기준. `x_event_announcements` 대표 게시물 실측으로 도출했다.
도메인은 판정을 뒤집는 근거가 아니라 보조 신호라는 점을 먼저 기억할 것 —
자세한 건 SKILL.md의 "링크 도메인 신호" 절 참고.

## 목차

- [URL 추출 방법](#url-추출-방법)
- [비판매 도메인](#비판매-도메인)
- [구매 경로 패턴](#구매-경로-패턴)
- [판매 신호 키워드](#판매-신호-키워드)
- [넣으면 안 되는 도메인](#넣으면-안-되는-도메인)

## URL 추출 방법

본문의 링크는 전부 `t.co` 단축이라 도메인 판정에 쓸 수 없다.
원본 도메인은 `x_posts_raw.entities_json`의 `urls[].unwound_url`(리다이렉트 최종 목적지)
또는 `expanded_url`에만 들어있다.

리트윗은 본문이 140자에서 잘리므로 `referenced_tweet_id`로 원본 트윗의 entities도
함께 봐야 한다. 원본이 우리 수집 대상이 아니면 못 가져온다.

트윗 자체의 첨부 사진/영상 링크(`x.com/.../photo/1`)는 링크로 치지 않는다.

```sql
SELECT COALESCE(JSON_VALUE(u,'$.unwound_url'), JSON_VALUE(u,'$.expanded_url')) AS url
FROM `makestar-dw.makestar_ax.x_posts_raw`,
     UNNEST(JSON_EXTRACT_ARRAY(COALESCE(entities_json,'[]'),'$.urls')) u
WHERE NOT REGEXP_CONTAINS(url, r'^https?://(x|twitter)\.com/[^/]+/status/\d+/(photo|video)/')
```

## 비판매 도메인

### 음원·스트리밍

```
melon.com · m.melon.com · m2.melon.com · genie.co.kr · music-flo.com
music.bugs.co.kr · vibe.naver.com · open.spotify.com · music.youtube.com
*.lnk.to (stray-kids.lnk.to, nct127.lnk.to, aespa.lnk.to 등 전부)
lnk.to · orcd.co · stationhead.com
```

### SNS·미디어·앱스토어

```
youtube.com · youtu.be · tiktok.com · vt.tiktok.com · instagram.com
pinterest.com · entertain.naver.com · news.naver.com · facebook.com
threads.com · threads.net · x.com · twitter.com
docs.google.com · play.google.com · apps.apple.com · linktr.ee
```

### 팬커뮤니티·투표앱

판매처가 아니라 공지 채널이다. 위버스·베리즈 공지에 판매 정보가 실리는 경우가 있어
도메인만으로 거르면 진짜 이벤트가 함께 날아간다.

```
weverse.io (+ campaigns., jp-membership-benefits.) · berriz.in · link.berriz.in
app.fans · link.fans · mnetplus.world · artist.mnetplus.world · mnetplus.onelink.me
fanca.io · open.fanca.io · fantheone.com · idolchamp.com · promo-web.idolchamp.com
linc.fan · app.linc.fan · pypd.app · flybook.kr · dayoff.at
```

### 티켓·공연

```
ticketmaster.com · a-nation.net · api-liveviewing.com
hybejapan-concert.com · kcforum.co.kr
```

## 구매 경로 패턴

경로에 아래가 있으면 판매 신호로 본다. 도메인이 비판매 목록에 있어도
이 경로가 있으면 제외하지 않는다.

```
/shop  /store  /product  /products  /goods  /order  /cart  /buy  /item
```

## 판매 신호 키워드

본문에 아래가 하나라도 있으면 비판매 도메인이어도 유지한다.
"위버스 공지 안의 럭키드로우"를 살리기 위한 장치다.

```
판매 · 구매 · 예약 · 예판 · 선주문 · PRE-ORDER · 응모
팬사인회 · 사인회 · 영상통화 · 영통 · 럭키드로우 · LUCKY DRAW · 럭드
특전 · 特典 · MD · 굿즈 · MERCH · 입고 · 당첨 · 추첨
POP-UP · 팝업 · SPECIAL GIFT · FAN SIGN · MEET N GREET · VIDEO CALL
KIT · 키트 · 아카이브북 · ARCHIVING BOOK · PHOTO BOOK · 포토북 · 화보집
응원봉 · LIGHT STICK · 라이트스틱 · 이용권 · SOUND COIN · 시즌 그리팅 · STORE · 스토어
```

`特典`(일본어)을 빠뜨리면 일본 계정의 특전회(팬사인회) 공지를 놓친다.
`KIT`·`아카이브북` 같은 상품 명사가 없으면 `PLAVE BIRTHDAY KIT`,
`ZB1 ARCHIVING BOOK` 같은 실제 판매글이 걸러진다.

## 넣으면 안 되는 도메인

실제 판매처다. 비판매 목록에 들어가면 진짜 이벤트가 통째로 사라진다.

```
yes24.com · aladin.co.kr(도서는 종합몰이지만 음반 판매도 함) · ktown4u.com
weverseshop.io · shop.weverse.io  ← weverse.io 와 혼동 주의
everlineshop.com · withmuu.com · musickorea.asia · minirecord.store
soundwave.co.kr · applemusic.co.kr · jumpupent.com · fanplee.com
musicndrama.com · musicart.kr · beatroad.co.kr · dearmymuse.com
nymusickr.com · mubeatmall.shop · kpop2gether.com · higher.market
mnetplusmerch.com · tblshop.com · kmonstar.com · dailyduck.com · nemoz.shop
```
