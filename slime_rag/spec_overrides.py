# -*- coding: utf-8 -*-
"""
1층 스펙 **사람 검수 오버레이** — 순수 정책 + 파일 IO. DB·네트워크·LLM 무의존.

왜 있는가: 캡션 추출은 **판매자가 안 쓴 것을 만들어낼 수 없다**(1급 규칙: 미언급 → null,
지어내기 금지). 판매자가 풀조합·향·종류·질감을 캡션에 안 적으면 `specs` 의 그 칸은 LLM 을
몇 번 더 돌려도 영구히 NULL 이다 — **추출의 한계가 아니라 입력의 부재**다. 유일한 해법은
사람이 게시물(사진·영상)을 보고 직접 넣는 것이고, 그 사람의 판단이 사는 곳이 여기다.

**왜 DB 가 아니라 파일인가**([ADR-0016](../docs/adr/0016-human-in-the-loop-spec-review.md)):
`specs` 는 **파생 테이블**이다 — `pipeline.setup(reset=True)` 가 `TRUNCATE reviews, specs` 하고
재추출이 다시 만든다. DB 에만 쓰면 손으로 채운 값이 리셋 한 번에 사라진다. 오버레이는
커밋되므로 리빌드 뒤 `pipeline.apply_spec_overrides()` 로 복원된다.
`data/product_aliases.json`·`evals/gold/boundary_rulings.json` 과 같은 계열이다
(사람의 판단은 코드가 아니라 데이터).

**ADR-0013 안전:** 파일에 담기는 건 사람이 타이핑한 값 + 덮기 전 값(`was`)뿐이고 **캡션 본문
바이트는 담지 않는다** — `data/product_registry.json` 이 커밋 가능한 것과 같은 이유다.

파일 모양(`data/spec_overrides.json`):

    {
      "_comment": "...",
      "베이퍼": {
        "와일드베리소프트콘": {
          "slime_type": {"value": "소프트콘", "was": null, "at": "2026-08-09", "note": null}
        }
      },
      "머머": {
        "위즈캔디샵": {
          "official_texture": {"value": null, "was": null, "at": "2026-08-09", "unknown": true}
        }
      }
    }

셀프테스트: python -m slime_rag.spec_overrides    (게이트: eval/test_spec_overrides.py)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import DATA_DIR

# 사람이 고칠 수 있는 칸 전부. `specs` 의 **DB 컬럼 이름**이다(`official_scent` 는 표시층 이름).
OVERRIDABLE = ("base_combo", "scent", "slime_type", "official_texture",
               "beads", "source_permalink")

# 그중 **큐를 띄우는** 칸(사용자 결정 2026-08-09). `beads` 는 빈 배열이 108행인데 대부분
# '없음'이 정답이라, 큐에 넣으면 대상이 3.5배가 되고 대부분 '없음 확인' 노동이 된다.
# `source_permalink` 도 큐를 띄우지 않는다 — 없으면 임베드만 못 뜨지 스펙이 빈 건 아니다.
# 둘 다 카드에서 **편집은 된다**. 큐 진입 조건과 편집 가능 범위는 다른 개념이다.
QUEUE_FIELDS = ("base_combo", "scent", "slime_type", "official_texture")

# 배열 칸 — `None` 이 아니라 **빈 배열**이 미언급이다(`pipeline._upsert_spec` 과 같은 규칙).
LIST_FIELDS = ("beads",)

DEFAULT_PATH = DATA_DIR / "spec_overrides.json"

# 파일 첫 줄에 항상 다시 써 넣는 자기설명. `load` 가 `_` 키를 버리므로 `save` 가 복원하지
# 않으면 왕복 한 번에 사라진다 — 사람이 직접 열어 보는 파일이라 설명이 남아 있어야 한다.
COMMENT = (
    "1층 스펙 사람 검수 오버레이(ADR-0016). 캡션에 없어서 추출이 못 채운 칸을 사람이 게시물을 "
    "보고 채운 값. specs 는 파생 테이블이라 리셋되면 사라지므로 판단은 여기에 산다. "
    "was=덮기 전 값(추적용), unknown=사람이 보고도 모른다고 판정(큐에서 영구 제외, 값은 계속 null)."
)


# ---------------------------------------------------------------- 파일 IO
def load(path: Path | str | None = None) -> dict:
    """오버레이 파일 → dict. 없으면 `{}`.

    `_` 로 시작하는 키는 메타(설명)라 버린다 — 레포 관례(`data/*.json` 의 `_note`/`_comment`).
    깨진 JSON 은 **조용히 무시하지 않고** 예외로 올린다: 이 파일이 안 읽히면 재수집이 사람
    검수를 되돌리는데(`pipeline._upsert_spec` 마스킹이 빈 dict 로 돌아간다), 그건 화면에
    아무 흔적도 남기지 않는 실패다.
    """
    p = Path(path or DEFAULT_PATH)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {m: v for m, v in data.items() if not m.startswith("_") and isinstance(v, dict)}


def save(data: dict, path: Path | str | None = None) -> None:
    """dict → 오버레이 파일. **원자적**(직렬화 먼저 → tmp → `os.replace`).

    사람이 39건을 한 건씩 채우는 파일이라, 쓰는 도중 죽어서 반쪽 JSON 이 남으면 그때까지의
    검수가 통째로 날아간다(다음 `load` 가 예외로 죽는다). 두 단계로 막는다:
      ① `json.dumps` 를 **먼저** — 직렬화 불가 값은 디스크를 건드리기 전에 터진다.
      ② tmp 파일에 쓰고 `os.replace` — 같은 파일시스템 안의 원자적 rename 이라
         부분 기록된 파일이 정본 자리에 놓이는 순간이 없다.
    """
    p = Path(path or DEFAULT_PATH)
    body = {"_comment": COMMENT}
    for market in sorted(data):
        if market.startswith("_"):
            continue
        body[market] = {product: data[market][product] for product in sorted(data[market])}
    text = json.dumps(body, ensure_ascii=False, indent=2) + "\n"      # ① 먼저 직렬화
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)                                               # ② 원자적 교체


# ---------------------------------------------------------------- 조회(순수)
def entry(data: dict, market: str | None, product: str | None) -> dict:
    """(마켓, 제품) → 그 제품의 오버레이 칸들. 없으면 `{}`."""
    return ((data or {}).get(market or "") or {}).get(product or "") or {}


def is_unknown(data: dict, market: str | None, product: str | None, field: str) -> bool:
    """그 칸이 '사람이 보고도 모름'으로 판정됐는가."""
    return bool(entry(data, market, product).get(field, {}).get("unknown"))


def is_blank(field: str, value) -> bool:
    """그 칸이 '미언급'인가. 배열 칸은 `None` 이 아니라 **빈 배열**이 미언급이다."""
    if field in LIST_FIELDS:
        return not (value or [])
    return value is None or value == ""


def apply(spec_row: dict, data: dict) -> dict:
    """스펙 행 + 오버레이 → **오버레이가 이긴 새 dict**. 입력은 건드리지 않는다(순수).

    `spec_row` 는 `specs` 의 컬럼 이름을 쓴다(`market`·`product` 포함 — 오버레이 조회 키다).

    ⚠️ **오버레이가 이긴다**가 이 함수의 존재 이유다. `_upsert_spec` 은 전 칸이
       `COALESCE(EXCLUDED.x, specs.x)` 라 **들어오는 값이 non-null 이면 기존 값을 덮는다** —
       사람이 채운 뒤 그 제품이 다른 캡션에서 다시 잡히면 LLM 값이 사람 값을 조용히
       되돌린다. 프로필 경로가 최신 ~12글만 주므로 같은 제품이 여러 글에 걸쳐 잡히는 건
       **정상**이라, 이건 이론적 위험이 아니라 예정된 사고다.

    ⚠️ `unknown` 칸은 **마스킹하지 않는다**. 사람이 '모르겠다'고 한 것은 값이 아니다 —
       나중에 판매자가 캡션에 적어서 LLM 이 그 칸을 채우면 그 값이 들어와야 한다.
       `unknown` 이 하는 일은 **큐에서 빼는 것 하나**뿐이다(1급 규칙 유지: 값을 만들지 않는다).
    """
    out = dict(spec_row or {})
    for field, rec in entry(data, out.get("market"), out.get("product")).items():
        if field not in OVERRIDABLE or not isinstance(rec, dict):
            continue
        if rec.get("unknown"):
            continue
        if "value" not in rec:
            continue
        out[field] = list(rec["value"] or []) if field in LIST_FIELDS else rec["value"]
    return out


def needs_review(spec_row: dict, data: dict) -> list[str]:
    """이 행에서 **아직 사람이 봐야 하는** 칸 목록. 비어 있으면 큐에 안 뜬다.

    조건: `apply` 후에도 비어 있는 `QUEUE_FIELDS` 중 `unknown` 표시가 없는 칸.
    `unknown` 이 없으면 그 행은 매번 큐에 다시 떠서 도구가 영원히 'N건 남음'을 띄운다 —
    15개의 `base_combo` NULL 중 상당수는 판매자가 정말 안 적었고 영상을 봐도 모르는 값이다
    (MEMORY `markets-that-omit-glue-and-scent`: 진통제는 향 0/5 · 풀조합 1/5).
    """
    row = apply(spec_row, data)
    market, product = row.get("market"), row.get("product")
    return [f for f in QUEUE_FIELDS
            if is_blank(f, row.get(f)) and not is_unknown(data, market, product, f)]


def orphans(data: dict, known_pairs) -> list[tuple[str, str]]:
    """`specs` 에 없는 오버레이 (마켓, 제품) 목록.

    `extract.resolve_product_name` 이 제품을 **개명·병합**할 수 있어서(실측: 10 renamed,
    5 folded) 사람이 채운 항목이 고아가 될 수 있다. 조용히 버리면 검수 노동이 흔적 없이
    사라지므로 카운트로 드러낸다 — 이 저장소가 무음 드롭을 금지하는 것과 같은 규칙이다.
    """
    known = set(known_pairs or ())
    return [(m, p) for m in sorted(data or {}) if not m.startswith("_")
            for p in sorted(data[m]) if (m, p) not in known]


# ---------------------------------------------------------------- 기록(순수)
def record(data: dict, market: str, product: str, field: str, value, *,
           was, at: str, note: str | None = None, unknown: bool = False) -> dict:
    """오버레이 한 칸 기록 → **새 dict**(입력 비변경).

    `at` 은 호출부가 주입한다 — 여기서 `now()` 를 부르면 테스트가 비결정적이 된다
    (`rawstore` 의 `scraped_at` 과 같은 규칙).
    `was` 는 덮기 전 값이다. 사람이 **틀린 값을 고친** 경우 무엇을 덮었는지가 남아야
    나중에 되짚을 수 있다 — 추출기가 향료·재료어를 제품명으로 잡은 사례가 이 레포에 이미
    실측돼 있다(유령 제품 복구).
    """
    if field not in OVERRIDABLE:
        raise ValueError(f"오버레이 대상 칸이 아니다: {field!r} (허용: {', '.join(OVERRIDABLE)})")
    out = {m: {p: dict(fields) for p, fields in products.items()}
           for m, products in (data or {}).items() if not m.startswith("_")}
    cell = {"value": None if unknown else value, "was": was, "at": at}
    if note:
        cell["note"] = note
    if unknown:
        cell["unknown"] = True
    out.setdefault(market, {}).setdefault(product, {})[field] = cell
    return out


# ---------------------------------------------------------------- 프로세스 캐시
_CACHE: dict | None = None


def cached() -> dict:
    """오버레이 1회 로드 후 프로세스 캐시. `_upsert_spec` 이 게시물마다 부르는 자리라
    캐시가 없으면 판매자 게시물 수만큼 파일을 다시 파싱한다(`_tag_exclusions` 와 같은 이유).
    """
    global _CACHE
    if _CACHE is None:
        _CACHE = load()
    return _CACHE


def reload() -> dict:
    """캐시 무효화 후 재로드. **저장 경로가 반드시 부른다** — 안 부르면 방금 사람이 채운
    값이 같은 프로세스의 다음 재수집에서 안 보인다(같은 프로세스에서 저장과 수집이 함께
    도는 게 이 도구의 정상 사용법이다)."""
    global _CACHE
    _CACHE = None
    return cached()


# ---------------------------------------------------------------- 셀프테스트
if __name__ == "__main__":
    d = load()
    n_fields = sum(len(f) for m in d.values() for f in m.values())
    print(f"오버레이 {len(d)}마켓 · {sum(len(m) for m in d.values())}제품 · {n_fields}칸")
    print("큐를 띄우는 칸:", ", ".join(QUEUE_FIELDS))
    print("편집 가능한 칸:", ", ".join(OVERRIDABLE))
