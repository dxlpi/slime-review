# -*- coding: utf-8 -*-
"""
수집 원문 스냅샷 저장소 — **가공하기 전에** 액터 응답을 그대로 디스크에 남긴다.

[결정 2026-08-07] 그전까지 Apify 응답은 어디에도 저장되지 않았다. `RawReview` 로 매핑 →
LLM → DB 가 한 패스라, **추출 규칙이 틀렸다는 걸 나중에 알면 Apify 를 다시 사야 했다**
(유령 제품 복구 때 실제로 그랬다). 이 모듈은 그 사이에 디스크 한 겹을 넣는다:
수집(유료·1회) 과 처리(무료·N회)를 가르는 경계다.

설계 성질 세 가지 — 되돌리지 말 것:
① **덮어쓰지 않는다.** 파일명이 수집 시각이라 한 키(마켓)에 런마다 새 파일이 쌓인다.
   나중 런이 `resultsLimit` 을 줄여서 돌거나 계정이 비공개로 바뀌어 0건이 와도, 먼저 받은
   스냅샷은 그대로 남는다. 이 append-only 성질이 '잘못 처리했을 때 되돌린다'의 전부다.
② **가공하지 않는다.** `items` 는 액터가 준 dict 그대로다. 필드를 골라 담으면 그 순간
   '무엇을 버렸는지'가 코드에만 남고, 나중에 필요해진 필드는 재과금으로만 얻는다.
③ **봉투(envelope)에 출처를 적는다.** `actor`/`requested`/`scraped_at`/`usage_usd` 가
   없으면 6개월 뒤 이 파일이 무슨 요청의 답인지 알 수 없다. 손으로 만들던 기존 스냅샷
   (`data/apify_*_raw.json`)이 이미 `_note`/`actor`/`scraped_at`/`items[]` 관례를 쓰고 있어
   같은 모양을 코드로 굳힌 것이다 — 그 파일들도 `load_runs` 로 읽힌다.

⚠️ 저장물은 **git 에 안 올라간다**(`.gitignore: data/raw/`, ADR-0013). 저장·처리는 허용이고
   금지되는 건 재배포다. 브라우저로 나가는 건 서버 발췌 + 링크뿐이라는 규칙은 그대로다.

네트워크·LLM·DB 를 모르는 순수 디스크 I/O 모듈이다. 그래야 오프라인 테스트가 무비용이다.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .config import settings

log = logging.getLogger("rawstore")

DEFAULT_NOTE = ("Apify 원문 스냅샷 — 가공 전 그대로. 재처리는 이 파일에서 하고 "
                "액터를 다시 사지 않는다(slime_rag/rawstore.py).")

# 게시물 동일성 판정 키 — 앞에 있는 것부터. `shortCode` 가 인스타의 게시물 고유 id 다.
_ID_KEYS = ("shortCode", "shortcode", "id", "url")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    """파일명용 UTC 타임스탬프. 콜론을 쓰지 않는다(파일명에 못 쓰는 OS 가 있다)."""
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_key(key: str) -> str:
    """키(핸들 등) → 디렉터리명. 경로 이탈 문자를 막되 인스타 핸들의 `.`/`_` 는 살린다.

    `from.murmurslime`·`bom__slime` 처럼 핸들에 구분자가 들어가는데, 그걸 뭉개면 디렉터리와
    핸들이 1:1 이 아니게 되어 `latest_items(key=handle)` 이 조용히 빈손으로 돌아온다.
    """
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", (key or "").strip())
    cleaned = cleaned.strip(".")          # `..` 로 상위 디렉터리 타고 올라가는 것 차단
    return cleaned or "_unkeyed"


def run_dir(kind: str, key: str, *, root: Optional[Path] = None) -> Path:
    return (root or settings.raw_dir) / _safe_key(kind) / _safe_key(key)


# ---------------------------------------------------------------- 쓰기
def save_run(items: list[dict], *, actor: str, kind: str, key: str,
             requested: Optional[dict] = None, usage_usd: Optional[float] = None,
             note: Optional[str] = None, root: Optional[Path] = None) -> Path:
    """액터 응답 1런을 봉투에 싸서 새 파일로 저장하고 경로를 돌려준다.

    **0건도 저장한다.** '요청했는데 아무것도 안 왔다'는 것 자체가 기록할 사실이다 —
    파일이 없으면 '아직 안 돌렸다'와 '돌렸는데 비어 있다'가 구분되지 않고, 그 둘은
    다음 판단(재시도냐 포기냐)이 정반대다.
    """
    d = run_dir(kind, key, root=root)
    d.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    # 파일명은 **항상 같은 모양**(`<stamp>-<nn>.json`)이다. 충돌 때만 접미사를 붙이면
    # `...Z-2.json` 이 `...Z.json` 보다 **앞서** 정렬된다(`-` < `.`) — 같은 초에 두 번 저장한
    # 순간 최신 캡처가 옛 캡처에 밀린다. 순서는 아래 `scraped_at` 정렬이 정본이지만,
    # 파일명도 시간순으로 읽히는 편이 사람이 디렉터리를 볼 때 정직하다.
    path = d / f"{_stamp(now)}-00.json"
    for n in range(1, 100):
        if not path.exists():
            break
        path = d / f"{_stamp(now)}-{n:02d}.json"
    envelope = {
        "_note": note or DEFAULT_NOTE,
        "actor": actor,
        "kind": kind,
        "key": key,
        "requested": requested or {},
        "scraped_at": now.isoformat(),
        "usage_total_usd": usage_usd,
        "n_items": len(items),
        "items": items,
    }
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("원문 저장: %s (%d건%s)", path, len(items),
             f", ${usage_usd:.4f}" if isinstance(usage_usd, (int, float)) else "")
    return path


# ---------------------------------------------------------------- 읽기
def iter_keys(kind: str, *, root: Optional[Path] = None) -> list[str]:
    """그 종류로 저장된 키(마켓 핸들 등) 목록. 저장소가 없으면 []."""
    base = (root or settings.raw_dir) / _safe_key(kind)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def load_runs(kind: str, key: Optional[str] = None, *,
              root: Optional[Path] = None) -> Iterator[dict]:
    """저장된 런 봉투를 **오래된 것부터** 순회. `key=None` 이면 그 종류 전체.

    순서의 정본은 파일명이 아니라 봉투의 **`scraped_at`**(마이크로초까지 있다)이다.
    `latest_items` 가 '나중 캡처가 이긴다'로 병합하므로 이 순서가 틀리면 수정된 캡션이
    옛 캡션에 조용히 덮인다 — 화면에 아무 이상이 안 보이는 종류의 오류다.
    파일을 복사·개명해도 순서가 유지되는 편이 안전하다.

    깨진 파일은 건너뛰되 **경고를 남긴다** — 조용히 빼면 재처리 결과가 원인 없이 줄어든다.
    """
    keys = [key] if key else iter_keys(kind, root=root)
    for k in keys:
        d = run_dir(kind, k, root=root)
        if not d.is_dir():
            continue
        docs = []
        for path in sorted(d.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                log.warning("원문 파일 읽기 실패 — 건너뜀: %s (%s)", path, e)
                continue
            doc.setdefault("key", k)
            doc["_path"] = str(path)
            docs.append(doc)
        # `scraped_at` 이 없는 손작성 스냅샷은 빈 문자열로 떨어져 맨 앞(가장 오래된 것)에 선다.
        docs.sort(key=lambda x: (str(x.get("scraped_at") or ""), x["_path"]))
        yield from docs


def _item_id(item: dict) -> Optional[str]:
    for k in _ID_KEYS:
        v = item.get(k)
        if v:
            return str(v)
    return None


def latest_items(kind: str, key: Optional[str] = None, *,
                 root: Optional[Path] = None) -> list[dict]:
    """한 키의 모든 런을 합쳐 게시물 단위로 **최신 캡처를 남긴** 아이템 목록.

    런이 시간순이라 나중 것이 앞 것을 덮는다 — 같은 게시물이라도 캡션이 수정됐거나
    좋아요 수가 달라졌으면 최근에 본 쪽이 맞다. 식별자를 못 찾은 아이템은 **버리지 않고**
    그대로 싣는다(중복 위험 < 유실 위험 — 이 저장소는 유실을 막으려고 존재한다).
    """
    merged: dict[str, dict] = {}
    orphans: list[dict] = []
    for doc in load_runs(kind, key, root=root):
        for item in doc.get("items") or []:
            iid = _item_id(item)
            if iid is None:
                orphans.append(item)
            else:
                merged[iid] = item
    return list(merged.values()) + orphans


def newest_timestamp(kind: str, key: str, *, root: Optional[Path] = None) -> Optional[str]:
    """그 키에서 이미 확보한 **게시물 작성 시각**의 최댓값(ISO 문자열). 없으면 None.

    다음 런의 `onlyPostsNewerThan` 워터마크다. 디시 워터마크와 같은 이유로 **키별**이다 —
    전체 최댓값을 쓰면 이번에 처음 훑는 마켓이 남의 마켓이 올려놓은 시각부터 시작해
    과거 게시물이 통째로 잘리고, 카운트엔 '새 글 없음'과 구분 안 되는 0 만 남는다.
    """
    best: Optional[str] = None
    for item in latest_items(kind, key, root=root):
        ts = item.get("timestamp")
        if isinstance(ts, str) and (best is None or ts > best):
            best = ts                     # ISO-8601 은 사전순 = 시간순
    return best


def manifest(kind: Optional[str] = None, *, root: Optional[Path] = None) -> dict:
    """저장소 현황 — **무과금**. 키별 런 수·고유 게시물 수·게시물 날짜 범위·마지막 수집·누적 비용.

    관측성 게이트(하드게이트 #3)의 수집 쪽 창구다. 여기서 `n_posts` 가 요청량과 같으면
    피드가 잘렸을 수 있다는 뜻이라, '전부 걷었다'를 눈으로 확인 없이 주장하지 않게 된다.
    """
    base = root or settings.raw_dir
    if kind:
        kinds = [kind]
    elif base.is_dir():
        kinds = sorted(p.name for p in base.iterdir() if p.is_dir())
    else:
        kinds = []
    out: dict[str, Any] = {"root": str(base), "kinds": {}}
    for kd in kinds:
        per_key: dict[str, Any] = {}
        for k in iter_keys(kd, root=root):
            runs = list(load_runs(kd, k, root=root))
            items = latest_items(kd, k, root=root)
            stamps = sorted(t for t in (i.get("timestamp") for i in items)
                            if isinstance(t, str))
            usd = [r.get("usage_total_usd") for r in runs]
            per_key[k] = {
                "n_runs": len(runs),
                "n_posts": len(items),
                "oldest_post": stamps[0] if stamps else None,
                "newest_post": stamps[-1] if stamps else None,
                "last_scraped_at": runs[-1].get("scraped_at") if runs else None,
                "usd": round(sum(u for u in usd if isinstance(u, (int, float))), 4),
            }
        out["kinds"][kd] = {
            "n_keys": len(per_key),
            "n_posts": sum(v["n_posts"] for v in per_key.values()),
            "usd": round(sum(v["usd"] for v in per_key.values()), 4),
            "keys": per_key,
        }
    return out


# ---------------------------------------------------------------- 셀프테스트
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(manifest(), ensure_ascii=False, indent=2))
