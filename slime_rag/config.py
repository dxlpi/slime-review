# -*- coding: utf-8 -*-
"""
환경 설정 — .env 한 곳에서 모델/경로/임계값을 읽는다.

스택 결정의 단일 출처(single source of truth). 모델·임베딩·벡터스토어를
인터페이스 뒤에 두기 위한 첫 단추로, 값은 전부 여기서만 바꾼다.
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:           # python-dotenv 미설치 시 OS 환경변수만 사용
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PROMPTS_DIR = ROOT / "prompts"


@dataclass(frozen=True)
class Settings:
    # --- LLM (OpenAI) ---
    # 대량 추출은 mini(싸고 빠름), 고난도 개체연결 판정만 플래그십으로 분리.
    model_extract: str = os.getenv("OPENAI_MODEL_EXTRACT", "gpt-5.4-mini")
    model_judge: str = os.getenv("OPENAI_MODEL_JUDGE", "gpt-5.4")

    # --- 임베딩 (BGE-M3: 한국어 친화 + dense/sparse 동시 출력) ---
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

    # --- 벡터스토어 (pgvector: 1층 스펙 ↔ 2층 후기 조인 + 메타필터) ---
    # 기본값은 docker-compose.yml 의 자격증명과 일치(로컬 pgvector).
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://slime:slime@localhost:5432/slime_rag")

    # --- 개체연결 보류 임계값 ---
    link_abstain_threshold: float = float(os.getenv("LINK_ABSTAIN_THRESHOLD", "0.6"))

    # --- 2층 스레드 배치 추출 상한 (extract.py) ---
    # 실측 근거·상세 코멘트는 extract.py 의 MAX_THREAD_SOURCES 정의부 참조.
    max_thread_sources: int = int(os.getenv("MAX_THREAD_SOURCES", "12"))

    # --- 인스타 Graph API (1층 스펙, 선택) ---
    ig_access_token: str | None = os.getenv("IG_ACCESS_TOKEN")
    ig_user_id: str | None = os.getenv("IG_USER_ID")

    # --- 인스타 해시태그 스크래퍼 (Apify, 2층 긍정편향 유저후기) ---
    # Graph API ig_hashtag_search 는 App Review(advanced access) 요구 → 스크래퍼 티어로 대체.
    # 토큰 미설정이면 ApifyHashtagSource 는 조용히 스킵(collect_all 이 빈 결과 처리).
    apify_token: str | None = os.getenv("APIFY_TOKEN")
    apify_hashtag_actor: str = os.getenv("APIFY_HASHTAG_ACTOR", "apify/instagram-hashtag-scraper")
    apify_results_per_hashtag: int = int(os.getenv("APIFY_RESULTS_PER_HASHTAG", "30"))

    kb_demo_path: Path = DATA_DIR / "slime_market_kb_demo.json"
    # 1층 공식 스펙 fixture (business_discovery 가 App Review 요구 → 라이브 대신 큐레이션 스냅샷)
    layer1_fixture_path: Path = DATA_DIR / "layer1_fixture.json"
    # 2층 해시태그 큐레이션 목록 (Apify 스크래퍼 입력)
    ig_hashtags_path: Path = DATA_DIR / "ig_hashtags.json"


settings = Settings()
