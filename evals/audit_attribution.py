# -*- coding: utf-8 -*-
"""
추출 파이프라인 마켓/제품 귀속 감사 하네스 (Phase 0, US-001) —
`.omc/plans/2026-08-10-extraction-attribution-fixes.md` 의 "Requirements summary" 표와
"Phase 0" 절이 지정한 측정을 그대로 코드로 옮긴다.

무엇을 재는가: 마켓/제품 귀속 결함 7종의 **현재 규모**를 소스별(amos/instagram)로 집계해
하나의 복붙 가능한 요약 블록으로 낸다. Phase 1~4 의 각 수정이 이 숫자를 어디까지 줄였는지
재실행으로 확인하는 before/after 기준선(baseline)이 이 스크립트의 존재 이유다 — 그래서
라벨은 두 소스 블록에서 **한 글자도 다르지 않게** 정렬해 diff 가 그대로 비교가 되게 한다.

무과금·읽기전용:
  · LLM 호출 없음 — `slime_rag.llm_ops`/OpenAI 무의존. `.env` 자동주입(`slime_rag.config`)이
    실과금을 낼 수 있으므로 항상 `OPENAI_API_KEY=""` 로 실행할 것.
  · DB 읽기전용 — `slime_rag.db.connect()` 로 연결하고 SELECT 만 수행한다. commit() 없음.

실행:
    OPENAI_API_KEY="" python evals/audit_attribution.py
    OPENAI_API_KEY="" python evals/audit_attribution.py --source amos
    OPENAI_API_KEY="" python evals/audit_attribution.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# 직접 실행(python evals/audit_attribution.py) 시 repo 루트를 경로에 추가(-m 없이도 동작).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slime_rag import extract
from slime_rag.db import connect
from slime_rag.linking import KB, load_kb, split_market_prefix

SOURCES = ("amos", "instagram")

# 계획 §Phase2 가 지정한 접착제/베이스 재료 어휘(1층 base_combo 어휘에서 소싱) — 정확일치만.
GLUE_WORDS = frozenset({
    "글루올", "택키", "아마존", "우드", "우마존", "생베", "점토", "화이트글루", "글리",
})
# "이름을 까먹었다" 명시 마커 — 부분일치(포함)로 잡는다.
FRAGMENT_MARKERS = ("어쩌구", "어쩌고", "어쩍고")

# 붙여 쓴 호환 자모(초성) 런 스캔 — `linking._JAMO_RUN_RE`(^로 시작만 봄)와 달리 텍스트
# 어디에 있든 전부 찾아야 하므로 앵커 없이 별도 정의한다.
_JAMO_RUN_RE = re.compile(r"[ㄱ-ㅎ]+")


def _strip(s: str | None) -> str:
    return "".join((s or "").split())


def _market_surface_forms(kb: KB) -> list[str]:
    """KB 전 마켓의 표면형(마켓명·약칭·핸들) 목록 — 길이 2 미만은 오탐 위험이 커 제외."""
    forms: set[str] = set()
    for m in kb.markets:
        for f in KB._surface_forms(m):
            sf = _strip(f)
            if len(sf) >= 2:
                forms.add(sf)
    return sorted(forms)


def _markets_in_text(text: str, kb: KB, forms: list[str]) -> set[str]:
    """텍스트에 등장하는 KB 마켓(정규 market_word) 집합.

    표면형은 부분일치로 후보를 찾고, 초성은 자모 런 단위로 후보를 찾는다 — 어느 쪽이든
    최종 인정 여부는 **`KB.resolve_market` 한 곳**에 위임한다(len(candidates)==1 일 때만
    인정). 그게 "unique-초성" 규칙이다: `ㅈㄴ` 처럼 여러 마켓과 충돌하는 초성은
    candidates>1 이 되어 자동으로 걸러진다 — 지나(ㅈㄴ)와 부사 '존나'를 갈라내는 바로 그 규칙.
    """
    if not text:
        return set()
    hits: set[str] = set()
    low = text.lower()
    for form in forms:
        if form.lower() not in low:
            continue
        cands, _conf, _how = kb.resolve_market(form)
        if len(cands) == 1:
            hits.add(cands[0]["market_word"])
    for run in _JAMO_RUN_RE.findall(text):
        cands, _conf, _how = kb.resolve_market(run)
        if len(cands) == 1:
            hits.add(cands[0]["market_word"])
    return hits


def _prefix_containment_pairs(products: list[str]) -> int:
    """distinct 제품명 중 한쪽이 다른쪽의 strict prefix 인 쌍의 수(무순서쌍, 1회만 계산).

    비교는 공백제거+casefold 정규화 후 수행한다(표기 흔들림 흡수) — "요아곰 밀키크림파르페"
    vs "요아곰밀키크림파르페" 같은 공백차만 있는 쌍도 여기서 걸린다.
    """
    norm = sorted({_strip(p).casefold() for p in products if p})
    n = len(norm)
    pairs = 0
    for i in range(n):
        a = norm[i]
        for j in range(i + 1, n):
            b = norm[j]
            if a == b or not a:
                continue
            shorter, longer = (a, b) if len(a) < len(b) else (b, a)
            if shorter and longer.startswith(shorter):
                pairs += 1
    return pairs


def audit_source(conn, source: str, kb: KB, forms: list[str], specs_products: set[str]) -> dict:
    """소스 하나(amos|instagram)를 감사해 지표 dict 를 낸다. SELECT 만 수행(읽기전용)."""
    cur = conn.execute(
        "SELECT market, market_confidence, product, evidence, body, title, post_id, "
        "       attributes "
        "  FROM reviews WHERE source = %s",
        (source,),
    )
    rows = cur.fetchall()

    n_total = len(rows)
    products = [r[2] for r in rows if r[2]]
    n_distinct_products = len(set(products))
    # 행 단위 카운트(중복 제품명도 각 행으로 센다) — distinct 명 수와는 다른 지표다.
    n_not_in_specs = sum(1 for p in products if p not in specs_products)

    # --- 마켓: agree / mismatch / fillable / multi(텍스트가 명명하는 마켓 스캔) ---
    agree = mismatch = fillable = multi = 0
    for market, _conf, _product, _evidence, body, title, _post_id, _attrs in rows:
        text = body or title or ""
        detected = _markets_in_text(text, kb, forms)
        if len(detected) >= 2:
            multi += 1
        if market:
            if market in detected:
                agree += 1
            elif detected:
                mismatch += 1
        elif len(detected) == 1:
            fillable += 1

    # --- 제품명에 낀 마켓 초성/표면형(`split_market_prefix` 재사용, 재구현 금지) ---
    bare_market_token = compound_market_token = 0
    for product in products:
        hint, remainder = split_market_prefix(product, kb)
        if hint is None:
            continue
        if remainder is None:
            bare_market_token += 1
        else:
            compound_market_token += 1

    # --- TYPE_ENUM / 접착제(glue) / 조각(fragment) 제품명 ---
    type_enum_set = set(extract.TYPE_ENUM)
    type_enum_products = glue_products = fragment_products = 0
    for product in products:
        p = product.strip()
        if p in type_enum_set:
            type_enum_products += 1
        if p in GLUE_WORDS:
            glue_products += 1
        if any(marker in p for marker in FRAGMENT_MARKERS):
            fragment_products += 1

    # --- NULL-product 중복그룹/잉여행 ((source, post_id) 그룹, post_id NULL 은 제외) ---
    # DB UNIQUE(source, post_id, product) 제약 자체가 post_id NULL 행은 서로 다른 값으로
    # 취급해 절대 충돌시키지 않는다(schema.sql 주석) — 그래서 여기서도 post_id NULL 행을
    # 같은 그룹으로 묶지 않는다(묶으면 서로 무관한 행들이 거대한 가짜 중복그룹이 된다).
    n_null_product = sum(1 for r in rows if r[2] is None)
    null_no_post_id = 0
    post_id_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        if r[2] is not None:
            continue
        post_id = r[6]
        if post_id is None:
            null_no_post_id += 1
            continue
        post_id_counts[post_id] += 1
    dup_groups = sum(1 for n in post_id_counts.values() if n > 1)
    surplus_rows = sum(n - 1 for n in post_id_counts.values() if n > 1)

    # 그중 **내용까지 같은** 잉여행 — 이게 실제로 고쳐야 할 결함이다.
    # ⚠️ 위 `surplus_rows` 를 '고칠 대상'으로 읽지 말 것. 한 조각이 제품명을 못 정한 항목을
    #   여럿 낼 수 있고(비교글에서 이름이 안 잡힌 제품 둘), 그건 **서로 다른 의견**이라
    #   합치면 안 된다(원칙 2 — 과잉 병합 금지). 추출기 말더듬만이 결함이고, 그건 내용이
    #   글자 하나까지 같다 — `extract._fold_by_product` 가 접는 기준과 같은 지문을 쓴다.
    fp_counts: dict[tuple, int] = defaultdict(int)
    for r in rows:
        if r[2] is None and r[6] is not None:
            fp_counts[(r[6], extract._held_fingerprint(r[7] or {}))] += 1
    identical_surplus = sum(n - 1 for n in fp_counts.values() if n > 1)

    # --- 제품명 접두-포함 별칭 후보쌍 ---
    prefix_pairs = _prefix_containment_pairs(products)

    # --- 교차성 결함: evidence 헤더 모순 / market_confidence=0-with-market ---
    # `evidence LIKE '[마켓미상%'` 과 동형(선행 % 없음 = 접두 일치) — Python 쪽 문자열
    # 이스케이프/파라미터 문제를 피하려고 fetch 한 evidence 로 직접 판정한다.
    evidence_header_contra = sum(
        1 for r in rows if r[0] is not None and (r[3] or "").startswith("[마켓미상")
    )
    conf_zero_with_market = sum(
        1 for r in rows if r[0] is not None and r[1] == 0
    )

    return {
        "n_total": n_total,
        "n_distinct_products": n_distinct_products,
        "n_product_not_in_specs": n_not_in_specs,
        "market_agree": agree,
        "market_mismatch": mismatch,
        "market_fillable": fillable,
        "market_multi": multi,
        "product_bare_market_token": bare_market_token,
        "product_compound_market_token": compound_market_token,
        "product_type_enum": type_enum_products,
        "product_glue_word": glue_products,
        "product_fragment_marker": fragment_products,
        "null_product_rows": n_null_product,
        "null_product_no_post_id": null_no_post_id,
        "null_product_dup_groups": dup_groups,
        "null_product_surplus_rows": surplus_rows,
        "null_product_identical_surplus": identical_surplus,
        "prefix_containment_pairs": prefix_pairs,
        "evidence_header_contradictions": evidence_header_contra,
        "market_confidence_zero_with_market": conf_zero_with_market,
    }


LABELS = [
    ("n_total", "전체 행수"),
    ("n_distinct_products", "product NOT NULL distinct 제품명 수"),
    ("n_product_not_in_specs", "  └ 그중 specs 에 전혀 안 조인(제품명 단독매칭)"),
    ("market_agree", "마켓 agree(저장값 ∈ 텍스트내 마켓)"),
    ("market_mismatch", "마켓 mismatch(저장값 ∉ 텍스트내 마켓, 텍스트는 ≥1건 명명)"),
    ("market_fillable", "마켓 fillable(NULL + 텍스트가 유일 마켓 1개만 명명)"),
    ("market_multi", "마켓 multi(텍스트가 ≥2개 KB 마켓 명명)"),
    ("product_bare_market_token", "제품명=마켓 초성/표면형 단독(bare)"),
    ("product_compound_market_token", "제품명='마켓토큰 나머지'(compound)"),
    ("product_type_enum", "제품명=TYPE_ENUM 종류어(정확일치)"),
    ("product_glue_word", "제품명=접착제/베이스 재료어(정확일치)"),
    ("product_fragment_marker", "제품명=조각 마커(어쩌구/어쩌고/어쩍고 포함)"),
    ("null_product_rows", "product IS NULL 행수"),
    ("null_product_no_post_id", "  └ 그중 post_id 도 NULL(중복판정 제외)"),
    ("null_product_dup_groups", "  └ (source, post_id) 중복그룹 수(n>1)"),
    ("null_product_surplus_rows", "  └ 잉여행(그룹별 n-1 합 · 다른 의견 포함)"),
    ("null_product_identical_surplus", "  └ 그중 **내용까지 동일**(말더듬=실결함)"),
    ("prefix_containment_pairs", "제품명 접두-포함 별칭 후보쌍(무순서, 1회)"),
    ("evidence_header_contradictions", "evidence 헤더 모순([마켓미상 인데 market 有)"),
    ("market_confidence_zero_with_market", "market_confidence=0 인데 market 有"),
]
_LABEL_WIDTH = max(len(label) for _key, label in LABELS)


def print_report(results: dict[str, dict]) -> None:
    print("=" * 72)
    print("추출 파이프라인 귀속 감사 (Phase 0 기준선)")
    print("=" * 72)
    for source in SOURCES:
        if source not in results:
            continue
        r = results[source]
        print(f"\n[{source}]")
        for key, label in LABELS:
            print(f"  {label:<{_LABEL_WIDTH}} : {r[key]}")
    if len(results) == len(SOURCES):
        total_evidence = sum(results[s]["evidence_header_contradictions"] for s in SOURCES)
        total_conf_zero = sum(results[s]["market_confidence_zero_with_market"] for s in SOURCES)
        print(f"\n[합계 — 소스 무관 교차성 결함]")
        label = "evidence 헤더 모순([마켓미상 인데 market 有)"
        print(f"  {label:<{_LABEL_WIDTH}} : {total_evidence}")
        label = "market_confidence=0 인데 market 有"
        print(f"  {label:<{_LABEL_WIDTH}} : {total_conf_zero}")
    print("-" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description="마켓/제품 귀속 결함 감사(LLM 0회, DB 읽기전용)")
    ap.add_argument("--source", choices=[*SOURCES, "both"], default="both",
                     help="감사할 소스(기본: both)")
    ap.add_argument("--json", action="store_true", help="사람이 읽는 리포트 대신 JSON 덤프")
    args = ap.parse_args()

    targets = list(SOURCES) if args.source == "both" else [args.source]

    kb = load_kb()
    forms = _market_surface_forms(kb)

    results: dict[str, dict] = {}
    with connect() as conn:
        # 1층 스펙 제품명 집합 — market 이 NULL 인 후기가 많아(45%) (market,product) 조인은
        # 그런 행을 전부 놓친다. 여기선 의도적으로 **제품명만**으로 매칭해 "1층에 이 이름이
        # 아예 없다"를 측정한다 — 마켓 불일치와 제품 부재를 한 숫자로 섞지 않기 위해서다.
        specs_products = {row[0] for row in conn.execute("SELECT DISTINCT product FROM specs")}
        for source in targets:
            results[source] = audit_source(conn, source, kb, forms, specs_products)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
