# -*- coding: utf-8 -*-
"""
스레드 배치 추출 테스트 — 계획 `.omc/plans/kind-axis-resolution.md` AC12·AC13.

왜 배치인가: 호출당 입력 토큰의 **99.4%가 고정 프롬프트**다(실측 `evals/cost_profile.py`).
댓글 1건당 1회 호출은 2,900자 지시문을 댓글 수만큼 재전송하는 것과 같다.

AC12 — 댓글 N개 스레드가 1~2회 호출로 처리되고, 결과 행이 **댓글별로 정확히 귀속**된다.
AC13 — 형제 댓글을 참조하는 댓글("웅 근데 향이 좀 에바ㅠ")의 **제품 귀속이 성공**한다.
        per-comment 경로에서는 원리적으로 불가능했던 케이스다.
        ⚠️ 계획 본문은 이 케이스를 `dc-055` 로 적었지만, 골드에서 해당 텍스트의 실제 id 는
        `dc-059` 다(`dc-055`는 두 제품을 스스로 비교하는 별개 댓글). 여기선 텍스트를 기준으로 검증한다.

호출 수·귀속은 가짜 LLM 으로 결정적으로 검증하고(키 불필요), 문맥 복원은 실호출로만 확인한다.

실행:  python -m eval.test_extract_thread   (repo 루트에서)
"""
from __future__ import annotations

import os
import re

from slime_rag import extract as X
from slime_rag.sources.base import RawReview

_POST_URL = "https://gall.dcinside.com/mgallery/board/view/?id=amos&no=777"
_S_RE = re.compile(r"^\[S(\d+)\]\s?(.*)$", re.M)


class FakeLLM:
    """
    네트워크 경계 대체 — 호출 수를 세고, [S<n>] 조각을 그대로 되돌려준다.

    `firsthand_evidence` 를 채워 내보내는 건 필수다. 스키마상 required 이고, 추출 경로가
    그 필드 없는 항목을 결정적으로 버리기 때문에(AC15 `drop_hearsay_reviews`) 비워 두면
    모든 항목이 사라져 귀속 검증 자체가 성립하지 않는다.
    """

    def __init__(self):
        self.calls: list[str] = []

    def complete(self, prompt, *, system=None, schema=None, model=None,
                 max_tokens=4096, effort=None, label=""):
        self.calls.append(prompt)
        docs = []
        for sid, body in _S_RE.findall(prompt):
            # 본문의 첫 낱말을 제품명처럼 되돌려 '귀속이 밀렸는지'를 눈에 보이게 한다.
            first = (body.split() or [""])[0]
            docs.append({"source_id": f"S{sid}", "market": None, "shipping_cs": None,
                         "reviews": [{"mentioned_product": first,
                                      "firsthand_evidence": body[:15]}],
                         "flags": {"toxic": False}})
        return {"docs": docs}


def _thread(n_comments: int) -> list[RawReview]:
    post = RawReview(text="ㅂ 카피바라 푸냥이 둘다만져본사람", url=_POST_URL, platform="dcinside",
                     raw_title="ㅂ 카피바라 푸냥이", meta={"type": "post"})
    comments = [
        RawReview(text=f"댓글{i} 내용", url=f"{_POST_URL}#cmt", platform="dcinside",
                  meta={"type": "comment", "parent_no": "777", "parent_title": "ㅂ 카피바라 푸냥이"})
        for i in range(n_comments)
    ]
    return [post] + comments


# ---------------------------------------------------------------- AC12
def test_ac12_call_count():
    """댓글 10개 스레드(조각 11개) → 1회 호출. batch_size 를 넘으면 2회."""
    llm = FakeLLM()
    X.extract_collected(_thread(10), llm)
    assert len(llm.calls) == 1, f"조각 11개는 1회 호출이어야, 실제 {len(llm.calls)}"

    llm2 = FakeLLM()
    X.extract_collected(_thread(10), llm2, batch_size=6)
    assert len(llm2.calls) == 2, f"batch_size=6 이면 2회여야, 실제 {len(llm2.calls)}"
    print("✓ AC12 호출 수: 조각 11개 → 1회 (batch_size=6 이면 2회) OK")


