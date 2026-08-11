# -*- coding: utf-8 -*-
"""dc_pieces.jsonl → 사람 검수 결과 문서(원문 / 현재 추출 / 제안 / 사유).

무과금·읽기전용. 판정 규칙은 446개 조각을 전부 읽고 세운 것이고,
규칙으로 못 잡는 건은 OVERRIDES 에 수동으로 박는다.

산출물이 셋이다 — **무엇을 담느냐가 갈린다**:
  · 위치 인자 `DST` — **원본**. 조각 본문을 1,500자까지 그대로 싣는다(약 600KB).
    수집한 바이트라 **git 밖**이다(ADR-0013 · `.gitignore`). 잃는 게 없는 건
    이 스크립트 + `data/raw/dc_thread` 로 $0 재생성되기 때문이다.
  · `--redacted PATH` — **편집본**. 커밋 대상. 담는 것은 스레드/조각 식별자 · 판정 ·
    D코드 · 현재값/제안값 · **≤15자 근거 스니펫**뿐이고, 원문 본문 블록과 제목 전문은
    담지 않는다. `data/product_registry.json` 이 캡션을 뺀 것과 같은 자리다.
  · `--json PATH` — D코드 카운트만. 재감사 diff 의 기준선(`evals/diff_audit.py`).
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
ROOT = _P(__file__).resolve().parents[1]
from slime_rag.linking import load_kb

# 편집본 스니펫 상한. 1급 규칙의 '근거 스니펫 ~15자'와 같은 눈금이다.
REDACTED_SNIPPET_MAX = 15

_ap = argparse.ArgumentParser(description=__doc__)
_ap.add_argument("src", type=Path, help="dump_dcinside_pieces.py 산출 jsonl")
_ap.add_argument("dst", type=Path, help="원본 검수 문서 출력 경로(gitignore 대상)")
_ap.add_argument("--redacted", type=Path, default=None,
                 help="편집본 출력 경로(커밋 대상 — 원문 본문 없음)")
_ap.add_argument("--json", dest="json_out", type=Path, default=None,
                 help="D코드 카운트 JSON 출력 경로")
_ap.add_argument("--gold", type=Path, default=None,
                 help="감사 골드 출력 경로(안정 키 = post_id + mentioned_product)")
_args = _ap.parse_args()

SRC = _args.src
DST = _args.dst

kb = load_kb()
KB_MARKETS = [m["market_word"] for m in kb.markets]

# ─────────────────────────────────────────────────────────────────────────────
# 1) 마켓 표기 사전
# ─────────────────────────────────────────────────────────────────────────────
# KB 초성(길이 2 이상) + 검수 중 실측된 미등재 초성 별칭
CHOSEONG_TO_MARKET: dict[str, str] = {}
for m in kb.markets:
    c = m.get("choseong") or ""
    if len(c) >= 2:
        CHOSEONG_TO_MARKET[c] = m["market_word"]
    for a in m.get("choseong_aliases") or []:
        if len(a) >= 2:
            CHOSEONG_TO_MARKET[a] = m["market_word"]

# 실측으로 확인한, KB 에 없는 초성 별칭 (본문에서 마켓 자리로 쓰임)
EXTRA_CHOSEONG = {
    "ㅅㅈㄴ": "지나",        # 슬지나 — KB aliases 에 '슬지나' 문자열은 있으나 초성은 없음
    "ㅅㄹㅇㅈㄴ": "지나",     # 슬라임지나(정식명)
    "ㅋㄹㅇ": "지나",        # 쿨라임(지나가 인수한 마켓명)
    "ㅂㅅㄹㅇ": "봄",        # 봄슬라임
}
CHOSEONG_TO_MARKET.update(EXTRA_CHOSEONG)

# KB 밖 마켓 — 아모스갤에서 실제로 마켓 자리에 쓰이는 초성. 등록 전엔 귀속 불가.
UNKNOWN_MARKET_TOKENS = {
    "ㅋㅋㅁ", "ㅍㄹㄹ", "ㅍㅍㄹ", "ㅅㄱㄷ", "ㅅㄹㅇㅂㄴ", "ㅇㄹ", "ㅇㅅㅈ", "ㅎㅆ",
    "ㅋㅈ", "ㅍㅅㅌㄹ", "ㅇㅍㅋ", "ㅁㅅㄹㅇ", "ㅅㄹㄹ", "ㅇㅌ", "ㅂㄹㅂ", "ㅌㅍ",
    "ㅃㅇ", "ㅍㅅ", "ㅁㄴ", "ㅋㅁㄹ", "ㅈㄹㅇ", "ㅍㅋ", "ㄸㅇ",
}

# 표면형(한글 이름). 일반명사와 겹치는 것은 '잡음'으로 따로 뺀다.
SURFACE_TO_MARKET = {}
for m in kb.markets:
    SURFACE_TO_MARKET[m["market_word"]] = m["market_word"]
    for a in m.get("aliases") or []:
        SURFACE_TO_MARKET[a] = m["market_word"]
# 일반어와 충돌해 단독으로는 마켓 근거가 못 되는 표면형/초성
NOISY_SURFACE = {"푸딩", "봄", "캐치", "지나", "머머"}   # 푸딩=질감어, 봄=계절/보다, 지나=지나다, 머머=ㅁㅁ 잡음
NOISY_CHOSEONG = {"ㅈㄴ"}                              # 존나

_JAMO_RUN = re.compile(r"[ㄱ-ㅎ]+")


def scan_markets(text: str):
    """텍스트가 마켓 자리로 쓴 토큰 → (확실 KB마켓 집합, 미등재마켓 집합, 잡음 토큰)."""
    safe, unknown, noisy = set(), set(), set()
    if not text:
        return safe, unknown, noisy
    for run in _JAMO_RUN.findall(text):
        if run in NOISY_CHOSEONG:
            noisy.add(run)
        elif run in CHOSEONG_TO_MARKET:
            safe.add(CHOSEONG_TO_MARKET[run])
        elif run in UNKNOWN_MARKET_TOKENS:
            unknown.add(run)
    for form, mw in SURFACE_TO_MARKET.items():
        if form in NOISY_SURFACE:
            if form in text:
                noisy.add(form)
            continue
        if form in text:
            safe.add(mw)
    return safe, unknown, noisy


# ─────────────────────────────────────────────────────────────────────────────
# 2) 비제품 어휘 (실측으로 모은 것만)
# ─────────────────────────────────────────────────────────────────────────────
TYPE_PARTS_WORDS = {   # 종류어·재료어·부속어
    "클리어", "퐁말", "디폼", "디폼류", "디폼생베", "생베", "빨대", "빨대슬", "빈백",
    "수수깡", "펄러비즈", "우드폼볼", "크런치", "크런키", "점섞슬", "돌슬라임",
    "크런치 슬라임", "파츠", "별디폼", "프링글스", "마카롱", "고양이", "디폼클리어",
    "지글리 구아검 들어간 것", "풀 조합 비슷한 생베", "바나나 빨대", "서비스 폼볼",
    "초코디폼", "글루올", "액괴",
}
META_WORDS = {         # 커뮤니티·메타어 (제품이 아님)
    "플레잉", "미감", "첫굼", "이번차수", "유슬이", "슬랑이", "슬랑이갤로", "슬켓",
    "빠삭이", "인수메뉴들", "디자인", "지비프", "존나좋다", "적구 가", "할로윈",
    "석기시대", "사해구", "빠코", "츄빵",
}
SALES_LABELS = {"비매", "비매품", "이번비매", "비매품 1번"}
LINE_WORDS = {"뭉치", "케이크", "허니", "빙하", "믹스", "불량", "유자", "크머",
              "레몬커드", "말차뭉", "비뭉", "숭덩자바", "잘자바", "막걸리", "갈배",
              "아생케", "유폭찜", "바토디", "마크시", "배", "자몽", "썸파", "물젤리",
              "진저", "맥플", "베플", "플레이크"}

# ─────────────────────────────────────────────────────────────────────────────
# 3) 1층/레지스트리 소유 인덱스
# ─────────────────────────────────────────────────────────────────────────────
pieces = [json.loads(l) for l in SRC.open(encoding="utf-8")]

spec_products_by_market = defaultdict(set)
all_spec_products = set()
for p in pieces:
    for r in p["rows"]:
        pass  # 소유 정보는 덤프에 이미 실려 있다

# specs 전체 목록은 덤프에 없으므로 DB 에서 한 번 더 읽는다(SELECT only).
from slime_rag.db import connect  # noqa: E402

conn = connect()
cur = conn.cursor()
cur.execute("SELECT market, product FROM specs WHERE product IS NOT NULL")
for mk, pr in cur.fetchall():
    spec_products_by_market[mk].add(pr)
    all_spec_products.add(pr)

reg = json.load(open(ROOT / "data/product_registry.json"))
reg_products_by_market = defaultdict(set)
for mw, mkt in reg["markets"].items():
    for cand in mkt.get("products", []) or []:
        n = cand.get("name") if isinstance(cand, dict) else cand
        if n:
            reg_products_by_market[mw].add(n)


def canon_candidates(name: str, market: str | None):
    """이름 → 정본 후보(부분일치 + 근사일치). (정본, 사유) 또는 (None, 사유).

    ⚠️ **마켓을 모르면 정본화하지 않는다.** 전 마켓을 한 풀로 보면 `진저브레드`(ㅋㅋㅁ 제품)가
    `진저브레드아이싱키트`(베이퍼)로 접히는 등, 이름 충돌이 그대로 오귀속이 된다 —
    D2c 와 같은 실패다. 마켓이 정해진 뒤에만 좁힌다.
    """
    if not name:
        return None, None
    if not market:
        return None, "마켓 미상이라 정본화 보류(전 마켓 풀에서 접으면 이름 충돌이 오귀속이 된다)"
    pool = spec_products_by_market.get(market, set()) | reg_products_by_market.get(market, set())
    if not pool:
        return None, None
    if name in pool:
        return None, None                       # 이미 정본
    subs = sorted({p for p in pool if name in p and name != p})
    if len(subs) == 1:
        cand = subs[0]
        # 뒤에 숫자만 붙는 건 **차수 구분**이라 사람이 골라야 한다(`오마카슬` vs `오마카슬3/5`).
        if re.fullmatch(re.escape(name) + r"\d+", cand):
            return None, f"차수 접미(`{cand}`)만 다름 — 어느 차수인지는 사람이 판단"
        return cand, "부분일치 1건"
    near = difflib.get_close_matches(name, sorted(pool), n=3, cutoff=0.86)
    near = [n for n in near if n != name]
    if len(near) == 1:
        return near[0], "철자 근사 1건"
    if subs:
        return None, f"부분일치 {len(subs)}건(모호)"
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# 4) 수동 판정 (규칙으로 못 잡는 건)
# ─────────────────────────────────────────────────────────────────────────────
OVERRIDES: dict[int, dict] = {
    # 근거 없는 마켓 배정 (본문·형제 조각 어디에도 '푸딩' 없음)
    1127: dict(market=None, code="D4", why="본문은 `ㄴㅈ 오마카슬3`. '푸딩' 근거가 본문·스레드 어디에도 없다 — 배치 안 형제 조각에서 새어든 값으로 보임. 늪지로 채우려면 `오마카슬` 을 1층에 넣고 역인덱스로 채울 것."),
    1128: dict(market=None, code="D4", why="같은 스레드의 근거 없는 `푸딩` 배정. 원문은 '난 만져봤다' 뿐이라 마켓·제품 모두 미상이 맞다."),
    1333: dict(market=None, code="D4", why="원문이 `ㅂ 할로윈 한정 펌킨치즈케이크` 로 **봄**을 명시했는데 푸딩이 붙었다. 봄으로 고치거나(권장) 최소한 NULL."),
    # 감정 축 오배치
    164: dict(code="D8", why="`유자 개개개객좋아` 는 총평이지 향 평가가 아니다 — `scent_sentiment=pos` 를 null 로. 이름도 `유자폭탄찜` 축약."),
    165: dict(code="D8", why="`빠코` 비교 문장에 `scent_sentiment=neg` 가 붙었다. 향 얘기가 없으므로 null."),
    # 제품명이 아닌 라벨/문장 조각
    54: dict(product=None, code="D5b", why="글 제목 `ㅂ 존나좋다` 의 감탄사를 제품명으로 들어올렸다. 본문의 실제 제품은 허니푸냥이·카피바라."),
    133: dict(product=None, code="D5b", why="`인생슬 적구 가`(적고 가) 의 어절을 제품명으로 잡았다. 실제 제품은 빠코볼."),
    850: dict(product=None, code="D5a", why="`ㄴㅈ 풀 조합 비슷한 생베` 는 종류 설명 문구다. 제품명 없음."),
    405: dict(product=None, code="D5a", why="`지글리 구아검 들어간 것` 은 종류 설명구."),
    1321: dict(product="갈배", code="D6", why="후보 나열 `뼈갈우 / 갈배 / 막걸리 / 망치뭉` 에서 `갈배` 가 `배` 로 잘렸다."),
    412: dict(product="뼈갈우", code="D6", why="`뼈갈우나 갈배` 의 조사 `나` 가 이름에 붙었다."),
    397: dict(product="간배괴물", market="늪지", code="D6", why="`갈배괴물` 은 `간배괴물`(늪지, spec 345) 오타. 같은 스레드 다른 행은 정본으로 잡혔다."),
    1139: dict(product="엉겁처치", code="D6", why="`엉겁처지` 오타 — 같은 스레드 ROW#1329·1138 은 `엉겁처치`(spec 4172)."),
    1200: dict(product="진저브레드", code="D6", why="`진저브래드` 오타."),
    1125: dict(product="와이풀그린티", code="D6", why="띄어쓰기 변형 `와이풀 그린티` → 정본은 `와이풀그린티`(spec 4237)."),
    954: dict(product="UFO머핀", market=None, code="D7", why="`ㅅㄱㄷ UFO머핀` 에서 마켓 초성이 이름에 남았다. ㅅㄱㄷ 는 KB 미등재라 마켓은 NULL 유지."),
    1111: dict(product="애플크림머핀", market=None, code="D7", why="`ㅍㅍㄹ 애플크림머핀` — 접두가 이름에 남았고, 마켓은 ㅍㅍㄹ(미등재)인데 스레드 상속으로 예찬이 붙었다."),
    1324: dict(product=None, code="D7", why="`ㅋㅈ(ㅁㅁ)` 는 마켓 표기지 제품명이 아니다. ㅋㅈ 미등재 · ㅁㅁ=머머."),
    1336: dict(product=None, code="D7", why="`구ㅍㄷ`(구 푸딩) 는 마켓 표기. 제품명 아님."),
    # 스레드 상속이 원문의 명시 마켓을 덮은 대표 사례(규칙이 잡지만 사유를 특정해 남긴다)
    421: dict(market="지나", code="D1", why="댓글 원문이 `ㅅㅈㄴ 헝잭버거` 로 마켓을 직접 말했는데 스레드(ㄴㅈ)가 덮었다. 1층 소유도 지나."),
    422: dict(market="베이퍼", code="D1", why="같은 댓글의 `ㅂㅇㅍ 건체리크럼블`. ㅂㅇㅍ 는 KB 에 있는 초성인데도 상속이 이겼다."),
    855: dict(market="지나", code="D1", why="댓글 전체가 `ㅅㅈㄴ 헝잭버거` 한 줄. 상속(늪지)이 원문을 덮었다."),
    851: dict(market="지나", code="D2", why="`요구르팅` 의 1층 소유는 지나. 늪지 스레드 상속이 덮었다."),
    420: dict(market="지나", code="D1", why="OP 본문이 `ㅅㅈㄴ 빠코볼은 표면이 기름져서` 로 명시. 빠코볼의 1층 소유도 지나."),
    1057: dict(market="지나", code="D1", why="댓글 원문 `ㅅㅈㄴ 뀰샐러드`. 1층 소유도 지나인데 베이퍼가 붙었다."),
    1056: dict(market="모모네", code="D1", why="댓글 원문 `ㅁㅁㄴ 아이스체리?디폼`. ㅁㅁㄴ 는 KB 초성인데 상속(베이퍼)이 이겼다."),
    832: dict(market="모모네", code="D1", why="OP 가 항목마다 마켓을 붙였다 — `ㅁㅁㄴ 로지가든`. 1층 소유도 모모네."),
    833: dict(market="봄", code="D1", why="OP 원문 `ㅂ 쿠앤크초코퍼지`. 1층 소유도 봄."),
    834: dict(market="모모네", code="D1", why="OP 원문 `ㅁㅁㄴ 블루베리요거보울`. 1층 소유도 모모네."),
    848: dict(market="진통제", code="D1", why="댓글 원문 `ㅈㅌㅈ에서 뚝딱보리굴비 샀는데`. ㅈㅌㅈ 는 KB 초성인데 스레드(ㅂㅇㅍ)가 덮었다."),
    # 미등재 마켓 때문에 통째로 비거나 잘못 붙은 스레드
    1219: dict(code="D3", why="스레드 전체가 `ㅋㅋㅁ` 마켓 후기인데 ㅋㅋㅁ 가 KB 에 없다. 제품이 1층에 있는 행만 우연히 채워지고 나머지는 NULL 로 남는다."),
    1190: dict(market=None, code="D2c", why="스레드는 `ㅋㅋㅁ` 후기인데 `진저크런키` 가 지나 1층에 있어 지나가 붙었다 — **이름 충돌**. 같은 글의 다른 제품은 전부 NULL 이라 한 스레드 안에서 마켓이 갈렸다."),
    317: dict(code="D3", why="스레드는 `ㅂㅅㄹㅇ`(봄슬라임) 후기. KB 초성이 `ㅂ` 뿐이라 ㅂㅅㄹㅇ 가 안 잡히고, 1층에 있는 제품만 봄으로 채워졌다(같은 글의 토마토라구파스타·모닝빠앙·허니넛츠시리얼은 NULL)."),
}

# ─────────────────────────────────────────────────────────────────────────────
# 5) 행 단위 판정
# ─────────────────────────────────────────────────────────────────────────────
CODE_LABEL = {
    "D1": "스레드 상속이 원문의 명시 마켓을 덮음",
    "D2": "1층 소유 마켓과 배정 마켓 불일치",
    "D2c": "제품명 충돌로 다른 마켓이 붙음",
    "D3": "KB 미등재 마켓(초성) → 귀속 불가",
    "D4": "근거 없는 마켓 배정",
    "D5a": "종류어·재료어·부속어를 제품명으로",
    "D5b": "커뮤니티·메타어를 제품명으로",
    "D5c": "판매형식 라벨을 제품명으로",
    "D6": "약칭·오타·조사 결합 → 정본 미매칭",
    "D7": "마켓 표기가 제품명에 남음",
    "D8": "감정 축 오배치",
    "D9": "같은 조각의 내용 없는 중복 행",
    "D10": "원문(body) 없음 — 대조 불가",
    "F1": "마켓 채울 수 있음(비어 있음)",
}

stats = Counter()
piece_out = []

for p in pieces:
    text = p["text"] or ""
    op = p.get("op") or {}
    op_text = f"{op.get('title') or ''}\n{op.get('body') or ''}"
    p_safe, p_unknown, p_noisy = scan_markets(text)
    t_safe, t_unknown, t_noisy = scan_markets(op_text)
    # ⚠️ 스레드가 '무슨 마켓 글인가'는 **제목**이 정한다(디시 관행: `ㅋㅋㅁ 첫굼 후기`).
    #    본문에 스쳐 지나간 타 마켓 언급까지 스레드 마켓으로 세면 한 번 언급된 `ㅇㄹ` 가
    #    그 글의 댓글 전부를 미등재 마켓으로 물들인다(실측 94행 오탐).
    _ts, title_unknown, _tn = scan_markets(op.get("title") or "")
    ctx_safe = p_safe | t_safe
    ctx_unknown = p_unknown | title_unknown

    # 같은 조각 안 중복(내용 없는 제품미상 행)
    fp = Counter()
    for r in p["rows"]:
        if r["product"] is None:
            key = (r["evidence"] or "", json.dumps(r["sent"], sort_keys=True))
            fp[key] += 1

    rows_out = []
    for r in p["rows"]:
        codes: list[str] = []
        notes: list[str] = []
        new_market = r["market"]
        new_product = r["product"]

        cur_m, cur_p = r["market"], r["product"]
        own_spec = r.get("owner_spec") or []
        own_reg = r.get("owner_registry") or []

        # ---- 원문 없음
        if not text:
            codes.append("D10")
            notes.append("`reviews.body` 가 NULL 이라 원문 대조가 안 된다(색인 초기 8행). 원문 재수집 전엔 검증 불가.")

        # ---- 제품명 위생
        if cur_p:
            name = cur_p.strip()
            if name in SALES_LABELS:
                codes.append("D5c"); new_product = None
                notes.append(f"`{name}` 은 판매 형식 라벨이지 제품명이 아니다.")
            elif name in TYPE_PARTS_WORDS:
                codes.append("D5a"); new_product = None
                notes.append(f"`{name}` 은 종류/재료/부속어다. 제품명 자리를 비우되 후기 항목은 남긴다(마켓 축 CS·배송은 유효).")
            elif name in META_WORDS:
                codes.append("D5b"); new_product = None
                notes.append(f"`{name}` 은 커뮤니티·메타 표현이지 제품이 아니다.")
            elif name in LINE_WORDS:
                codes.append("D6")
                canon, why = canon_candidates(name, new_market or (own_spec[0] if len(own_spec) == 1 else None))
                if canon:
                    new_product = canon
                    notes.append(f"`{name}` 은 축약 지칭 → `{canon}` ({why}).")
                else:
                    notes.append(f"`{name}` 은 제품 라인/축약 지칭이다({why or '정본 후보 없음'}). "
                                 "정본이 하나로 안 좁혀지면 채우지 말고 별칭 사전 대상으로 남긴다.")
            else:
                canon, why = canon_candidates(name, new_market or (own_spec[0] if len(own_spec) == 1 else None))
                if canon:
                    codes.append("D6"); new_product = canon
                    notes.append(f"`{name}` → `{canon}` ({why}).")
                elif why:
                    notes.append(f"`{name}` 은 1층 미등재({why}) — 사람 확인 필요.")

        # ---- 마켓 위생
        explicit = None
        if len(p_safe) == 1:
            explicit = next(iter(p_safe))
        if explicit and cur_m and cur_m != explicit:
            codes.append("D1"); new_market = explicit
            notes.append(f"이 조각 원문이 마켓을 **한 곳만** 지목한다(`{explicit}`). 현재 값 `{cur_m}` 은 스레드 상속으로 보인다.")
        elif len(own_spec) == 1 and cur_m and cur_m != own_spec[0]:
            codes.append("D2"); new_market = own_spec[0]
            notes.append(f"`{cur_p}` 의 1층 소유는 `{own_spec[0]}` 인데 `{cur_m}` 이 붙었다.")
        elif cur_m is None:
            if explicit:
                codes.append("F1"); new_market = explicit
                notes.append(f"원문이 `{explicit}` 을 직접 말하는데 비어 있다.")
            elif len(own_spec) == 1:
                codes.append("F1"); new_market = own_spec[0]
                notes.append(f"1층에서 `{cur_p}` 는 `{own_spec[0]}` 단독 소유 → 역인덱스로 채울 수 있다.")
            elif len(own_reg) == 1:
                codes.append("F1"); new_market = own_reg[0]
                notes.append(f"레지스트리에서 `{cur_p}` 는 `{own_reg[0]}` 단독 소유 → 사람 검수 후 채울 수 있다(확신도 0.65).")
            elif ctx_unknown:
                codes.append("D3")
                notes.append(f"원문이 KB 미등재 마켓 `{'·'.join(sorted(ctx_unknown))}` 을 가리킨다. KB 에 넣기 전엔 NULL 이 맞다.")
        if cur_m and ctx_unknown and not p_safe and not own_spec:
            if "D3" not in codes:
                codes.append("D3")
                notes.append(f"원문이 가리키는 마켓은 미등재 `{'·'.join(sorted(ctx_unknown))}` 인데 `{cur_m}` 이 상속됐다.")

        # ---- 초성 잡음 경고
        if p_noisy:
            notes.append(f"⚠️ 초성/표면형 잡음 검출: `{'·'.join(sorted(p_noisy))}` (ㅈㄴ=존나 · 푸딩=질감어 · 봄=계절 · 지나=지나다). 이 토큰은 마켓 근거로 쓰면 안 된다.")

        # ---- 감정 축
        ev = r["evidence"] or ""
        msnd = re.search(r"소리: ([^/]*)", ev)
        if msnd and re.search(r"탈출|날라", msnd.group(1)):
            codes.append("D8")
            notes.append("`소리` 축에 **탈출**(비즈·폼이 빠져나감)이 들어갔다. "
                         "탈출은 지속력/질감 사안이지 소리가 아니다.")

        # ---- 중복 행
        if r["product"] is None:
            key = (r["evidence"] or "", json.dumps(r["sent"], sort_keys=True))
            if fp[key] > 1:
                codes.append("D9")
                notes.append(f"같은 조각에서 내용이 글자까지 동일한 행이 {fp[key]}개 — 접어야 한다.")

        # ---- 수동 판정 우선
        ov = OVERRIDES.get(r["id"])
        if ov:
            if "market" in ov:
                new_market = ov["market"]
            if "product" in ov:
                new_product = ov["product"]
            codes = [ov["code"]] + [c for c in codes if c != ov["code"]]
            notes = [ov["why"]] + notes

        changed = (new_market != cur_m) or (new_product != cur_p)
        if codes:
            for c in codes:
                stats[c] += 1
        status = "🔧 수정" if changed else ("⚠️ 지적" if codes else "✅ 유지")
        stats["_" + status] += 1

        rows_out.append(dict(row=r, codes=codes, notes=notes, status=status,
                             new_market=new_market, new_product=new_product))

    piece_out.append((p, rows_out, dict(p_safe=p_safe, p_unknown=p_unknown,
                                        p_noisy=p_noisy, ctx_safe=ctx_safe)))

# ─────────────────────────────────────────────────────────────────────────────
# 6) 출력
# ─────────────────────────────────────────────────────────────────────────────
n_rows = sum(len(r) for _p, r, _s in piece_out)
buf: list[str] = []
w = buf.append

w("# 디시(아모스갤) 추출 결과 전수 검수")
w("")
w(f"- 대상: `reviews.source='amos'` **{n_rows}행 / 조각 {len(piece_out)}개 / 스레드 172개** (2026-08-10 기준)")
w("- 방법: 원문(`data/raw/dc_thread` 파싱본 + `reviews.body`) ↔ 추출행을 조각 단위로 1:1 대조. LLM·HTTP 0회.")
w("- 각 행은 `현재` → `제안` → `사유` 로 적었다. 규칙으로 못 잡는 건은 손으로 판정했다.")
w("")
w("## 결함 분류와 규모")
w("")
w("| 코드 | 결함 | 해당 행 |")
w("|---|---|---|")
for code, label in CODE_LABEL.items():
    if stats[code]:
        w(f"| `{code}` | {label} | {stats[code]} |")
w("")
w(f"판정: 🔧 수정 {stats['_🔧 수정']} · ⚠️ 지적(값은 유지) {stats['_⚠️ 지적']} · ✅ 유지 {stats['_✅ 유지']}")
w("")
w("## 원인 — 왜 이렇게 됐나")
w("")
w("### ① 마켓 귀속의 **우선순위가 거꾸로** 서 있다 (`D1` 19행)")
w("")
w("디시 글은 제목이 마켓을 정하고(`ㅂㅇㅍ 후기`), 댓글은 **다른 마켓 얘기를 자유롭게 한다**.")
w("그런데 지금은 스레드에서 정해진 마켓이 조각별 명시 언급을 덮는다. 실측 사례:")
w("")
w("- `ㅈㅌㅈ에서 뚝딱보리굴비 샀는데` → `베이퍼` (ROW#848). ㅈㅌㅈ 는 **KB 에 있는 초성**이다.")
w("- `ㅅㅈㄴ 헝잭버거 , ㅂㅇㅍ 건체리크럼블` 한 댓글 → 둘 다 `늪지` (ROW#421·422).")
w("- 항목마다 마켓을 붙인 글(`ㅁㅁㄴ 로지가든` / `ㅂ 쿠앤크초코퍼지` / `ㅁㅁㄴ 블루베리요거보울`)")
w("  → 세 행 모두 `베이퍼` (ROW#832~834).")
w("")
w("고칠 것: **조각 내 명시 마켓 > 1층 제품 소유 > 스레드 상속** 순서를 코드로 못박고,")
w("상속으로 채운 값에는 직접 매칭과 **다른 확신도**를 남길 것. 지금은 상속도 0.85, 직접 초성 매칭도")
w("0.85 라 무엇이 상속인지 사후에 구분할 수 없다 — 되돌릴 수가 없다는 뜻이다.")
w("")
w("### ② 스레드 하나의 마켓이 **댓글 전부에 도장 찍힌다** (`D2` 18행)")
w("")
w("스레드 200743(`인생슬 적구 가` — 제목에 마켓 없음)에서 한 댓글의 `ㅂㅉ 달토끼` 하나가")
w("그 글 댓글 **20행 전부**를 `빈짱` 으로 만들었다. 실제로는 `ㅂㅇㅍ`·`ㅍㅅㅌㄹ`·`ㅅㄹㄹ`·`ㅇㅉ`·")
w("`ㅇㅇㅈ`·`ㅇㅊ` 가 섞인 '내 인생슬 나열' 글이고, 1층 소유는 베이퍼·웨이즈·봄·연찌로 갈린다.")
w("")
w("고칠 것: 마켓 상속의 단위는 **스레드가 아니라 조각**이어야 한다. 제목이 마켓을 선언한 글에서만")
w("상속하고, 제목에 마켓이 없으면 상속하지 말 것.")
w("")
w("### ③ KB 에 없는 마켓이 코퍼스의 큰 덩어리다 (`D3` 105행)")
w("")
w("아모스갤에서 마켓 자리에 실제로 쓰이는데 KB 에 없는 초성:")
w("")
w("| 초성 | 등장 | 지금 벌어지는 일 |")
w("|---|---|---|")
w("| `ㅋㅋㅁ` | 47행 | 스레드 통째로 `market IS NULL`. 그런데 `진저크런키` 처럼 이름이 지나 1층과 겹치는 제품만 **지나로 잘못 채워져서**, 한 글 안에서 마켓이 갈린다(ROW#1190). |")
w("| `ㅍㄹㄹ` | 16행 | 17개 제품 후기 한 글이 통째로 NULL. |")
w("| `ㅎㅆ` | 9행 | 첫 슬라임 후기 한 글이 통째로 NULL. |")
w("| `ㅇㄹ`·`ㅇㅅㅈ`·`ㅅㄱㄷ`·`ㅋㅈ`·`ㅈㄹㅇ` 등 | 나머지 | 동일 |")
w("")
w("그리고 **KB 에 있는데 초성이 안 잡히는** 것도 둘 있다 — 이건 KB 한 줄로 끝난다:")
w("")
w("- `ㅅㅈㄴ`·`ㅅㄹㅇㅈㄴ`·`ㅋㄹㅇ` → 지나. `aliases` 에 `슬지나`·`쿨라임` 문자열은 있는데")
w("  `choseong_aliases` 가 비어 있어 초성으로는 안 걸린다. 아모스갤은 초성으로 쓴다.")
w("- `ㅂㅅㄹㅇ` → 봄. `choseong` 이 `ㅂ` 하나뿐이라 정식명 초성이 안 잡힌다.")
w("")
w("실측 효과: 스레드 143610(`ㅋㄹㅇ 몰아서 쓰는 훅이`, 38행)은 제품이 1층에 있는 행만 지나로")
w("채워지고 나머지는 NULL 이다. **같은 글, 같은 마켓인데 행마다 마켓이 있고 없다.**")
w("")
w("### ④ 초성·표면형이 일반어와 충돌한다 (`D4` 3행 + 전 구간 잡음)")
w("")
w("- `ㅈㄴ` = **존나**. KB 에서 지나로 유일 해석된다. 이 문서의 조각 요약에 `잡음` 으로 표시했다.")
w("- `푸딩` = 질감어(`젤리나 푸딩같은 느낌`). 마켓 `푸딩` 으로 잡힌다.")
w("- `봄` = 계절/`보다`, `지나` = `지나면`.")
w("")
w("근거 없이 마켓이 붙은 3행(ROW#1127·1128·1333)은 본문·제목 어디에도 `푸딩` 이 없는데 `푸딩` 이")
w("붙었다 — 배치 안 형제 조각에서 새어든 값으로 보인다.")
w("")
w("고칠 것: 이 토큰들은 **단독으로는 마켓 근거가 될 수 없게** 막고(뒤에 제품명이 붙을 때만 인정),")
w("추출 배치가 조각 경계를 넘겨 값을 섞지 않는지 확인할 것.")
w("")
w("### ⑤ 제품명 게이트가 디시 쪽엔 사실상 없다 (`D5a` 22행 · `D5b` 25행 · `D7` 4행)")
w("")
w("종류어(`클리어`·`퐁말`·`디폼류`·`펄러비즈`·`수수깡`), 부속어(`파츠`·`프링글스`),")
w("메타어(`플레잉`·`첫굼`·`미감`·`이번차수`·`슬랑이`·`유슬이`·`슬켓`), 심지어 글 제목의")
w("감탄사(`존나좋다`)와 어절(`적구 가`)까지 제품명 자리에 들어왔다.")
w("마켓 접두가 이름에 남은 것도 있다(`ㅅㄱㄷ UFO머핀`·`ㅍㅍㄹ 애플크림머핀`·`ㅋㅈ(ㅁㅁ)`·`구ㅍㄷ`).")
w("")
w("고칠 것: 인스타 쪽 해시태그 게이트에 대응하는 **어휘 게이트**를 디시에도 둘 것.")
w("`extract.is_non_product_label` 의 '맨몸 라벨 / 수식된 라벨' 2분법을 그대로 확장하면 된다 —")
w("맨몸 종류어·메타어는 무조건 비제품, 수식된 것(`초코디폼`)은 1층·레지스트리가 모를 때만 비제품.")
w("")
w("### ⑥ 약칭·오타가 정본으로 안 접힌다 (`D6` 58행)")
w("")
w("`갈배괴물`(간배괴물 오타) · `엉겁처지`(엉겁처치) · `진저브래드` · `뼈갈우나`(조사 결합) ·")
w("`와이풀 그린티`(띄어쓰기) · `배`(`갈배` 절단) 같은 것들이다.")
w("**단, 마켓을 모르면 접으면 안 된다** — 이 문서를 만들면서 전 마켓을 한 풀로 놓고 접었더니")
w("`진저브레드`(ㅋㅋㅁ 제품)가 `진저브레드아이싱키트`(베이퍼)로 접혔다. ③의 이름 충돌과 같은 실패다.")
w("그래서 여기서는 **마켓이 정해진 행만** 정본 후보를 제안하고 나머지는 보류로 뒀다.")
w("")
w("### ⑦ 감정 축 오배치 (`D8` 6행)")
w("")
w("`소리` 축에 **탈출**이 들어간 행들이다(`비즈 탈출`·`후두둑 날라감`). 탈출은 지속력/질감 사안이다.")
w("`빠코`·`유자` 두 행은 총평 문장이 `향` 축으로 갔다(ROW#164·165).")
w("")
w("### ⑧ 원문이 없는 8행 (`D10`)")
w("")
w("`reviews.body` 가 NULL 이라 대조 자체가 안 된다. `e930471` 이전에 색인된 행이고, 색인이")
w("`ON CONFLICT DO NOTHING` 이라 재수집으로는 안 채워진다 — 명시 백필이 필요하다.")
w("")
w("---")
w("")
w("## 먼저 손댈 순서 (효과/비용)")
w("")
w("1. **KB 초성 별칭 4개 추가** (`ㅅㅈㄴ`·`ㅅㄹㅇㅈㄴ`·`ㅋㄹㅇ`→지나, `ㅂㅅㄹㅇ`→봄) — 파일 한 줄씩, LLM 0회.")
w("2. **우선순위 고정**(조각 명시 > 1층 소유 > 스레드 상속) + 상속 전용 확신도 — 재추출 없이 백필로 처리 가능.")
w("3. **미등재 마켓 등록**(ㅋㅋㅁ·ㅍㄹㄹ·ㅎㅆ 부터) — 105행이 여기 걸려 있다.")
w(f"4. **디시 제품명 어휘 게이트** — {stats['D5a'] + stats['D5b'] + stats['D7']}행(D5a+D5b+D7). "
  "이름만 비우고 후기 항목은 남긴다.")
w("5. `ㅈㄴ`·`푸딩`·`봄`·`지나` 단독 매칭 차단.")
w(f"6. 약칭 사전(D6 {stats['D6']}행)과 감정 축(D8 {stats['D8']}행)은 그 다음.")
w("")
w("---")
w("")
w("## 행 단위 검수표")
w("")

last_thread = None
for p, rows, sig in piece_out:
    if p["thread_no"] != last_thread:
        op = p.get("op") or {}
        w("")
        w(f"### 스레드 {p['thread_no'] or '(스레드 번호 없음 · 초기 고정 데이터)'} — "
          f"{op.get('title') or '(원문 제목 없음 — 저장소에 스레드 HTML 없음)'}")
        w("")
        last_thread = p["thread_no"]
    kind = "본문" if p["kind"] == "post" else f"댓글 {p['comment_no']}"
    w(f"<details>")
    w(f"<summary><b>{kind}</b> · {p['author'] or '?'} · {len(rows)}행</summary>")
    w("")
    body = (p["text"] or "(원문 없음)").strip()
    if len(body) > 1500:
        body = body[:1500] + " …(생략)"
    w("**원문**")
    w("")
    w("```")
    w(body)
    w("```")
    w("")
    if sig["p_safe"] or sig["p_unknown"] or sig["p_noisy"]:
        w(f"조각이 지목한 마켓: KB `{'·'.join(sorted(sig['p_safe'])) or '-'}` · "
          f"미등재 `{'·'.join(sorted(sig['p_unknown'])) or '-'}` · "
          f"잡음 `{'·'.join(sorted(sig['p_noisy'])) or '-'}`")
        w("")
    for ro in rows:
        r = ro["row"]
        w(f"**{ro['status']} ROW#{r['id']}**")
        w("")
        w(f"- 현재: market=`{r['market']}`({r['market_confidence']}) · product=`{r['product']}` · "
          f"spec_id=`{r['spec_id']}` · 감정 `{r['sent']}`")
        w(f"- 근거: `{r['evidence']}`")
        if ro["status"] == "🔧 수정":
            w(f"- **제안: market=`{ro['new_market']}` · product=`{ro['new_product']}`**")
        codes = ro["codes"]
        if codes:
            w(f"- 사유({', '.join(f'`{c}` {CODE_LABEL.get(c, c)}' for c in codes)}):")
            for n in ro["notes"]:
                w(f"  - {n}")
        elif ro["notes"]:
            for n in ro["notes"]:
                w(f"- {n}")
        w("")
    w("</details>")
    w("")

DST.write_text("\n".join(buf), encoding="utf-8")
print(DST, DST.stat().st_size, "bytes")
print(dict(stats))

# ─────────────────────────────────────────────────────────────────────────────
# 7) 편집본 / 카운트 / 골드 — 커밋되는 산출물은 여기서만 나온다
# ─────────────────────────────────────────────────────────────────────────────
_BACKTICK_RE = re.compile(r"`([^`]*)`")


def redact(text: str) -> str:
    """백틱 스팬을 `REDACTED_SNIPPET_MAX` 자로 자른다 — 편집본의 유일한 인용 통로.

    왜 '자르는 함수'인가: 사유 문장 안에 원문 어절이 백틱으로 인용돼 있고, 그 길이는
    사람이 손으로 쓴 `OVERRIDES` 마다 다르다. 상한을 **검사**로만 두면 문장을 고칠 때마다
    게이트가 깨지고, 결국 상한이 문장에 맞춰 올라간다. 그래서 검사가 아니라 **변환**이다.
    """
    def cut(m):
        inner = m.group(1)
        if len(inner) > REDACTED_SNIPPET_MAX:
            inner = inner[:REDACTED_SNIPPET_MAX - 1] + "…"
        return f"`{inner}`"
    return _BACKTICK_RE.sub(cut, text)


def _row_key(piece, row) -> str:
    """감사 골드·재감사 diff 의 **안정 키**.

    ⛔ `reviews.id` 로 잡으면 안 된다 — Phase 4·6 의 접기(fold)가 행을 지운다.
    ⛔ `reviews.evidence` 로 잡으면 안 된다 — `index.render_review` 가 마켓을 evidence 에
      굽고, 마켓이 바뀌면 재렌더가 **강제된다**(`test_market_change_always_re_renders…`).
      즉 이 계획이 성공하는 만큼 evidence 가 바뀌어 골드가 자기를 파괴한다.
    채택 키는 `attributes->>'mentioned_product'` 다 — 801/801행에 존재하고,
    `repair_dc_attribution` 이 `attributes` 를 **일부러 안 고치므로** 불변이다.
    """
    return f"{piece['post_id']}␟{row.get('mentioned_product') or ''}"


if _args.redacted:
    rb: list[str] = []
    rw = rb.append
    rw("# 디시(아모스갤) 추출 결과 전수 검수 — **편집본**")
    rw("")
    rw("> 원문 본문을 담지 않는다(ADR-0013). 담는 것은 식별자 · 판정 · D코드 · 현재값/제안값 ·")
    rw(f"> ≤{REDACTED_SNIPPET_MAX}자 근거 스니펫뿐이다. 원본은 `.gitignore` 대상이고,")
    rw("> 이 스크립트와 `data/raw/dc_thread` 로 $0 재생성된다.")
    rw("")
    rw(f"- 대상: `reviews.source='amos'` **{n_rows}행 / 조각 {len(piece_out)}개**")
    rw("")
    rw("| 코드 | 결함 | 해당 행 |")
    rw("|---|---|---|")
    for code, label in CODE_LABEL.items():
        if stats[code]:
            rw(f"| `{code}` | {label} | {stats[code]} |")
    rw("")
    rw(f"판정: 🔧 수정 {stats['_🔧 수정']} · ⚠️ 지적 {stats['_⚠️ 지적']} · ✅ 유지 {stats['_✅ 유지']}")
    rw("")
    rw("## 행 단위 검수표(편집본)")
    rw("")
    rw("| 조각 | 행 | 판정 | 코드 | 현재 market/product | 제안 market/product | 근거(발췌) |")
    rw("|---|---|---|---|---|---|---|")
    for p, rows, _sig in piece_out:
        kind = "본문" if p["kind"] == "post" else f"댓글{p['comment_no']}"
        for ro in rows:
            r = ro["row"]
            if not ro["codes"]:
                continue                      # ✅ 유지 행은 편집본에서 표로만 세고 열거하지 않는다
            ev = (r["evidence"] or "").replace("|", "／").replace("\n", " ")
            ev = ev[:REDACTED_SNIPPET_MAX - 1] + "…" if len(ev) > REDACTED_SNIPPET_MAX else ev
            rw(f"| {p['thread_no'] or '-'}·{kind} | {r['id']} | {ro['status']} | "
               f"{' '.join(ro['codes'])} | `{r['market']}`/`{r['product']}` | "
               f"`{ro['new_market']}`/`{ro['new_product']}` | `{ev}` |")
    rw("")
    _args.redacted.write_text(redact("\n".join(rb)), encoding="utf-8")
    print(_args.redacted, _args.redacted.stat().st_size, "bytes (redacted)")

if _args.json_out:
    _args.json_out.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in stats.items()}
    payload["_rows"] = n_rows
    payload["_pieces"] = len(piece_out)
    # 재감사 diff 의 하드 입력 — 코드별 **키 집합**(행 수가 아니다).
    # 접기가 분모를 줄이므로 "22 → 0" 같은 행 수 비교는 성공 기준이 될 수 없다(§10.1).
    by_code: dict[str, list[str]] = {}
    for p, rows, _sig in piece_out:
        for ro in rows:
            for c in ro["codes"]:
                by_code.setdefault(c, []).append(_row_key(p, ro["row"]))
    payload["keys_by_code"] = {c: sorted(set(v)) for c, v in sorted(by_code.items())}
    payload["keys_kept"] = sorted({_row_key(p, ro["row"])
                                   for p, rows, _s in piece_out for ro in rows
                                   if ro["status"] == "✅ 유지"})
    _args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(_args.json_out, "(json)")

if _args.gold:
    entries = []
    for p, rows, _sig in piece_out:
        for ro in rows:
            if not ro["codes"]:
                continue
            r = ro["row"]
            entries.append({
                "key": _row_key(p, r),
                "post_id": p["post_id"],
                "mentioned_product": r.get("mentioned_product"),
                "thread_no": p["thread_no"],
                "kind": p["kind"],
                "status": ro["status"],
                "codes": ro["codes"],
                "current": {"market": r["market"], "product": r["product"],
                            "market_confidence": r["market_confidence"]},
                "proposed": {"market": ro["new_market"], "product": ro["new_product"]},
            })
    _args.gold.parent.mkdir(parents=True, exist_ok=True)
    _args.gold.write_text(json.dumps({
        "_note": ("아모스갤 추출 전수 검수(사람) 오라클. 키는 (post_id, mentioned_product) — "
                  "reviews.id 는 접기가 지우고 evidence 는 마켓 변경이 재렌더한다. "
                  "원문 바이트 없음(ADR-0013)."),
        "_key_fields": ["post_id", "mentioned_product"],
        "_counts": {c: stats[c] for c in CODE_LABEL if stats[c]},
        "entries": entries,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(_args.gold, len(entries), "entries (gold)")
