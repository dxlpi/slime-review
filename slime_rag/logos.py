# -*- coding: utf-8 -*-
"""
마켓 로고 수집 — IG 프로필 아바타를 받아 `data/market_logos/` 에 저장하고 KB 에 기록한다.

**1회성 수동 실행**이다. 파이프라인에 배선되어 있지 않고, 자동 갱신도 없다 — 로고는 거의
안 바뀌는 데다, 자동화하면 감시 없는 반복 재호스팅이 된다(ADR-0010).

정책 근거: [ADR-0010](../docs/adr/0010-market-logo-assets.md). ADR-0009 §1 의 "다운로드·
재호스팅 배제"는 후기 본문·게시물 미디어에 적용되고, 마켓 본인 프로필 아바타는 예외다.
그 예외의 경계(마켓당 1개·320px·본인 계정만·링크백 필수)를 코드로 지키는 게 이 파일이다.

왜 다운로드해야만 하나: 인스타는 만료되지 않는 아바타 URL 을 주지 않는다. `profilePicUrlHD`
는 서명 파라미터(`_nc_ohc`,`oe`)를 달고 수일 내 죽고, 살아 있어도 CDN 이 referrer 를 본다.
게시물의 `/p/{shortcode}/embed` 같은 안정 주소가 프로필엔 없다 → URL 을 저장하면 반드시 깨진다.

실행:
  python -m slime_rag.logos --dry-run      # 네트워크 0. 대상 핸들 + 예상비용만
  python -m slime_rag.logos                # 실제 Apify run(과금) + 다운로드
  python -m slime_rag.logos --refresh      # 이미 받은 파일도 다시 받기
  python -m slime_rag.logos --handles from.murmurslime,vinzzang_slime
"""

from __future__ import annotations

import argparse
import json
import logging

import requests

from .config import settings, ROOT
from .source_links import LOGO_DIR
from .sources import InstagramProfileSource

log = logging.getLogger("logos")

LOGO_PATH = ROOT / LOGO_DIR
# 상한은 크기 제한이라기보다 '아바타가 아닌 것'(오류 HTML, 리다이렉트된 큰 이미지)이
# 저장소에 커밋되는 걸 막는 새니티 체크다.
MAX_BYTES = 512 * 1024
_TIMEOUT = 15

# ADR-0010 이 정한 해상도 경계. **로컬에서 강제한다** — URL 의 `stp=dst-jpg_s320x320` 은
# 요청일 뿐이고, 2026-08-06 실측 결과 인스타 CDN 은 원본이 더 클 때 이 힌트를 무시하고
# 네이티브 크기를 준다(13개 중 9개가 518~1080px 로 도착). 경계를 URL 에 맡기면 판매자의
# 원본 해상도 브랜드 자산이 그대로 커밋된다 — ADR-0010 의 '저해상' 완화 논거가 무너지는 지점.
MAX_PX = 320
_JPEG_QUALITY = 85


def _download(url: str, dest) -> int | None:
    """URL → dest 저장. 바이트 수 반환, 실패면 None(그 핸들만 스킵하고 나머지는 계속).

    한 마켓의 실패가 나머지 12개를 막지 않게 예외를 여기서 삼킨다 — 대신 조용히 넘어가지
    않고 호출부가 세어서 요약에 드러낸다.
    """
    try:
        r = requests.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("다운로드 실패 %s: %s", dest.name, e)
        return None
    ctype = (r.headers.get("Content-Type") or "").lower()
    if not ctype.startswith("image/"):           # 만료 URL 은 이미지가 아니라 에러 문서를 준다
        log.warning("이미지가 아님 %s: Content-Type=%r", dest.name, ctype)
        return None
    if len(r.content) > MAX_BYTES:
        log.warning("상한 초과 %s: %d bytes", dest.name, len(r.content))
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    if normalize(dest) is None:                  # 이미지로 안 열리면 저장물을 남기지 않는다
        dest.unlink(missing_ok=True)
        return None
    return dest.stat().st_size


def normalize(path) -> tuple[int, int] | None:
    """저장된 로고를 `MAX_PX` 이하 정사각 JPEG 로 맞춘다. (w,h) 반환, 못 열면 None.

    다운로드와 분리된 함수인 이유: 경계를 어긴 파일이 **이미 저장소에 있을 때** 재수집(과금)
    없이 고칠 수 있어야 한다(`--normalize`). 멱등이라 이미 규격에 맞으면 다시 쓰지 않는다.
    """
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(path) as im:
            im.load()
            if max(im.size) <= MAX_PX:
                return im.size                   # 이미 규격 — 재인코딩으로 화질을 깎지 않는다
            im = im.convert("RGB")
            im.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
            size = im.size
            im.save(path, "JPEG", quality=_JPEG_QUALITY, optimize=True)
            return size
    except (UnidentifiedImageError, OSError) as e:
        log.warning("이미지로 열 수 없음 %s: %s", getattr(path, "name", path), e)
        return None