def test_ac12_per_comment_attribution():
    """결과 행이 조각별로 **정확히** 귀속된다 — 한 칸이라도 밀리면 실패."""
    raws = _thread(8)
    pairs = X.extract_collected(raws, FakeLLM())
    assert len(pairs) == len(raws), f"조각 {len(raws)}개인데 결과 {len(pairs)}개"
    for raw, doc in pairs:
        got = doc["reviews"][0]["mentioned_product"]
        want = raw.text.split()[0]
        assert got == want, f"귀속 어긋남: {raw.text[:12]!r} → {got!r} (기대 {want!r})"
    print("✓ AC12 조각별 귀속 정확 OK")


def test_ac12_missing_doc_is_padded():
    """모델이 조각을 빠뜨려도 리스트 길이가 유지된다 — 안 그러면 귀속이 통째로 밀린다."""
    class DropsOne(FakeLLM):
        def complete(self, prompt, **kw):
            out = super().complete(prompt, **kw)
            out["docs"] = [d for d in out["docs"] if d["source_id"] != "S2"]
            return out

    class DropsTwo(FakeLLM):
        def complete(self, prompt, **kw):
            out = super().complete(prompt, **kw)
            out["docs"] = [d for d in out["docs"] if d["source_id"] not in ("S1", "S2")]
            return out

    raws = _thread(4)
    pairs = X.extract_collected(raws, DropsOne())
    assert len(pairs) == len(raws), "빠진 조각이 패딩되지 않아 길이가 어긋남"
    missing = [doc for raw, doc in pairs if raw is raws[2]][0]
    assert missing["reviews"] == [], "빠진 조각은 빈 문서여야"

    # 패딩 문서끼리 리스트를 **공유하면 안 된다** — 공유하면 한 조각의 결과가 다른 조각으로 샌다.
    pairs2 = X.extract_collected(_thread(4), DropsTwo())
    blanks = [doc for _, doc in pairs2 if not doc["reviews"]]
    assert len(blanks) == 2, f"빈 문서 2개 기대, 실제 {len(blanks)}"
    blanks[0]["reviews"].append({"mentioned_product": "오염"})
    assert blanks[1]["reviews"] == [], "패딩 문서들이 같은 reviews 리스트를 공유함"
    for raw, doc in pairs:
        if raw is raws[2]:
            continue
        assert doc["reviews"][0]["mentioned_product"] == raw.text.split()[0], \
            "빠진 조각 뒤로 귀속이 밀림"
    print("✓ AC12 누락 조각 패딩(귀속 밀림 없음) OK")


def test_hearsay_gate_applies_to_batch_path():
    """AC15 결정적 게이트는 단건뿐 아니라 **배치 경로에도** 걸려야 한다."""
    class NoEvidence(FakeLLM):
        def complete(self, prompt, **kw):
            out = super().complete(prompt, **kw)
            for d in out["docs"]:
                for r in d["reviews"]:
                    r["firsthand_evidence"] = None      # 전언 — 근거 못 댐
            return out

    pairs = X.extract_collected(_thread(3), NoEvidence())
    assert all(doc["reviews"] == [] for _, doc in pairs), \
        "배치 경로가 전언 게이트를 통과시킴(가짜 후기 행이 적재된다)"
    print("✓ AC15 배치 경로에도 전언 게이트 적용 OK")


def test_ac12_orphan_comments_without_post():
    """글 없이 댓글만 수집된 경우에도 스레드로 묶여 1회 호출 + 귀속 유지."""
    raws = _thread(5)[1:]
    llm = FakeLLM()
    pairs = X.extract_collected(raws, llm)
    assert len(llm.calls) == 1 and len(pairs) == 5
    print("✓ AC12 글 없는 스레드(댓글만) 처리 OK")


# ---------------------------------------------------------------- AC13 (실호출)
def test_ac13_sibling_context_attribution():
    """형제 댓글 문맥으로 제품명이 생략된 댓글의 귀속이 성공한다."""
    if not os.getenv("OPENAI_API_KEY"):
        print("· AC13 통합테스트 skip (OPENAI_API_KEY 없음)")
        return
    from slime_rag.llm_ops import LLM

    title = "ㅂ 카피바라 푸냥이 둘다만져본사람"
    texts = [
        "ㅂ 카피바라 푸냥이 둘다만져본사람 뭐가 더 좋았음? 카피바라 못사서 ㅈㄴ 울고있어",
        "샤갈 푸냥이는 겟함?",
        "웅 근데 향이 좀 에바ㅠ 고구마케이크향에 좀 이상한 향 입힌느낌이야..",
    ]
    docs = X.extract_thread(title, texts, LLM())
    assert len(docs) == 3, f"조각 3개 기대, 실제 {len(docs)}"
    products = [(r.get("mentioned_product") or "") for r in (docs[2].get("reviews") or [])]
    assert products, "문맥 참조 댓글에서 항목이 하나도 안 나옴"
    assert any("푸냥이" in p for p in products), \
        f"형제 댓글 문맥으로 '푸냥이' 귀속 실패: {products}"
    print(f"✓ AC13 형제 댓글 문맥 귀속 성공: {products}")


