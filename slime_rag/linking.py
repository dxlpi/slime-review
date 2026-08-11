# -*- coding: utf-8 -*-
"""
개체연결 (Phase 3) — 후기의 mentioned_market/product → KB 정규 레퍼런스.

원칙(스펙): 정답이 없으면 지어내지 않고 '보류(abstain)'한다.
- 마켓: KB 명부의 표면형(마켓명·약칭·핸들·별칭) 또는 초성으로 결정적 매칭.
  초성 충돌(ㅁㅁ, ㅇㅇ 등 전체 KB 12그룹)이면 후보가 여럿 → 확신도를 나눠 보류.
- 제품: KB `products[]` 가 아직 비어있다(1층 미시드). 검증할 그라운드 트루스가 없으므로
  제품은 보류하고 표면형만 잠정 보존한다. 1층이 채워지면 같은 매칭 로직이 제품에도 적용된다.
- 확신도 < settings.link_abstain_threshold 면 abstain.

[TODO] 사용자 제공: 제품 약칭 사전 시드(예: 몽땅←사과몽땅), 개체연결 정답셋 ~30~50개(정확도 측정).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from .config import settings, ROOT

log = logging.getLogger(__name__)

# 한글 초성표 — 음절을 초성 자모로 환원해 '빈짱'과 'ㅂㅉ'을 같은 키로 본다.
_CHO = ["ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ", "ㅆ",
        "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]


def _strip(s: str) -> str:
    return "".join(s.split())


def choseong(s: str) -> str:
    """문자열의 초성만 추출. 음절은 자모로 환원, 이미 자모면 그대로, 그 외는 버린다."""
    out = []
    for c in s:
        o = ord(c)
        if 0xAC00 <= o <= 0xD7A3:           # 완성형 음절 → 초성 인덱스
            out.append(_CHO[(o - 0xAC00) // 588])
        elif 0x3131 <= o <= 0x314E:          # 호환 자모(이미 초성)
            out.append(c)
    return "".join(out)


@dataclass
class LinkResult:
    market: Optional[str]               # 정규 market_word, 보류면 None
    market_confidence: float
    product: Optional[str]              # 잠정 제품 표면형(KB 미시드라 미검증)
    product_confidence: float
    abstained: bool                     # 마켓이 임계 미만이면 True
    candidates: list[str] = field(default_factory=list)   # 충돌 시 후보 market_word들
    reason: str = ""


# ------------------------------------------------- 제품→마켓 역인덱스(확신도 상수)
# 왜 별도 값인가: 이 두 값이 **롤백의 유일한 열쇠**다. 역인덱스로 채운 행은 원문이 마켓을
# 말한 적이 없으므로, 나중에 오귀속이 드러나면 그 값 하나로 전량을 식별해 되돌릴 수 있어야
# 한다. 직접 매칭과 같은 값을 쓰면 그 구분이 영영 사라진다 — 틀린 마켓은 NULL 보다 나쁘다
# (남의 마켓 후기가 화면에 붙고, 그게 곧 1급 기능인 출처 편향의 왜곡이다).
# ⚠️ **SQL 로 찾을 땐 `::real` 캐스트가 필수다.** `reviews.market_confidence` 는 `REAL`(4바이트)
#   인데 리터럴·바인딩 파라미터는 `float8` 이라, `WHERE market_confidence = 0.80` 은
#   `0.80::real::float8 = 0.800000011920929 ≠ 0.8` 로 **한 행도 안 맞는다**(실측: `= 0.85` 0행 vs
#   `= 0.85::real` 397행). 조용히 빈 결과를 주므로 '되돌릴 게 없다'로 읽힌다 — 롤백 경로가
#   있다고 믿으면서 실제로는 없는 상태다. 실행 가능한 정본은 `pipeline.revert_market_inversion`.
# ⚠️ 두 층을 **한 값으로 합치지 말 것.** 근거의 세기가 다르다: 1층(`specs`)은 판매자 본인
#   캡션에서 온 값이고, 레지스트리는 해시태그 빈도로 **유도된 후보**라 사람이 승격한 목록이
#   아니다(실측 잡음: 늪지의 `액괴`·`워터글루`). 값이 하나면 잡음 층만 골라 되돌릴 수 없다.
# 값의 배치: 직접 매칭(표면형 0.95 · 초성 0.85)보다 낮고, `link_abstain_threshold`(0.6)보다는
# 높다 — 채웠는데 임계 미만이면 '보류가 아닌데 확신도는 보류선 아래'라는 모순된 행이 남는다.
INVERSION_CONF_SPEC = 0.80          # 1층 `specs` 유일소유
INVERSION_CONF_REGISTRY = 0.65      # 제품 후보 레지스트리 유일소유
INVERSION_CONFS = (INVERSION_CONF_SPEC, INVERSION_CONF_REGISTRY)

# 제품명 **접두**에서 건진 마켓의 확신도. 직접 매칭(0.95·0.85)보다 한 칸 낮다 — 원문이
# `market` 자리에 쓴 게 아니라 제품명 칸에 섞여 들어온 표기라, 추출기가 경계를 잘못 그었을
# 가능성이 한 겹 더 있다. 역인덱스(0.80·0.65)보다는 높다: 저건 원문이 마켓을 **한 번도 말한
# 적 없는** 행을 제품명 소유관계로 추론한 것이고, 이건 원문이 그 자리에 실제로 쓴 표기다.
# ⚠️ 직접 매칭과 **같은 값을 쓰지 말 것.** `INVERSION_CONFS` 와 같은 이유다 — reason 은 DB 에
#   안 남고 `market_confidence` 만 남으므로, 이 값이 '접두에서 온 마켓'을 사후에 전량 식별해
#   되돌릴 수 있는 유일한 열쇠다. `ㅈㄴ` 처럼 마켓 초성과 부사가 겹치는 표기가 실재해서
#   (실측: 빠코볼 25건의 `ㅈㄴ` 6건 대부분이 부사 '존나') 되돌릴 길은 반드시 있어야 한다.
PREFIX_CONF_SURFACE = 0.92          # 접두가 표면형(마켓명·핸들·별칭)
PREFIX_CONF_CHOSEONG = 0.82         # 접두가 초성
PREFIX_CONFS = (PREFIX_CONF_SURFACE, PREFIX_CONF_CHOSEONG)

REASON_PREFIX = "제품명 접두"

# 스레드 **글 마켓 상속**으로 채워진 행의 확신도. 다른 채움 경로가 전부 전용 값을 갖는데
# (PREFIX 0.92/0.82 · INVERSION 0.80/0.65 · `pipeline.BACKFILL_CONFS` 0.90/0.70) 상속만
# 없어서, 물려받은 마켓이 **원문이 직접 말한 마켓과 바이트 단위로 같았다**(0.95/0.85).
# 그러면 `extract.py` 자신이 다른 자리에서 "되돌릴 표식 없는 오귀속은 NULL 보다 나쁘다"고
# 적어 둔 상태가 상속에만 그대로 남는다. `counts["market_inherited"]` 는 **런 집계**라
# 행을 짚지 못한다 — 되돌리려면 `WHERE market_confidence = 0.75` 처럼 행을 골라야 한다.
# 값의 배치: `link_abstain_threshold`(0.6)보다 높고 직접 매칭·접두보다 낮다.
# ⚠️ 이 숫자는 **provenance 표식이지 티어 정렬 키가 아니다.** 어느 근거가 이기는지는 아래
#   코드의 분기 순서가 정하고, 숫자는 '어디서 왔는가'만 기록한다. 값을 바꾸려면 다른 다섯
#   상수와 겹치지 않는지 먼저 확인할 것(겹치면 층별 롤백이 그 순간 불가능해진다).
INHERIT_CONF = 0.75
REASON_INHERIT = "글 마켓 상속"

# 조각 **본문 스캔**으로 되찾은 마켓(`markets_in_text`).
# 왜 쌍인가: 표면형(`늪지에서 샀는데`)과 초성(`ㅅㅈㄴ 헝잭버거`)은 오탐 성질이 다르다.
# 초성은 일반어와 겹치고(`ㅈㄴ`=존나) 표면형은 안 겹친다 — 나중에 초성 층만 골라
# 되돌릴 수 있어야 한다(`PREFIX_CONFS` 를 쌍으로 둔 것과 정확히 같은 이유).
# 값의 배치: 접두(0.92/0.82)보다 **아래**다 — 접두는 추출기가 제품명 칸에 넣은 표기라
# 위치가 특정되지만, 이건 본문 어디에나 있을 수 있어 근접성 근거가 한 겹 약하다.
# 역인덱스(0.80/0.65)보다는 위다 — 저건 원문이 마켓을 한 번도 말한 적 없는 행이다.
# ⚠️ 이 숫자도 provenance 표식이지 티어 정렬 키가 아니다(위 `INHERIT_CONF` 주석과 같다).
#   실제 서열은 아래 `link()` 의 분기 순서가 정한다.
REPAIR_CONF_PIECE_SURFACE = 0.88
REPAIR_CONF_PIECE_CHOSEONG = 0.83
REPAIR_PIECE_CONFS = (REPAIR_CONF_PIECE_SURFACE, REPAIR_CONF_PIECE_CHOSEONG)
REASON_PIECE_SCAN = "조각 본문 스캔"

REASON_INVERSION_SPEC = "제품→마켓 역인덱스(1층)"
REASON_INVERSION_REGISTRY = "제품→마켓 역인덱스(레지스트리)"
REASON_INVERSION_AMBIGUOUS = "제품→마켓 역인덱스 다중소유→보류"
REASON_INVERSION_EXCLUDED = "제품→마켓 역인덱스 제외목록"


# ---------------------------------------------------------------- KB 인덱스
class KB:
    """KB 마켓 명부를 표면형/초성 역인덱스로 한 번만 구성한다."""

    def __init__(self, data: dict):
        self.markets = data["markets"]
        self._literal: dict[str, list[dict]] = {}
        self._cho: dict[str, list[dict]] = {}
        # market_word → 엔트리. 정규 키라 충돌이 없어(마켓당 1개) 리스트가 아니라 단일 값이다.
        self._by_word: dict[str, dict] = {}
        for m in self.markets:
            self._by_word.setdefault(_strip(m["market_word"]), m)
            # 마켓별로 키를 집합화 → 한 마켓이 같은 버킷에 중복 등록되지 않게.
            for form in {_strip(f).lower() for f in self._surface_forms(m)}:
                self._literal.setdefault(form, []).append(m)
            for cho in set(self._choseong_forms(m)):
                self._cho.setdefault(cho, []).append(m)

    @staticmethod
    def _surface_forms(m: dict) -> list[str]:
        forms = [m["market"], m["market_word"], m["handle"], *m["handles_alt"], *m["aliases"]]
        return [f for f in forms if f]

    @staticmethod
    def _choseong_forms(m: dict) -> list[str]:
        # 명시 초성 + 별칭 초성 + 마켓명에서 환원한 초성
        forms = [m["choseong"], *m["choseong_aliases"], choseong(m["market_word"])]
        return [f for f in forms if f]

    def market_by_word(self, market_word: str | None) -> Optional[dict]:
        """정규 `market_word` → KB 엔트리. 없으면 None.

        `resolve_market` 과 다르다 — 저건 후기 표면형을 **추론**하는 퍼지 경로(초성·별칭·보류)고,
        이건 이미 정규화된 키의 **정확 조회**다. DB `specs.market` 이 정규 market_word 라
        표시 계층(로고 등)은 추론 없이 여기로 들어온다.
        """
        if not market_word:
            return None
        return self._by_word.get(_strip(market_word))

    def resolve_market(self, mentioned: str) -> tuple[list[dict], float, str]:
        """(후보들, 확신도, 근거). 표면형 우선, 없으면 초성."""
        key = _strip(mentioned).lower()
        if key in self._literal:
            hits = self._literal[key]
            return hits, (0.95 if len(hits) == 1 else 1.0 / len(hits)), "표면형"
        cho = choseong(mentioned)
        if cho and cho in self._cho:
            hits = self._cho[cho]
            return hits, (0.85 if len(hits) == 1 else 1.0 / len(hits)), "초성"
        return [], 0.0, "미발견"


def load_kb() -> KB:
    return KB(json.loads(settings.kb_demo_path.read_text(encoding="utf-8")))


def load_product_aliases() -> dict[str, dict[str, str]]:
    """
    제품 약칭 사전(§11-C 시드) → {market_word: {약칭: 정규제품명}}.
    마켓별 스코프라 같은 약칭이 마켓마다 다른 제품을 가리켜도 안전.
    link_post 에 주입할 땐 해당 후기의 '정규화된 market' 하위 dict 만 넘긴다.
    파일이 없으면 빈 dict.
    """
    path = ROOT / "data" / "product_aliases.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


MARKET_INVERSION_EXCLUDES_PATH = ROOT / "data" / "market_inversion_excludes.json"


def load_market_inversion_excludes() -> set[str]:
    """역인덱스 **제외 이름** 목록 → 정규화된 집합. 파일이 없으면 빈 집합.

    `data/product_aliases.json`·`data/spec_overrides.json` 과 같은 가족이다 — 커밋되는
    **사람 판단 오버레이**이고, 자동 유도물(`data/product_registry.json`)이 틀렸을 때
    사람이 그 이름 하나만 꺼내는 자리다.

    왜 코드가 아니라 파일인가: 레지스트리는 해시태그 빈도로 유도된 후보라 잡음이 섞이는데,
    잡음의 목록은 코퍼스가 자라면 같이 자란다. 코드에 박으면 그때마다 배포가 필요하고,
    무엇보다 **사람이 검수한 판단**이라는 성격이 소스코드에 섞여 안 보이게 된다.
    """
    path = MARKET_INVERSION_EXCLUDES_PATH
    if not path.exists():
        return set()
    # ⚠️ 파일이 깨지면 '제외 없음'으로 떨어진다 — 안전한 쪽이 아니다(제외했어야 할 이름이
    #   채워진다). 그래도 예외로 죽이지 않는 건, 이 함수가 수집 경로 한복판에서 불리는데
    #   그 행들은 전용 confidence 로 전량 되돌릴 수 있기 때문이다. 대신 조용히 넘기지 않는다.
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("역인덱스 제외목록 읽기 실패 — 제외 없이 진행: %s", e)
        return set()
    names = data.get("excludes") if isinstance(data, dict) else data
    return {_strip(n).lower() for n in (names or []) if isinstance(n, str) and n}


UNREGISTERED_MARKET_TOKENS_PATH = ROOT / "data" / "unregistered_market_tokens.json"


def load_unregistered_market_tokens() -> frozenset:
    """KB **미등재** 마켓 초성 오버레이 → 토큰 집합. 파일이 없으면 빈 집합.

    `load_market_inversion_excludes` 와 같은 가족이고 **같은 실패 정책**이다(깨지면 경고 후
    빈 집합, 예외로 죽지 않음) — 수집 경로 한복판에서 불리기 때문이다.

    ⚠️ **자모 전용이 아닌 항목은 거부한다.** 완성형 문자열이 들어오면 `_market_token` 이
      그걸 제품명 접두로 인정해 **진짜 제품명을 잘라 낸다** — `split_market_prefix` 가
      완성형 초성 환원을 의도적으로 금지한 이유(`포도`→푸딩 · `배`→봄 · `육쩐`→연찌 사고)를
      오버레이가 우회하는 구멍이 된다. 거부는 **조용히 하지 않는다**(`log.warning`).
    ⚠️ 여기 쓰는 정규식은 `extract._ALL_JAMO_RE` 의 **더 좁은 쌍둥이**다(공백 불허) —
      저쪽은 이름 전체가 자모인지를 보고, 여긴 **토큰** 하나를 본다. 토큰에 공백이 있으면
      접두 분리의 경계 판정이 무의미해진다.
    """
    path = UNREGISTERED_MARKET_TOKENS_PATH
    if not path.exists():
        return frozenset()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("미등재 마켓 토큰 읽기 실패 — 오버레이 없이 진행: %s", e)
        return frozenset()
    raw = data.get("tokens") if isinstance(data, dict) else data
    if isinstance(raw, dict):
        names = list(raw.keys())
    else:
        names = [n for n in (raw or []) if isinstance(n, str)]
    good, bad = set(), []
    for n in names:
        tok = _strip(n or "")
        (good.add(tok) if tok and _ALL_JAMO_RE.match(tok) else bad.append(n))
    if bad:
        log.warning("미등재 마켓 토큰 중 자모 전용이 아닌 %d건을 버렸다: %s", len(bad), bad[:5])
    return frozenset(good)


@dataclass(frozen=True)
class MarketInversion:
    """제품명 → 마켓 역인덱스. **두 층을 끝까지 갈라 둔다**(합집합 금지).

    각 층은 `{정규화 제품명: market_word | None}` 이고 값이 `None` 이면 **여러 마켓이
    같은 이름을 갖는다**(=모호 → 채우지 않는다). 키가 아예 없는 것과 값이 None 인 것은
    다른 뜻이다 — 전자는 '그 층이 모르는 이름', 후자는 '그 층이 알지만 못 고른다'이고,
    A2(1층 우선)를 지키려면 후자에서 **레지스트리로 내려가면 안 된다.**

    ⛔ 두 dict 를 하나로 합치지 말 것. 합치면 1층에서 정확히 하나이던 판정이 레지스트리
      후보가 끼어들어 다중소유(=보류)로 **퇴화**한다. 있던 판정이 사라지는 방향이라
      화면엔 '마켓 없는 후기'로만 보이고 원인이 안 보인다
      (`extract.resolve_product_name` 의 ③′ 2단 타이브레이크와 정확히 같은 금지다).
    """
    spec: dict[str, Optional[str]] = field(default_factory=dict)
    registry: dict[str, Optional[str]] = field(default_factory=dict)
    excludes: frozenset = frozenset()

    def market_for(self, product: str | None) -> tuple[Optional[str], float, str]:
        """제품 표면형 → `(마켓|None, 확신도, 사유)`. 못 채우면 `(None, 0.0, 사유)`."""
        key = _strip(product or "").lower()
        if not key:
            return None, 0.0, ""
        if key in self.excludes:
            return None, 0.0, REASON_INVERSION_EXCLUDED
        # A2: 1층이 **그 이름을 알면** 거기서 끝난다. 모호해도 레지스트리로 내려가지 않는다.
        if key in self.spec:
            mk = self.spec[key]
            return ((mk, INVERSION_CONF_SPEC, REASON_INVERSION_SPEC) if mk
                    else (None, 0.0, REASON_INVERSION_AMBIGUOUS))
        if key in self.registry:
            mk = self.registry[key]
            return ((mk, INVERSION_CONF_REGISTRY, REASON_INVERSION_REGISTRY) if mk
                    else (None, 0.0, REASON_INVERSION_AMBIGUOUS))
        return None, 0.0, ""


def _own_index(pairs) -> dict[str, Optional[str]]:
    """`[(market, product)…]` → `{정규화 제품명: 유일소유 마켓 | None}`."""
    owners: dict[str, set] = {}
    for market, product in pairs:
        if not market or not product:
            continue
        owners.setdefault(_strip(product).lower(), set()).add(market)
    return {name: (next(iter(mk)) if len(mk) == 1 else None) for name, mk in owners.items()}


def build_market_inversion(spec_pairs=(), registry=None, *, excludes=None) -> MarketInversion:
    """역인덱스 조립. **DB 를 열지 않는다** — 1층 쌍은 호출부가 주입한다.

    spec_pairs: `SELECT market, product FROM specs` 결과(무과금 왕복 1회).
    registry:   `pipeline.load_product_registry()` 의 `{market_word: [제품명…]}`.
    excludes:   `load_market_inversion_excludes()`. 미지정이면 디스크에서 읽는다.

    이 모듈이 DB 무의존인 건 우연이 아니라 계약이다(`load_kb`·`load_product_aliases` 도
    디스크만 읽는다). 여기서 `db.connect` 를 부르면 오프라인 게이트가 통째로 못 돈다.
    """
    reg_pairs = [(market, name)
                 for market, names in (registry or {}).items() for name in (names or [])]
    ex = load_market_inversion_excludes() if excludes is None else {
        _strip(n).lower() for n in excludes}
    return MarketInversion(spec=_own_index(spec_pairs), registry=_own_index(reg_pairs),
                           excludes=frozenset(ex))


# ---------------------------------------------------------------- 조각 스코프 텍스트 스캐너
# 일반어와 겹쳐 **단독으로는 마켓 근거가 못 되는** 표기. 실측(2026-08-10 아모스갤):
#   `ㅈㄴ`=존나(빠코볼 25건 중 6건이 부사) · `푸딩`=질감어(`젤리나 푸딩같은 느낌`) ·
#   `봄`=계절/`보다` · `지나`=`지나면` · `머머`=`ㅁㅁ` 잡음 · `캐치`=일반어.
# 이 토큰들은 `noisy` 로만 보고하고, **바로 뒤에 아는 제품명이 붙을 때만** `unique` 로 승격한다.
# ⛔ `kb.resolve_market` 을 이 규칙으로 좁히지 말 것. 저건 추출기가 **이미 마켓 칸에 넣은 값**을
#   푸는 함수라, 거기서 `푸딩` 을 막으면 `mentioned_market="푸딩"` 인 정상 행이 죽는다.
#   `_market_token` 이 `resolve_market` 보다 좁은 것과 같은 계층 분리다.
AMBIGUOUS_SURFACES = frozenset({"푸딩", "봄", "지나", "캐치", "머머", "스르륵"})
AMBIGUOUS_CHOSEONG = frozenset({"ㅈㄴ"})

# 표면형 매칭의 **경계 규칙**. 부분문자열 매칭만으로는 마켓명이 다른 낱말 속에 묻혀도 잡힌다 —
# 실측(2026-08-11, 아모스갤 801행): `토끼나마나` 안의 `마나`(마나슬라임) · `포이즈닝` 안의
# `포이`(포이슬라임). 둘 다 **진짜 제품명**이라, 그대로 두면 제품명이 마켓 근거로 둔갑한다.
# 그렇다고 양쪽을 다 막으면 안 된다: 한국어는 조사가 이름에 **붙어서** 온다(`늪지에서 샀는데`).
# 그래서 왼쪽은 음절이면 무조건 거부하고, 오른쪽은 **조사일 때만** 허용한다.
# ⚠️ 조사 목록은 어휘(데이터)지 구조 규칙이 아니다 — 늘리려면 반례를 먼저 확인할 것.
_HANGUL_SYLLABLE = re.compile(r"[가-힣]")
_PARTICLE_HEADS = ("에", "은", "는", "이", "가", "을", "를", "의", "도", "만", "과", "와",
                   "랑", "로", "으", "서", "부", "까", "보", "처", "같", "한", "께", "네")

_JAMO_RUN_ANY_RE = re.compile(r"[ㄱ-ㅎ]+")
# 승격 판정에 쓰는 제품명의 최소 길이. 한 글자 이름은 아무 꼬리에나 걸린다.
_PROMOTE_MIN_NAME = 2


@dataclass(frozen=True)
class ScanResult:
    """조각 본문/제목이 **마켓 자리로 쓴** 토큰의 분류. 네 갈래가 서로 다른 뜻이다.

    unique    — KB 마켓이고 이 텍스트가 확실히 지목했다. 채움의 근거가 된다.
    ambiguous — 초성 충돌(후보 2+). 갈린 증거라 채우지 않는다.
    unknown   — KB **미등재**인데 마켓 자리로 쓰였다. 채우지 않고 **추론을 막는** 재료다.
    noisy     — 일반어와 겹친다. 단독으로는 근거가 못 된다.

    ⚠️ `unknown` 과 `noisy` 를 합치지 말 것 — 전자는 '진짜 마켓인데 명부에 없다'이고
      후자는 '마켓이 아닐 수 있다'다. 전자만 상속·역인덱스를 꺼야 한다.
    """
    unique: frozenset = frozenset()
    ambiguous: dict = field(default_factory=dict)
    unknown: frozenset = frozenset()
    noisy: frozenset = frozenset()
    # market_word → "표면형" | "초성". **확신도를 가르는 유일한 재료**다(0.88 vs 0.83):
    # 초성은 일반어와 겹치고(`ㅈㄴ`=존나) 표면형은 안 겹치므로, 나중에 초성 층만 골라
    # 되돌릴 수 있어야 한다(`PREFIX_CONFS` 를 쌍으로 둔 것과 정확히 같은 이유).
    how: dict = field(default_factory=dict)


def _scan_tables(kb: KB) -> tuple[dict, dict]:
    """(초성→[market_word], 표면형→[market_word]). KB 당 한 번 만들어 캐시한다.

    재료는 **`market_word` + `aliases`** 뿐이다 — `handle`(영문)까지 넣으면 영어 단어가
    스치기만 해도 마켓이 되고, `market`(정식명)은 `market_word` 를 품고 있어 중복이다.
    감사 스크립트(`evals/review_dcinside_extraction.scan_markets`)가 D3 105·D4 3 이라는
    숫자를 실제로 만들어 낸 재료와 같은 구성이고, 그게 이 함수의 정본 의미론이다.
    """
    cached = getattr(kb, "_scan_tables_cache", None)
    if cached is not None:
        return cached
    cho: dict[str, list[str]] = {}
    surf: dict[str, list[str]] = {}
    for m in kb.markets:
        mw = m["market_word"]
        for c in [m.get("choseong") or "", *(m.get("choseong_aliases") or [])]:
            if len(c) >= 2 and mw not in cho.setdefault(c, []):
                cho[c].append(mw)
        for f in [mw, *(m.get("aliases") or [])]:
            f = _strip(f or "")
            if f and mw not in surf.setdefault(f, []):
                surf[f].append(mw)
    kb._scan_tables_cache = (cho, surf)
    return cho, surf


def _surface_occurs(text: str, form: str) -> bool:
    """`form` 이 **낱말 경계에서** 나타나는가. 순수 함수(위 `_PARTICLE_HEADS` 주석 참조).

    ⛔ 단순 `in` 으로 되돌리지 말 것 — 실측으로 `토끼나마나`·`포이즈닝` 같은 **진짜 제품명**이
      마켓으로 잡힌다. 그 손실은 반대 방향으로 조용하다: 후기가 남의 마켓에 붙는다.
    """
    start = 0
    while (i := text.find(form, start)) != -1:
        start = i + 1
        if i > 0 and _HANGUL_SYLLABLE.match(text[i - 1]):
            continue                              # 왼쪽이 음절 → 낱말 속에 묻힌 것
        j = i + len(form)
        if j < len(text) and _HANGUL_SYLLABLE.match(text[j]) \
                and not text[j:].startswith(_PARTICLE_HEADS):
            continue                              # 오른쪽이 조사가 아닌 음절 → 다른 낱말
        return True
    return False


def _promoted(text: str, token: str, known_norm: frozenset) -> bool:
    """`token` **바로 뒤**에 아는 제품명이 붙어 있는가 — 모호 토큰의 유일한 승격 경로."""
    if not known_norm:
        return False
    for m in re.finditer(re.escape(token), text):
        tail = _strip(text[m.end():m.end() + 40]).lower()
        if any(tail.startswith(k) for k in known_norm):
            return True
    return False


def markets_in_text(text: str, kb: KB, *, unregistered=frozenset(),
                    known_products=None) -> ScanResult:
    """텍스트가 마켓 자리로 쓴 토큰을 분류한다. 순수 함수(무DB·무LLM·무네트워크).

    ⚠️ **이 함수는 `backfill_review_markets` 에 되살아난 옛 가드가 아니다.** 되돌린 것은
      *행 스코프의 **부정** 가드* — '이미 다른 근거로 정해진 채움을 본문 스캔이 거부한다'였다.
      실패 모드는 "비교글에 마켓이 여럿 나오는 건 정상인데 그중 하나를 제품명으로만 가리킨
      것을 모순으로 읽었다"이고, 실측 3건 전부 채운 값이 **맞았다**. 여기는 스코프도 방향도
      다르다: **조각 스코프**에서 **채움의 근거를 만든다**(긍정). 그리고 되돌림의 진짜 교훈은
      "본문을 보지 마라"가 아니라 "**막는 판단을 조용히 하지 마라**"였다 —
      그래서 이 스캐너의 소비처는 충돌을 반드시 `conflict_list` 로 내보낸다
      (게이트: `test_the_body_scan_conflict_guard_stays_out`).

    ⚠️ 기수성(cardinality)이 그 구분을 기계적으로 보장한다: 이 결과는 `unique` 가 **정확히
      하나**일 때만 근거가 된다. 옛 가드를 깨뜨린 바로 그 입력(여러 마켓을 나열하는 비교글)
      에서는 `unique` 가 둘 이상이라 아무것도 만들어 내지 않고 그냥 통과한다.

    unregistered: KB 미등재인데 마켓 자리로 쓰이는 초성 집합
      (`load_unregistered_market_tokens()`). 미주입이면 `unknown` 은 항상 빈다.
    known_products: 모호 토큰 승격의 **증거**(`pipeline.known_product_names()`).
      ⚠️ 미주입이면 승격이 **아예 없다** — 페일세이프이고, 그 상태의 의미론이 감사
        스크립트 `scan_markets` 와 정확히 같다(그래서 감사 숫자와 대조가 가능하다).
    """
    if not text:
        return ScanResult()
    cho_tbl, surf_tbl = _scan_tables(kb)
    known_norm = frozenset(
        _strip(p).lower() for p in (known_products or ())
        if p and len(_strip(p)) >= _PROMOTE_MIN_NAME)
    unique: set[str] = set()
    ambiguous: dict[str, list[str]] = {}
    unknown: set[str] = set()
    noisy: set[str] = set()
    how: dict[str, str] = {}

    def _hit(mw: str, kind: str) -> None:
        unique.add(mw)
        if kind == "표면형" or mw not in how:      # 표면형이 초성을 이긴다(더 특정적)
            how[mw] = kind

    for run in set(_JAMO_RUN_ANY_RE.findall(text)):
        hits = cho_tbl.get(run)
        if run in AMBIGUOUS_CHOSEONG:
            noisy.add(run)
            if hits and len(hits) == 1 and _promoted(text, run, known_norm):
                _hit(hits[0], "초성")
            continue
        if hits:
            (_hit(hits[0], "초성") if len(hits) == 1
             else ambiguous.setdefault(run, list(hits)))
        elif run in unregistered:
            unknown.add(run)

    low = text.lower()
    for form, hits in surf_tbl.items():
        if not _surface_occurs(low, form.lower()):
            continue
        if form in AMBIGUOUS_SURFACES:
            noisy.add(form)
            if len(hits) == 1 and _promoted(text, form, known_norm):
                _hit(hits[0], "표면형")
            continue
        (_hit(hits[0], "표면형") if len(hits) == 1 else ambiguous.setdefault(form, list(hits)))

    return ScanResult(frozenset(unique), ambiguous, frozenset(unknown), frozenset(noisy), how)


# ---------------------------------------------------------------- 마켓 접두 분리
_JAMO_RUN_RE = re.compile(r"^[ㄱ-ㅎ]+")
_ALL_JAMO_RE = re.compile(r"^[ㄱ-ㅎ]+$")


def _market_token(token: str, kb: KB, unregistered=frozenset()) -> bool:
    """이 토큰이 **제품명 안에서** 마켓 표기로 인정되는가. 순수 함수.

    `kb.resolve_market` 보다 **좁다**. 저건 추출기가 이미 '마켓 칸'에 넣은 값을 푸는 함수라
    완성형 음절도 초성으로 환원해 맞춰 보지만, 여기서 보는 건 **제품명**이다 — 아무 두 음절
    단어나 초성으로 환원하면 진짜 제품명이 마켓으로 오인된다.

    실측(2026-08-10, 아모스갤): 환원을 허용하면 `포도`가 푸딩(`ㅍㄷ`)으로, `배`가 봄(`ㅂ`)으로,
    `육쩐 젤쿠칩` 의 `육쩐` 이 연찌(`ㅇㅉ`)로 잡혀 **멀쩡한 제품명이 마켓 접두로 잘려 나갔다**.
    이건 조용한 손실이다 — 화면엔 '제품 없는 후기'로만 보이고 원인이 안 보인다.

    그래서 인정 조건은 둘뿐이다:
      ① **표면형 완전일치** — 마켓명·표시어·핸들·별칭(`늪지`·`봄`).
      ② **자모만으로 이루어진 토큰**이 KB 초성과 일치(`ㅂㅇㅍ`·`ㅇㅊ`). 호환 자모는 완성형
         제품명의 일부가 될 수 없어 경계가 모호하지 않다.
      ③ **KB 미등재 마켓 토큰**(`unregistered`) — `ㅅㄱㄷ`·`ㅍㅍㄹ` 처럼 아모스갤이 마켓
         자리로 쓰는데 명부에 없는 초성. 이것도 **자모 전용일 때만** 인정한다(로더가 이미
         걸러내지만, 여기서 한 번 더 보는 건 호출부가 임의 집합을 주입할 수 있어서다).
         ⚠️ 인정한다고 **채우지 않는다** — 떼어 낸 힌트는 `kb.resolve_market` 에서 미발견이라
           마켓은 NULL 로 남고, 대신 `surface` 가 비지 않아 **상속과 역인덱스가 꺼진다**.
           그게 이 오버레이의 전부다(ROW#1111 `ㅍㅍㄹ 애플크림머핀`→예찬 오귀속 차단 ·
           D2c `진저크런키`→지나 차단).
    완성형 음절을 초성으로 환원하는 경로는 **의도적으로 없다**.
    """
    if unregistered and _ALL_JAMO_RE.match(token) and _strip(token) in unregistered:
        return True
    hits, _conf, how = kb.resolve_market(token)
    if not hits:
        return False
    return how == "표면형" or bool(_ALL_JAMO_RE.match(token))


def split_market_prefix(name: Optional[str], kb: KB,
                        unregistered=frozenset()) -> tuple[Optional[str], Optional[str]]:
    """제품명 표면형 → `(마켓 힌트, 제품명)`. 순수 함수(무LLM·무DB).

    추출 프롬프트는 '제품명에 마켓 초성을 섞지 마라'고 **말만** 하고 강제가 없었다.
    실측(2026-08-10, 아모스갤 813행): 순수 초성 제품명 26행/15종, `초성+제품명` 11행/9종.
    그리고 그 접두는 버릴 잡음이 아니라 **그 항목의 마켓 신호**다 — `ㅇㅉ 쿠키슈크림번` 의
    `ㅇㅉ` 는 연찌다. 그래서 떼면서 동시에 힌트로 돌려준다.

        "ㅇㅉ 쿠키슈크림번"      -> ("ㅇㅉ", "쿠키슈크림번")
        "ㅂㅇㅍ"                -> ("ㅂㅇㅍ", None)      # 전체가 마켓 → 제품명 보류
        "빠코볼"                -> (None, "빠코볼")     # 무변경
        "포도"                  -> (None, "포도")       # 완성형은 초성 환원 안 한다(≠푸딩)
        "요아곰 밀키크림파르페"   -> (None, "요아곰 밀키크림파르페")   # KB 부재 접두는 안 뗀다

    ⛔ **임의 초성 스트립 금지.** 접두로 인정하는 건 `kb.resolve_market` 이 후보를 내는,
      즉 **KB 명부에 실재하는** 표면형/초성뿐이다. `ㅈㄴ` 은 '지나'이기도 하고 부사 '존나'이기도
      해서(실측: 빠코볼 25건 중 `ㅈㄴ` 6건 대부분이 부사) 여기서 나온 힌트는 확정이 아니다 —
      확신도 판정은 `link()` 의 기존 규칙(초성 충돌 시 abstain)에 그대로 태운다. 이 함수는
      **후보만 만들고 아무것도 확정하지 않는다.**
    ⛔ 왜 `extract.py` 가 아니라 여기인가: KB 를 봐야 하고, 추출층이 KB 를 알면 계층이 뒤집힌다
      (`extract.py` 머리말의 "정규화는 linking 단계" 규칙).

    붙여 쓴 초성 접두(`ㅈㄴ아몬드바나나브레드`)도 뗀다 — 호환 자모는 완성형 제품명의 일부가
    될 수 없어 경계가 모호하지 않다. 반대로 **음절 접두는 공백이 있을 때만** 본다
    (`요아곰밀키크림파르페` 를 임의로 가르면 진짜 제품명을 반토막 낸다).
    """
    if not name or not name.strip():
        return None, None
    text = name.strip()
    if _market_token(text, kb, unregistered):    # 전체가 마켓이면 제품명이 아니다 → 보류
        return text, None

    head, sep, tail = text.partition(" ")
    if sep and tail.strip() and _market_token(head, kb, unregistered):
        return head, tail.strip()

    # 공백 없이 붙은 자모 접두 — "ㅈㄴ아몬드바나나브레드"
    if (m := _JAMO_RUN_RE.match(text)):
        head, tail = m.group(0), text[m.end():].strip()
        if tail and _market_token(head, kb, unregistered):
            return head, tail
    return None, text


# ---------------------------------------------------------------- 연결
def link(
    mentioned_market: Optional[str],
    mentioned_product: Optional[str],
    *,
    kb: KB,
    aliases: Optional[dict[str, str]] = None,
    inversion: Optional[MarketInversion] = None,
    fallback_market: Optional[str] = None,
    inherited: bool = False,
    unregistered=frozenset(),
    known_products=None,
    text_markets: Optional[ScanResult] = None,
) -> LinkResult:
    """
    (mentioned_market, mentioned_product) → LinkResult.
    마켓은 결정적 매칭 + 충돌→abstain. 제품은 KB 미시드라 보류(표면형만 보존).
    aliases: 제품 약칭 사전(예: {'몽땅':'사과몽땅'}). 없으면 표면형 그대로.
    inversion: 제품→마켓 역인덱스(`build_market_inversion`). **마켓을 아무도 말하지 않은
      경우에만** 본다 — 아래 분기 참조. 미주입이면 이 기능 자체가 없던 것과 같다.

    마켓 근거는 **좁은 것부터** 본다 — 다섯 단계이고 순서가 곧 근거의 세기다:
      ① `mentioned_market` — 이 **항목**이 직접 말한 마켓(`reviews[].mentioned_market`).
      ② 제품명 접두(`split_market_prefix`) — 이 항목의 제품명 칸에 섞여 들어온 표기.
      ③ `text_markets` — 이 **조각 본문**이 유일하게 지목한 마켓(`markets_in_text`).
      ④ 제품→마켓 역인덱스 **1층만** — 제품명의 1층 유일소유.
      ⑤ `fallback_market` — **글/스레드 단위** 값(`doc["market"]`).

    ⛔ ⑤를 ①②보다 앞에 두지 말 것. ⑤는 스레드 상속으로 채워질 수 있어(형제 조각에서 온
      값이다) 항목 자신의 증거보다 약하다. 앞에 두면 비교 스레드에서 **남의 마켓 후기**가
      된다 — `extract.extract_collected` 가 상속을 '채우기 전용'으로 바꾼 것과 같은 사고다.

    ⚠️ **④가 ⑤보다 앞이라는 게 2026-08-11 의 변경이다.** 그전엔 역인덱스가
      `market is None and not surface` 일 때만 돌았고 `surface` 에 `fallback_market` 이
      섞여 있어서, 상속이 있으면 역인덱스가 **아예 안 돌았다**(상속 > 1층 소유). 사람이
      만든 전수 감사가 그 순서 때문에 생긴 오귀속을 18행(D2) 짚었다 — `요구르팅` 의 1층
      소유는 지나인데 늪지 스레드 상속이 덮는 식이다. CLAUDE.md 가 이 순서를 "재검토 여지가
      있으나 **측정 없이 건드리지 말 것**"으로 봉인했었고, 그 측정이 바로 그 감사다
      (마켓 축은 `attributes` 에 `mentioned_market` 이 없어 리플레이가 원리적으로 불가능하다).

    ⛔ **불변 유지**: `mentioned_market`/`prefix_hint` 가 **있었는데** 충돌·미발견으로 보류된
      행에는 ③④⑤ 중 **아무것도** 태우지 않는다. 거긴 '증거가 갈렸다'는 뜻이고, 갈린 증거를
      본문/제품명/상속으로 덮으면 개체연결이 내린 보류 판정을 조용히 뒤집는 것이 된다.
      KB 미등재 오버레이가 정확히 이 성질을 이용한다 — `ㅍㅍㄹ 애플크림머핀` 은 접두가
      떨어져 나가되 해소는 실패하므로, 상속도 역인덱스도 꺼진다.
    ⛔ 역인덱스는 **1층 층만** 본다(`include_registry=False` 가 수집 경로의 기본) —
      레지스트리는 사람이 승격한 목록이 아니라 유도된 후보다.
    """
    threshold = settings.link_abstain_threshold

    # --- 제품 (KB products[] 미시드 → 검증 불가, 보류) ---
    # ⚠️ 마켓보다 **먼저** 푼다. 역인덱스의 조회 키는 약칭 정규화가 끝난 표면형이어야 하는데,
    #   마켓 분기 뒤로 미루면 정규화 전 이름으로 조회하게 되어 `data/product_aliases.json`
    #   시드가 역인덱스에만 안 먹는 절름발이가 된다.
    #
    # 제품명에 섞여 들어온 마켓 접두를 **여기서** 뗀다(`split_market_prefix`). 추출 프롬프트는
    # '제품명에 마켓 초성을 섞지 마라'고 말만 하고 강제가 없었다 — 실측(2026-08-10, 아모스갤
    # 813행): 순수 초성 제품명 26행/15종 + `초성+제품명` 11행/9종. 안 떼면 그 이름으로는
    # `specs` 조인도 다른 조각과의 집계도 영영 안 된다.
    # ⚠️ 약칭 사전은 **뗀 뒤의 이름**으로 찾는다. 접두가 붙은 채로 조회하면
    #   `data/product_aliases.json` 의 사람 시드가 접두 붙은 행에만 조용히 안 먹는다.
    product = None
    pconf = 0.0
    prefix_hint = None
    if mentioned_product:
        prefix_hint, stripped = split_market_prefix(mentioned_product, kb, unregistered)
        if stripped:
            # ⚠️ `aliases` 는 **이미 마켓별로 좁혀진** 표(`load_product_aliases()[market_word]`)다.
            #   이 함수는 그걸 주입받기만 하고 **직접 읽지 않는다** — 마켓을 아는 건 호출부다.
            # ⛔ 전 마켓 표를 합쳐 넘기지 말 것. 같은 약칭이 마켓마다 다른 제품을 가리키므로
            #   전역 적용은 알려진 **과잉 병합** 경로다(쿨라임 사고: 쿨라임→지나 별칭을 전역으로
            #   쓰면 다른 마켓의 동명 제품까지 지나 제품으로 접힌다).
            #   그래서 디시처럼 마켓이 자주 NULL 인 소스에선 약칭이 **적용되지 않는 게 정상**이다 —
            #   그 해결책은 전역 적용이 아니라 마켓 커버리지를 올리는 것(Phase 1)이다.
            product = (aliases or {}).get(_strip(stripped), stripped)
        # stripped 가 None = 이름 전체가 마켓 표기였다 → 제품은 보류(행은 남는다).

        # 종류어·재료어·조각난 이름·자모뿐인 이름은 제품이 아니다 — **접두를 뗀 뒤에** 건다.
        # ⛔ **순서가 규칙의 일부다.** 단어 게이트를 접두 분리보다 **앞**에 두면 두 가지가 깨진다:
        #     · `ㅇㅊ` 같은 맨몸 마켓 표기가 '자모뿐인 이름'으로 먼저 걸려 제품만 비워지고,
        #       그 안의 **마켓 신호가 통째로 버려진다**. 그러면 그 행은 스레드 도장 마켓을
        #       0.95(직접 매칭)로 물려받는다 — 틀린 마켓이 **되돌릴 표식도 없이** 남는 셈이라
        #       NULL 보다 나쁘다(출처 편향 왜곡).
        #     · `ㅂㅇㅍ 빨대` 의 나머지 `빨대` 가 종류어인데 게이트를 이미 지나쳐 제품으로 남는다.
        #   실측(2026-08-10): 두 방향 다 재현됐다. 그래서 **분리와 단어 게이트는 한 곳에서
        #   이 순서로만** 돈다 — `pipeline.dc_attribution_target`(적재분 복구)도 같은 순서다.
        if product:
            from . import extract          # 지연 임포트 — 이 모듈은 임포트 시점에 무의존이어야 한다
            if extract.is_non_product_word(product, known_products):
                product = None

    # --- 마켓 ---
    # ①② 항목 자신의 증거. 원문이 마켓을 말했으면 그게 먼저고, 접두 힌트는 **비어 있을
    # 때만** 승격한다 — 말한 마켓을 제품명 접두로 덮으면 근거가 약한 쪽이 강한 쪽을 이긴다.
    own = mentioned_market or prefix_hint
    from_prefix = not mentioned_market and bool(prefix_hint)
    market, mconf, candidates, reason = None, 0.0, [], "마켓 미언급"
    if own:
        hits, mconf, how = kb.resolve_market(own)
        candidates = [m["market_word"] for m in hits]
        if from_prefix and len(hits) == 1:
            # 전용 확신도로 갈아 끼운다 — 롤백의 유일한 열쇠(위 `PREFIX_CONFS` 주석).
            mconf = PREFIX_CONF_SURFACE if how == "표면형" else PREFIX_CONF_CHOSEONG
        how = f"{REASON_PREFIX} {how}" if from_prefix else how
        if len(hits) == 1 and mconf >= threshold:
            market, reason = hits[0]["market_word"], f"{how} 단일매칭"
        elif len(hits) > 1:
            # 초성 충돌(`ㅁㅁ`)은 접두에서 왔든 원문에서 왔든 똑같이 보류다 — 접두라고 해서
            # 갈린 증거가 하나로 모이지 않는다. 대신 제품명에서는 이미 떼어 냈다.
            market, reason = None, f"{how} 충돌({len(hits)}후보)→보류"
        else:
            market, reason = None, "미발견→보류"

    # ③④⑤ — **항목이 아무 말도 안 했을 때만** 내려온다.
    # ⛔ `own` 이 있었는데 보류된 행은 여기 안 들어온다. 그건 '증거가 갈렸다'는 뜻이고,
    #   갈린 증거를 본문/제품명/상속으로 덮으면 개체연결의 보류 판정을 조용히 뒤집는다.
    #   KB 미등재 오버레이가 이 성질에 기대어 오귀속을 막는다(§5.2).
    if not own:
        # ⓪ 이 **조각 자신의** 글 마켓(상속이 아닌 경우). `doc["market"]` 이 이 조각의 추출물
        #    이면 그건 항목 증거에 준한다 — 형제 조각에서 온 값이 아니다.
        if fallback_market and not inherited:
            hits, mconf, how = kb.resolve_market(fallback_market)
            candidates = [m["market_word"] for m in hits]
            if len(hits) == 1 and mconf >= threshold:
                market, reason = hits[0]["market_word"], f"{how} 단일매칭"
            elif len(hits) > 1:
                market, reason = None, f"{how} 충돌({len(hits)}후보)→보류"
            else:
                mconf, reason = 0.0, "미발견→보류"

        # ③ 조각 본문이 **정확히 하나**의 마켓을 지목했나. 여럿이면(비교글) 아무것도 안 한다 —
        #    '막는다'가 아니라 '만들지 않는다'이고, 그게 되돌린 행 스코프 가드와 다른 점이다.
        if market is None and text_markets is not None and len(text_markets.unique) == 1:
            mw = next(iter(text_markets.unique))
            mconf = (REPAIR_CONF_PIECE_SURFACE
                     if text_markets.how.get(mw) == "표면형" else REPAIR_CONF_PIECE_CHOSEONG)
            market, candidates = mw, [mw]
            reason = f"{REASON_PIECE_SCAN} 단일매칭"

        # ④ 제품→마켓 역인덱스(A1). **상속보다 먼저** 본다 — 1층 유일소유는 판매자 본인
        #    캡션에서 온 값이고, 상속은 형제 조각에서 온 값이다.
        if market is None and product and inversion is not None:
            inv_market, inv_conf, inv_reason = inversion.market_for(product)
            # **덧붙인다, 갈아치우지 않는다** — 갈아치우면 '왜 마켓이 비어 있었는가'(미언급)가
            # 사라져 제외·모호로 보류된 행의 사유가 반쪽만 남는다.
            if inv_reason:
                reason = f"{reason} | {inv_reason}"
            if inv_market:
                market, mconf, candidates = inv_market, inv_conf, [inv_market]

        # ⑤ 스레드 상속. `link_post` 가 **제목이 그 마켓을 선언한 글에서만** 넘긴다(§6.3) —
        #    제목에 마켓이 없는 나열/첨삭 글에서 한 조각의 마켓이 스레드 전체에 도장 찍히는
        #    사고(스레드 200743, 19행)를 구조적으로 막는 자리다.
        if market is None and fallback_market and inherited:
            hits, _c, how = kb.resolve_market(fallback_market)
            if len(hits) == 1:
                market, mconf = hits[0]["market_word"], INHERIT_CONF
                candidates = [market]
                reason = f"{REASON_INHERIT} {how} 단일매칭"
            elif len(hits) > 1:
                candidates = [m["market_word"] for m in hits]
                reason = f"{REASON_INHERIT} {how} 충돌({len(hits)}후보)→보류"

    if product:
        reason += " | 제품:KB미시드→미검증"

    abstained = market is None
    return LinkResult(market, round(mconf, 3), product, pconf, abstained,
                      candidates, reason.strip(" |"))


def link_post(doc: dict, *, kb: KB, aliases: Optional[dict[str, str]] = None,
              inversion: Optional[MarketInversion] = None,
              unregistered=None, known_products=None,
              text_markets: Optional[ScanResult] = None,
              title_markets: Optional[ScanResult] = None) -> list[LinkResult]:
    """
    extract.py 의 후기 1건(dict) → 제품별 LinkResult 리스트.
    제품은 reviews[]별로 매핑하고, 마켓도 **항목이 자기 것을 말했으면 그걸 먼저** 쓴다
    (`reviews[].mentioned_market`). 최상위 `doc['market']` 은 그 항목이 아무 말도 안 했을 때의
    폴백이다 — 보통 1주문=1마켓이라 대개 이쪽이 쓰이지만, 여러 마켓을 나열한 글에서는
    항목 값이 이긴다(`link` 독스트링의 순서 참조).

    **상속 권위는 제목이 갖는다**(2026-08-11). `doc["market"]` 이 스레드 상속분
    (`_market_inherited`)인데 **제목이 그 마켓을 지목하지 않으면 넘기지 않는다.**
    실측: 제목에 마켓이 없는 나열 글(스레드 200743 `인생슬 적구 가`)에서 한 댓글의 `ㅂㅉ`
    하나가 그 글 20행 전부를 빈짱으로 만들었다 — 실제로는 `ㅂㅇㅍ`·`ㅍㅅㅌㄹ`·`ㅅㄹㄹ`·
    `ㅇㅉ`·`ㅇㅇㅈ`·`ㅇㅊ` 가 섞인 글이고 1층 소유도 넷으로 갈린다.
    ⚠️ 상속을 **통째로 없애지는 않는다** — 제목이 마켓을 선언한 글(`ㅂㅇㅍ 후기`)에서는
      마켓을 안 밝힌 댓글이 여전히 글의 값을 받아야 개체연결이 선다
      (게이트: `test_post_market_still_fills_empty_pieces` 와 같은 방향의 과잉수정 가드).

    text_markets / title_markets: 조각 경계에서 **한 번** 계산한 `markets_in_text` 결과.
      ⚠️ 파생 계산은 doc 표식이 아니라 **명시 인자**다 — 비공개 표식이 셋 넘어가면 doc 이
        무엇을 계약하는지가 흐려진다. 추출이 만든 사실(`_market_inherited`·`_thread_title`)
        만 표식이고, 스캔은 KB 가 필요해 추출층이 만들 수 없다.
      `text_markets` 는 조각 본문이 필요한데 doc 엔 본문이 없다 → **호출부가 준다**
        (`index.index_post` 가 `raw.text` 로 만든다). 안 주면 ③ 사다리가 꺼진다(무해).
      `title_markets` 는 `doc["_thread_title"]` 에서 여기서 만든다.

    ⚠️ 역인덱스는 **제품별**로 갈릴 수 있다 — 마켓을 안 밝힌 비교글이면 제품마다 다른
      마켓이 붙는다. 그게 옳다: 그런 글의 각 항목은 실제로 서로 다른 마켓의 제품이다.
    """
    market = doc.get("market")
    # `_market_inherited` 는 `extract.extract_collected` 가 다는 **비공개 표식**이다 —
    # 이 doc 의 market 이 조각 자신의 추출물이 아니라 스레드 글에서 물려받은 값이라는 뜻.
    # 값을 옮기지 않고 표식만 다는 이유: `doc["market"]` 은 이미 여러 곳이 읽는 계약이고,
    # 옮기면 상속분이 조용히 사라진다(그 회귀는 `eval/test_extract_thread.py` 가 잡는다).
    inherited = bool(doc.get("_market_inherited"))
    unreg = (load_unregistered_market_tokens() if unregistered is None
             else frozenset(unregistered))
    if title_markets is None:
        title_markets = markets_in_text(doc.get("_thread_title") or "", kb,
                                        unregistered=unreg, known_products=known_products)
    if inherited and market:
        # ⚠️ `doc["market"]` 은 추출기 표면형(`ㅂㅉ`)이고 `title_markets.unique` 는 정규
        #   `market_word`(`빈짱`)다 — 정규화 없이 비교하면 **모든 초성 상속이 차단된다**
        #   (개발 중 실제로 그렇게 짰다). 못 푸는 표면형은 어차피 `link()` 의 ⑤가 보류하므로
        #   여기서 막지 않는다 — 막으면 표식이 '제목 때문'이라고 거짓말한다.
        hits, _c, _h = kb.resolve_market(market)
        resolved = hits[0]["market_word"] if len(hits) == 1 else None
        if resolved and resolved not in title_markets.unique:
            # 제목이 이 마켓을 선언하지 않았다 → 상속 권위 없음. 조용히 버리지 않고 표식을
            # 남긴다(무음 금지 — 상속 커버리지가 움직였을 때 사후에 못 가른다).
            doc["_inherit_blocked_by_title"] = market
            market = None

    # **스레드 단위 차단** — 이 조각(또는 그 스레드 제목)이 가리키는 마켓이 **미등재 토큰뿐**
    # 이면 제품명으로 마켓을 추론하지 않는다. 감사 D2c 가 정확히 이 자리다: 스레드는 `ㅋㅋㅁ`
    # 후기인데 `진저크런키` 라는 이름이 지나 1층과 겹쳐 **그 행만** 지나로 채워졌고, 같은 글의
    # 다른 제품은 전부 NULL 이라 **한 글 안에서 마켓이 갈렸다**.
    # ⚠️ 접두 분리만으로는 못 막는다 — 그 행의 제품명엔 접두가 없었다(`진저크런키` 뿐이다).
    #   미등재 신호는 **조각/제목 본문**에만 있으므로 차단도 그 스코프여야 한다.
    # ⚠️ KB 마켓을 하나라도 지목했으면 차단하지 않는다 — 미등재 마켓 얘기가 스쳐 지나간
    #   정상 조각까지 끄면 되돌린 가드와 같은 '조용한 차단'이 된다.
    scans = [s for s in (text_markets, title_markets) if s is not None]
    if inversion is not None and any(s.unknown for s in scans) \
            and not any(s.unique for s in scans):
        doc["_inversion_blocked_by_unregistered"] = sorted(
            {t for s in scans for t in s.unknown})
        inversion = None

    return [link(r.get("mentioned_market"), r.get("mentioned_product"), kb=kb,
                 aliases=aliases, inversion=inversion, fallback_market=market,
                 inherited=inherited, unregistered=unreg,
                 known_products=known_products, text_markets=text_markets)
            for r in doc.get("reviews", [])]


if __name__ == "__main__":
    kb = load_kb()

    # 골드(한글과자한줌 비교글)의 두 항목 — ㅂㅉ → 빈짱 확정, 제품은 보류
    for prod in ("한글과자한줌", "과일사탕한줌"):
        r = link("ㅂㅉ", prod, kb=kb)
        print(f"ㅂㅉ / {prod:8} → market={r.market} conf={r.market_confidence} "
              f"product={r.product} abstain={r.abstained} | {r.reason}")

    # 표면형(full word)도 같은 결과
    r = link("빈짱", "한줌", kb=kb)
    print(f"빈짱 / 한줌      → market={r.market} conf={r.market_confidence} | {r.reason}")

    # 일반어는 마켓 아님 → 미발견 보류
    r = link("자사몰", "한줌", kb=kb)
    print(f"자사몰 / 한줌    → market={r.market} abstain={r.abstained} | {r.reason}")

    # 충돌 시뮬레이션: 가짜 KB로 두 마켓이 같은 초성을 가질 때 보류되는지
    fake = KB({"markets": [
        {"market": "머머슬라임", "market_word": "머머", "handle": "a", "handles_alt": [],
         "aliases": [], "choseong": "ㅁㅁ", "choseong_aliases": [], "products": []},
        {"market": "미미슬라임", "market_word": "미미", "handle": "b", "handles_alt": [],
         "aliases": [], "choseong": "ㅁㅁ", "choseong_aliases": [], "products": []},
    ]})
    r = link("ㅁㅁ", None, kb=fake)
    print(f"[충돌] ㅁㅁ       → market={r.market} conf={r.market_confidence} "
          f"abstain={r.abstained} candidates={r.candidates} | {r.reason}")