def _write_kb(logos: dict[str, str]) -> int:
    """KB 의 `markets[].logo` 갱신. 갱신된 마켓 수 반환.

    KB 는 사람이 읽고 손으로도 고치는 파일이라 포맷을 보존한다 — 키 순서 그대로,
    `ensure_ascii=False`(한글이 `\\uXXXX` 로 깨지지 않게), 들여쓰기 2, 끝 개행.
    """
    path = settings.kb_demo_path
    data = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    for m in data["markets"]:
        rel = logos.get(m.get("handle"))
        if rel and m.get("logo") != rel:
            m["logo"] = rel
            n += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return n


def run(handles: list[str] | None = None, *, refresh: bool = False,
        dry_run: bool = False) -> dict:
    """로고 수집 1회. 반환 `{"targets","fetched","saved","skipped","failed","kb_updated"}`."""
    kb = json.loads(settings.kb_demo_path.read_text(encoding="utf-8"))
    known = {m["handle"]: m for m in kb["markets"] if m.get("handle")}
    targets = [h for h in (handles or known)]
    unknown = [h for h in targets if h not in known]
    if unknown:                                  # KB 밖 핸들은 받지 않는다(ADR-0010 '본인 계정만')
        raise SystemExit(f"KB 에 없는 핸들: {', '.join(unknown)}")

    # 멱등: 이미 받은 파일은 건너뛴다. 재호스팅을 반복하지 않는 게 기본값이어야 한다.
    todo = [h for h in targets if refresh or not (LOGO_PATH / f"{h}.jpg").exists()]
    skipped = len(targets) - len(todo)

    cost = len(todo) / 1000 * InstagramProfileSource.COST_PER_1000
    print(f"대상 {len(targets)}핸들 · 수집 {len(todo)} · 스킵(이미 있음) {skipped} "
          f"· 예상비용 ${cost:.4f}")
    if dry_run:
        for h in todo:
            print(f"  - {h}")
        return {"targets": len(targets), "fetched": 0, "saved": 0,
                "skipped": skipped, "failed": 0, "kb_updated": 0}
    if not todo:
        return {"targets": len(targets), "fetched": 0, "saved": 0,
                "skipped": skipped, "failed": 0, "kb_updated": 0}

    profiles = InstagramProfileSource(settings.apify_token).fetch_profiles(todo)
    saved = {}
    for p in profiles:
        handle = p["username"]
        if handle not in known:                  # 액터가 리다이렉트된 계정을 돌려줄 수 있다
            log.warning("KB 밖 핸들 응답 무시: %s", handle)
            continue
        dest = LOGO_PATH / f"{handle}.jpg"
        n = _download(p["profile_pic_url"], dest)
        if n is None:
            continue
        saved[handle] = f"{LOGO_DIR}/{handle}.jpg"
        print(f"  ✓ {handle}  {n // 1024}KB")
    # 실패는 요청 대비 결손으로 한 번에 센다 — 다운로드 실패든 응답 자체가 없었든(비공개·
    # 삭제·액터 누락) 사용자에게는 같은 결과다. 무음 갭 금지.
    failed = len([h for h in todo if h not in saved])
    updated = _write_kb(saved) if saved else 0
    print(f"저장 {len(saved)} · 실패 {failed} · KB 갱신 {updated}"
          + ("  (실패분은 UI 에서 모노그램으로 표시됩니다)" if failed else ""))
    return {"targets": len(targets), "fetched": len(profiles), "saved": len(saved),
            "skipped": skipped, "failed": failed, "kb_updated": updated}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="마켓 IG 프로필 아바타 수집(ADR-0010)")
    ap.add_argument("--dry-run", action="store_true", help="네트워크 없이 대상·비용만 출력")
    ap.add_argument("--refresh", action="store_true", help="이미 받은 파일도 다시 받기")
    ap.add_argument("--handles", help="쉼표 구분 핸들(부분 갱신). 생략 시 KB 전체")
    ap.add_argument("--normalize", action="store_true",
                    help="이미 받은 파일만 규격(320px) 재적용 — 네트워크·과금 0")
    a = ap.parse_args()
    handles = [h.strip() for h in a.handles.split(",") if h.strip()] if a.handles else None
    if a.normalize:
        n = 0
        for p in sorted(LOGO_PATH.glob("*.jpg")):
            before = p.stat().st_size
            size = normalize(p)
            if size and p.stat().st_size != before:
                print(f"  ✓ {p.name}  → {size[0]}x{size[1]}  {before // 1024}KB→{p.stat().st_size // 1024}KB")
                n += 1
        print(f"규격 재적용 {n}건 (≤{MAX_PX}px)")
        return
    run(handles, refresh=a.refresh, dry_run=a.dry_run)


if __name__ == "__main__":
    main()