def test_ac12_batch_equivalence():
    """
    배치 전후 추출 결과가 **동등하거나 개선**인가 (AC12).

    ⚠️ 항목 '개수'로 재면 안 된다 — 실측 결과 per-comment 경로가 **더 많이** 뽑는데,
    늘어난 분이 전부 **제목에서 지어낸 유령 항목**이었다. per-comment 는 마켓 상속을 위해
    부모 글 제목을 머리말로 붙이는데(구 `extract_collected`), 모델이 그 제목의 제품명을
    댓글 평가로 오인한다:
        "ㅂ 카피바라 푸냥이 둘다만져본사람 / 수선화 좋음 깨끗한 핸드크림 냄새"
        → per-comment: ['카피바라', '푸냥이']   (수선화 얘긴데 제목 제품이 나옴)
        → batch:       ['수선화']              (맞음)
    이 유령 행은 그대로 적재되면 **디시 긍정 카운트를 부풀린다** — AC15 전언 누수와 같은
    종류의 편향 왜곡이다. 그래서 여기서는 개수가 아니라 (a) 호출 수, (b) 유령 항목,
    (c) 본문 명시 제품의 보존을 잰다.
    """
    if not os.getenv("OPENAI_API_KEY"):
        print("· AC12 동등성 실측 skip (OPENAI_API_KEY 없음)")
        return
    from slime_rag import llm_ops
    from slime_rag.llm_ops import LLM

    title = "ㅂ 카피바라 푸냥이 둘다만져본사람"
    cases = [
        ("그래놀라딸토 개좋음 향은 호불호 많이 갈리던데 난 괜찮았음", "그래놀라딸토"),
        ("카피바라사서 만져봣었는데 그냥 딱히 크게호불호안갈릴향임", "카피바라"),
        ("수선화 좋음 깨끗한 핸드크림 냄새", "수선화"),
    ]
    texts = [t for t, _ in cases]
    title_products = ("카피바라", "푸냥이")
    llm = LLM()

    def products(doc):
        return [(r.get("mentioned_product") or "") for r in (doc.get("reviews") or [])]

    def ghosts(docs):
        """본문에 없는데 제목에서 새어 들어온 제품명 수."""
        return sum(1 for (text, _), doc in zip(cases, docs)
                   for p in products(doc)
                   if any(tp in p for tp in title_products) and not any(
                       tp in text for tp in title_products))

    n0 = len(llm_ops.LEDGER)
    per_comment = [X.extract_review(f"{title}\n{t}", llm) for t in texts]
    n_calls_per = len(llm_ops.LEDGER) - n0

    n1 = len(llm_ops.LEDGER)
    batched = X.extract_thread(title, texts, llm)
    n_calls_batch = len(llm_ops.LEDGER) - n1

    assert n_calls_batch < n_calls_per, \
        f"배치가 호출을 줄이지 못함 ({n_calls_batch} vs {n_calls_per})"
    g_batch, g_per = ghosts(batched), ghosts(per_comment)
    assert g_batch <= g_per, f"배치가 유령 항목을 더 만듦: {g_batch} > {g_per}"
    for (text, want), doc in zip(cases, batched):
        assert any(want in p for p in products(doc)), \
            f"본문 명시 제품 '{want}' 를 배치가 놓침: {products(doc)}"
    print(f"✓ AC12 동등성: 호출 {n_calls_per}→{n_calls_batch}, "
          f"제목 유래 유령 항목 {g_per}→{g_batch}, 본문 명시 제품 보존 OK")


if __name__ == "__main__":
    test_ac12_call_count()
    test_ac12_per_comment_attribution()
    test_ac12_missing_doc_is_padded()
    test_hearsay_gate_applies_to_batch_path()
    test_ac12_orphan_comments_without_post()
    test_ac13_sibling_context_attribution()
    test_ac12_batch_equivalence()
    print("\n스레드 배치 추출 테스트 통과 ✅")
