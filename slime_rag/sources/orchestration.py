# -*- coding: utf-8 -*-
"""검색어 확장 + 다중 소스 오케스트레이션."""

from __future__ import annotations

from .base import RawReview, Source, log


# ---------------------------------------------------------------- 검색어 확장
def expand_queries(product: str,
                   *,
                   aliases: list[str] | None = None,
                   market_word: str | None = None,
                   include_market_only: bool = True) -> list[str]:
    """
    제품 검색을 '유도리 있게' 확장한다 — 디시 제목 표기가 제각각이라(풀네임/약칭/
    마켓초성+제품/마켓초성 단독) 한 키워드로는 recall 이 샌다.

    예) expand_queries("허니푸냥이", aliases=["푸냥이"], market_word="봄")
        → ["허니푸냥이", "푸냥이", "ㅂ 허니푸냥이", "ㅂ 푸냥이", "ㅂ"]

    우선순위(앞일수록 먼저 검색 = limit 우선 소진): 구체적 제품 → 마켓초성+제품 →
    마켓초성 단독(가장 광범위·노이즈 많음, 맨 뒤). `collect()` 가 URL 로 키워드 간
    중복을 제거하므로 변형이 겹쳐도 안전하다.
    'ㅂ' 같은 단독 마켓 검색이 과하면 include_market_only=False 로 끈다.
    """
    names = [product] + [a for a in (aliases or []) if a and a != product]
    out: list[str] = []

    def add(t: str) -> None:
        t = " ".join(t.split())                 # 공백 정규화
        if t and t not in out:
            out.append(t)

    for n in names:                             # 1) 제품 풀네임 + 약칭
        add(n)

    if market_word:
        from ..linking import choseong          # '봄' → 'ㅂ' (마켓 표기 환원)
        cho = choseong(market_word)
        if cho:
            for n in names:                     # 2) 마켓초성 + 제품 ("ㅂ 허니푸냥이")
                add(f"{cho} {n}")
            if include_market_only:             # 3) 마켓초성 단독 (광범위 recall, 맨 뒤)
                add(cho)
    return out


# ---------------------------------------------------------------- 오케스트레이션
def collect_all(sources: list[Source], keywords: list[str], per_source_limit: int = 100,
                target: dict | None = None) -> list[RawReview]:
    """여러 소스를 동일 인터페이스로 수집. 나중에 네이버/유튜브 추가 시 리스트에만 넣으면 됨.

    target = {"market": str|None, "slime": str} — 2층 관련성 게이트 앵커(계획 Step 6). 주입하면
    소스별 게이트가 이 타깃으로 KEEP/DROP 을 판정한다. None 이면 각 소스가 keywords[0]로 폴백
    (하위호환); 1층 소스(InstagramProfileSource 등)는 target 을 무시한다(D6 불변식).
    """
    out: list[RawReview] = []
    for src in sources:
        try:
            out.extend(src.collect(keywords, limit=per_source_limit, target=target))
        except NotImplementedError:
            log.info("%s: 미구현 — 스킵", src.platform)
        except Exception as e:
            log.exception("%s 수집 실패: %s", src.platform, e)
    return out
