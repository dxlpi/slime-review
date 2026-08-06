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
from dataclasses import dataclass, field
from typing import Optional

from .config import settings, ROOT

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


# ---------------------------------------------------------------- 연결
def link(
    mentioned_market: Optional[str],
    mentioned_product: Optional[str],
    *,
    kb: KB,
    aliases: Optional[dict[str, str]] = None,
) -> LinkResult:
    """
    (mentioned_market, mentioned_product) → LinkResult.
    마켓은 결정적 매칭 + 충돌→abstain. 제품은 KB 미시드라 보류(표면형만 보존).
    aliases: 제품 약칭 사전(예: {'몽땅':'사과몽땅'}). 없으면 표면형 그대로.
    """
    threshold = settings.link_abstain_threshold

    # --- 마켓 ---
    if not mentioned_market:
        market, mconf, candidates, reason = None, 0.0, [], "마켓 미언급"
    else:
        hits, mconf, how = kb.resolve_market(mentioned_market)
        candidates = [m["market_word"] for m in hits]
        if len(hits) == 1 and mconf >= threshold:
            market, reason = hits[0]["market_word"], f"{how} 단일매칭"
        elif len(hits) > 1:
            market, reason = None, f"{how} 충돌({len(hits)}후보)→보류"
        else:
            market, reason = None, "미발견→보류"

    # --- 제품 (KB products[] 미시드 → 검증 불가, 보류) ---
    product = None
    pconf = 0.0
    if mentioned_product:
        surface = (aliases or {}).get(_strip(mentioned_product), mentioned_product)
        product = surface                      # 잠정 표면형 — 다운스트림 그룹핑용
        reason += " | 제품:KB미시드→미검증"

    abstained = market is None
    return LinkResult(market, round(mconf, 3), product, pconf, abstained,
                      candidates, reason.strip(" |"))


def link_post(doc: dict, *, kb: KB, aliases: Optional[dict[str, str]] = None) -> list[LinkResult]:
    """
    extract.py 의 후기 1건(dict) → 제품별 LinkResult 리스트.
    마켓은 후기 단위(doc['market']) 하나를 모든 제품에 공유. 제품은 reviews[]별로 매핑.
    """
    market = doc.get("market")
    return [link(market, r.get("mentioned_product"), kb=kb, aliases=aliases)
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
