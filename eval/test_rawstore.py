# -*- coding: utf-8 -*-
"""
rawstore 오프라인 테스트 — 네트워크·LLM·DB 미접촉/무비용.

이 모듈이 지키는 건 '유료로 산 원문을 잃지 않는다' 하나다. 그래서 검증도 유실 경로에
집중한다: 덮어쓰기, 순서 뒤집힘(옛 캡처가 새 캡처를 이김), 0건 런의 증발, 워터마크 오염.

실행:  python -m eval.test_rawstore   (repo 루트에서)
"""
from __future__ import annotations
import json
import tempfile
from pathlib import Path

from slime_rag import rawstore as rs

KIND = "ig_profile_feed"
HANDLE = "from.murmurslime"


def _root() -> Path:
    return Path(tempfile.mkdtemp(prefix="rawstore-test-"))


def test_envelope_roundtrip():
    root = _root()
    items = [{"shortCode": "A", "caption": "가", "timestamp": "2026-01-01T00:00:00Z"}]
    path = rs.save_run(items, actor="apify/instagram-scraper", kind=KIND, key=HANDLE,
                       requested={"resultsLimit": 200}, usage_usd=0.123, root=root)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["actor"] == "apify/instagram-scraper"
    assert doc["key"] == HANDLE and doc["kind"] == KIND
    assert doc["requested"]["resultsLimit"] == 200
    assert doc["usage_total_usd"] == 0.123
    assert doc["n_items"] == 1 and doc["items"] == items, "items 는 가공 없이 그대로여야 한다"
    assert doc["scraped_at"] and doc["_note"]
    print("✓ 봉투 왕복(액터·요청·비용·원문 무가공) OK")


def test_append_only_never_overwrites():
    """같은 키에 여러 번 저장해도 앞선 스냅샷이 남아야 한다 — 이 모듈의 존재 이유."""
    root = _root()
    for cap in ("v1", "v2", "v3"):
        rs.save_run([{"shortCode": "A", "caption": cap}], actor="x", kind=KIND,
                    key=HANDLE, root=root)
    files = sorted(rs.run_dir(KIND, HANDLE, root=root).glob("*.json"))
    assert len(files) == 3, f"런마다 새 파일이어야 하는데 {len(files)}개"
    caps = [json.loads(p.read_text(encoding="utf-8"))["items"][0]["caption"] for p in files]
    assert set(caps) == {"v1", "v2", "v3"}, f"앞선 캡처가 사라졌다: {caps}"
    print("✓ append-only(덮어쓰기 없음) OK")


def test_latest_capture_wins_within_same_second():
    """같은 초에 저장된 런들도 **나중 것이 이겨야** 한다.

    회귀 근거(2026-08-07): 충돌 시에만 `-2` 접미사를 붙였더니 `...Z-2.json` 이
    `...Z.json` 보다 앞서 정렬돼(`-` < `.`) 최신 캡처가 옛 캡처에 밀렸다.
    캡션 수정이 조용히 무시되는 경로였고, 화면엔 아무 이상이 안 보인다.
    """
    root = _root()
    for cap in ("v1", "v2", "v3-final"):
        rs.save_run([{"shortCode": "A", "caption": cap, "timestamp": "2026-01-01T00:00:00Z"}],
                    actor="x", kind=KIND, key=HANDLE, root=root)
    items = rs.latest_items(KIND, HANDLE, root=root)
    assert len(items) == 1, f"같은 shortCode 는 하나로 접혀야 하는데 {len(items)}건"
    assert items[0]["caption"] == "v3-final", f"최신 캡처가 이기지 않았다: {items[0]['caption']}"
    names = sorted(p.name for p in rs.run_dir(KIND, HANDLE, root=root).glob("*.json"))
    assert names == sorted(names), "파일명도 시간순으로 읽혀야 한다"
    print("✓ 최신 캡처 우선(같은 초 충돌 포함) OK")


def test_merge_across_runs_keeps_union():
    root = _root()
    rs.save_run([{"shortCode": "A", "timestamp": "2026-01-01T00:00:00Z"},
                 {"shortCode": "B", "timestamp": "2026-02-01T00:00:00Z"}],
                actor="x", kind=KIND, key=HANDLE, root=root)
    rs.save_run([{"shortCode": "C", "timestamp": "2026-03-01T00:00:00Z"}],
                actor="x", kind=KIND, key=HANDLE, root=root)
    got = {i["shortCode"] for i in rs.latest_items(KIND, HANDLE, root=root)}
    assert got == {"A", "B", "C"}, f"런 합집합이어야 하는데 {got}"
    print("✓ 런 합집합 병합 OK")


def test_items_without_id_are_kept():
    """식별자 없는 아이템을 버리지 않는다 — 중복 위험보다 유실 위험이 나쁘다."""
    root = _root()
    rs.save_run([{"caption": "식별자 없음"}, {"shortCode": "A"}],
                actor="x", kind=KIND, key=HANDLE, root=root)
    assert len(rs.latest_items(KIND, HANDLE, root=root)) == 2
    print("✓ 식별자 없는 아이템 보존 OK")


