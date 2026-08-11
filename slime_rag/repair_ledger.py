# -*- coding: utf-8 -*-
"""복구 원장 — **확신도 칸이 담지 못하는 되돌리기 열쇠**를 파일로 남긴다.

`market_confidence` 는 이 저장소에서 confidence 이자 provenance 다. 하지만 그건
**표식이지 서열 키가 아니고**, 무엇보다 **옛 값을 담지 못한다**. 그래서 다음 세 종류의
쓰기는 확신도만으로는 영영 되돌릴 수 없다:

  · **NULL 되돌림** — 무엇을 지웠는지가 칸에서 사라진다.
  · **개명** — 이름은 확신도 칸에 안 들어간다.
  · **같은 확신도 재사용** — 실측: 잘못 채워진 4행이 `BACKFILL_CONF_SPEC`(0.90)에
    정상 채움 150행과 **같은 값**으로 앉아 있다. 값으로는 선별 롤백이 불가능하다.

⚠️ **`.omc/` 아래에 두지 말 것.** `.gitignore:18` 이 `.omc/` 를 통째로 무시한다 — 거기
  적은 원장은 전부 **세션 수명**이고, 그러면 '되돌릴 수 있다'는 주장이 실제로는 거짓이다.
  (기실행된 `pipeline.backfill_non_product_labels` 의 롤백 주장도 이미 그 상태였다.)
  그래서 이 모듈의 경로는 커밋되는 `data/repair_ledgers/` 하나뿐이다.

⚠️ **원문 바이트 금지**(ADR-0013). 담는 건 id · 이전값 · 이름 · 시각 · `source_fn` 뿐이다.
  `data/product_registry.json` 이 캡션을 빼고도 커밋되는 것과 같은 근거이고,
  `market_inversion_review()` 반환값을 파일로 커밋하지 말라는 금지와 같은 자리다.

⚠️ **함수마다 파일이 갈린다.** `fold` 원장이 하나로 합쳐지면 `evals/diff_audit.py` 의
  `resolved = baseline − after − folded` 에서 어느 함수가 지운 건지 못 가른다. 그래서
  파일명이 함수명이고, 각 항목이 `source_fn` 을 한 번 더 들고 있다(파일을 옮겨도 산다).

무LLM·무DB·무네트워크. 실행: `python -m slime_rag.repair_ledger`
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

log = logging.getLogger(__name__)

LEDGER_DIR = ROOT / "data" / "repair_ledgers"

# 원장 항목에 허용되는 칸. **화이트리스트**인 이유는 `_source_material` 의 `ATTR_FIELDS` 와
# 같다 — '남는 키 전부 통과'로 넓히는 순간 원문 바이트가 조용히 섞인다.
ALLOWED_FIELDS = frozenset({
    "id", "post_id", "market", "product", "was", "to", "was_market", "to_market",
    "was_product", "to_product", "was_conf", "to_conf", "why", "tier", "axis", "source",
})

# 원장에 들어가면 안 되는 칸 — 이름만으로 원문임이 드러나는 것들.
FORBIDDEN_FIELDS = frozenset({"body", "text", "evidence", "caption", "title", "attributes",
                              "html", "snippet"})


def path_for(source_fn: str) -> Path:
    return LEDGER_DIR / f"{source_fn}.json"


def _clean(entry: dict, source_fn: str) -> dict:
    bad = FORBIDDEN_FIELDS & set(entry)
    if bad:
        raise ValueError(f"원장에 원문 칸이 들어왔다({source_fn}): {sorted(bad)}")
    unknown = set(entry) - ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"원장 허용 칸 밖이다({source_fn}): {sorted(unknown)} "
                         f"— 필요하면 ALLOWED_FIELDS 에 **의도적으로** 추가할 것")
    return {**entry, "source_fn": source_fn}


def write(source_fn: str, entries, *, note: str | None = None) -> Path:
    """`entries` 를 `data/repair_ledgers/<source_fn>.json` 에 **원자적으로** 쓴다.

    같은 함수를 다시 돌리면 **덮어쓴다** — 원장은 '마지막 실행을 되돌리는 열쇠'이지
    이력이 아니다. 이력이 필요하면 git 이 갖는다(그게 `.omc/` 가 아니라 여기인 이유다).
    """
    payload = {
        "_note": note or (f"{source_fn} 의 되돌리기 원장. 원문 바이트 없음(ADR-0013). "
                          "확신도 칸이 담지 못하는 값(옛 마켓·옛 이름·삭제)만 담는다."),
        "source_fn": source_fn,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(entries),
        "entries": [_clean(dict(e), source_fn) for e in entries],
    }
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    dest = path_for(source_fn)
    # 원자적 저장 — `spec_overrides.save` 와 같은 이유(중간에 죽으면 되돌리기가 사라진다).
    fd, tmp = tempfile.mkstemp(dir=str(LEDGER_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    log.info("원장 기록: %s (%d건)", dest, len(entries))
    return dest


def read(source_fn: str) -> dict:
    """원장 하나를 읽는다. 없으면 빈 원장(예외 아님 — 되돌릴 게 없는 것과 같은 뜻)."""
    p = path_for(source_fn)
    if not p.exists():
        return {"source_fn": source_fn, "count": 0, "entries": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("원장 읽기 실패 — 빈 원장으로 진행: %s (%s)", p, e)
        return {"source_fn": source_fn, "count": 0, "entries": []}


def manifest() -> dict:
    """무과금 관측 — 어떤 원장이 있고 몇 건인가."""
    if not LEDGER_DIR.exists():
        return {}
    return {p.stem: read(p.stem).get("count", 0) for p in sorted(LEDGER_DIR.glob("*.json"))}


if __name__ == "__main__":
    print("원장 디렉터리:", LEDGER_DIR)
    print(manifest())
