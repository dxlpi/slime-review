# -*- coding: utf-8 -*-
"""
관련성 필터 (Phase 1) — RawReview 가 '실제 제품 후기'인지 분류.

아모스 갤은 후기/질문/양도/잡담이 섞임 → 비후기를 드롭해 추출 비용을 아낀다.
1차는 휴리스틱(키워드·길이), 애매하면 LLM 판정으로 승격.

[TODO] 사용자 제공 기준 필요(CLAUDE.md §11-C): 후기 vs 질문 vs 양도 vs 잡담 규칙 + 예시.
"""

from __future__ import annotations
from dataclasses import dataclass

from .sources import RawReview


@dataclass
class RelevanceVerdict:
    is_review: bool
    kind: str          # 'review' | 'question' | 'resale' | 'chitchat'
    confidence: float
    reason: str


def classify(review: RawReview) -> RelevanceVerdict:
    raise NotImplementedError("Phase 1: 관련성 필터 규칙/예시 확정 후 구현")