def test_empty_run_is_recorded():
    """0건도 파일로 남아야 '아직 안 돌림'과 '돌렸는데 0건'이 구분된다."""
    root = _root()
    path = rs.save_run([], actor="x", kind=KIND, key="catchslime", root=root)
    assert path.exists() and json.loads(path.read_text(encoding="utf-8"))["n_items"] == 0
    assert "catchslime" in rs.iter_keys(KIND, root=root)
    print("✓ 0건 런 기록 OK")


def test_watermark_is_per_key():
    """워터마크는 **키별**이다 — 전체 최댓값이면 처음 훑는 마켓의 과거가 통째로 잘린다."""
    root = _root()
    rs.save_run([{"shortCode": "A", "timestamp": "2026-05-01T00:00:00Z"}],
                actor="x", kind=KIND, key="slime_gina_", root=root)
    rs.save_run([{"shortCode": "B", "timestamp": "2026-01-01T00:00:00Z"}],
                actor="x", kind=KIND, key="catchslime", root=root)
    assert rs.newest_timestamp(KIND, "slime_gina_", root=root) == "2026-05-01T00:00:00Z"
    assert rs.newest_timestamp(KIND, "catchslime", root=root) == "2026-01-01T00:00:00Z", \
        "남의 마켓 시각이 새어 들어왔다 — 그 마켓 과거가 통째로 잘린다"
    assert rs.newest_timestamp(KIND, "wayz.slime", root=root) is None, \
        "이력 없는 마켓은 워터마크 없음(전량 수집) 이어야 한다"
    print("✓ 워터마크 키별 격리 OK")


def test_handle_separators_survive_key_sanitization():
    """`from.murmurslime`·`bom__slime` 의 구분자를 뭉개면 조회가 조용히 빈손이 된다."""
    root = _root()
    for h in ("from.murmurslime", "bom__slime", "slime_gina_"):
        rs.save_run([{"shortCode": h}], actor="x", kind=KIND, key=h, root=root)
        assert rs.latest_items(KIND, h, root=root), f"{h} 조회 실패"
    assert set(rs.iter_keys(KIND, root=root)) == {"from.murmurslime", "bom__slime", "slime_gina_"}
    print("✓ 핸들 구분자 보존 OK")


def test_path_traversal_is_blocked():
    root = _root()
    p = rs.save_run([], actor="x", kind=KIND, key="../../escaped", root=root)
    assert root in p.parents, f"저장소 밖으로 나갔다: {p}"
    print("✓ 경로 이탈 차단 OK")


def test_corrupt_file_is_skipped_not_fatal():
    root = _root()
    rs.save_run([{"shortCode": "A"}], actor="x", kind=KIND, key=HANDLE, root=root)
    (rs.run_dir(KIND, HANDLE, root=root) / "99999999T999999Z-00.json").write_text(
        "{깨진 json", encoding="utf-8")
    items = rs.latest_items(KIND, HANDLE, root=root)
    assert [i["shortCode"] for i in items] == ["A"], "정상 파일까지 잃으면 안 된다"
    print("✓ 깨진 파일 건너뛰기(치명적 아님) OK")


def test_manifest_counts():
    root = _root()
    rs.save_run([{"shortCode": "A", "timestamp": "2026-01-01T00:00:00Z"},
                 {"shortCode": "B", "timestamp": "2026-03-01T00:00:00Z"}],
                actor="x", kind=KIND, key=HANDLE, usage_usd=0.5, root=root)
    rs.save_run([{"shortCode": "C", "timestamp": "2026-02-01T00:00:00Z"}],
                actor="x", kind=KIND, key="catchslime", usage_usd=0.25, root=root)
    man = rs.manifest(root=root)["kinds"][KIND]
    assert man["n_keys"] == 2 and man["n_posts"] == 3
    assert man["usd"] == 0.75, f"누적 비용이 틀림: {man['usd']}"
    murmur = man["keys"][HANDLE]
    assert murmur["oldest_post"] == "2026-01-01T00:00:00Z"
    assert murmur["newest_post"] == "2026-03-01T00:00:00Z"
    assert murmur["n_runs"] == 1 and murmur["last_scraped_at"]
    print("✓ manifest 집계(키·게시물·날짜범위·비용) OK")


def test_manifest_on_missing_store():
    """저장소가 아직 없어도 예외가 아니라 빈 결과여야 한다."""
    assert rs.manifest(root=_root() / "nope")["kinds"] == {}
    print("✓ 저장소 부재 회복력 OK")


if __name__ == "__main__":
    test_envelope_roundtrip()
    test_append_only_never_overwrites()
    test_latest_capture_wins_within_same_second()
    test_merge_across_runs_keeps_union()
    test_items_without_id_are_kept()
    test_empty_run_is_recorded()
    test_watermark_is_per_key()
    test_handle_separators_survive_key_sanitization()
    test_path_traversal_is_blocked()
    test_corrupt_file_is_skipped_not_fatal()
    test_manifest_counts()
    test_manifest_on_missing_store()
    print("\n모든 오프라인 테스트 통과 ✅")
