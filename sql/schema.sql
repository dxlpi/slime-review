-- 슬라임 리뷰 RAG — pgvector 스키마 (Phase 4)
-- 설계: 1층 스펙(specs) ↔ 2층 후기(reviews)를 spec_id 로 조인.
--       검색은 메타필터(마켓/종류/속성) + dense 벡터(BGE-M3) + BM25(앱단).
--       원문 미재배포: 본문 전체 대신 evidence 스니펫만 저장하고, 임베딩(벡터)만 보관.

CREATE EXTENSION IF NOT EXISTS vector;

-- 1층 공식 스펙(객관, 그라운드 트루스). 1층 미시드라 현재 비어있을 수 있음.
CREATE TABLE IF NOT EXISTS specs (
  id          BIGSERIAL PRIMARY KEY,
  market      TEXT NOT NULL,          -- 정규 market_word (예: 빈짱)
  product     TEXT NOT NULL,          -- 공식 제품명
  scent       TEXT,                   -- 향료
  base_combo  TEXT,                   -- 풀조합
  slime_type  TEXT,                   -- 종류 (TYPE_ENUM)
  -- 판매자 본인이 캡션에 쓴 질감 서술의 요약(1~2문장). slime_type 이 '무슨 종류인가'라면
  -- 이건 '만지면 어떤가'다. 구매자가 실제로 읽는 쪽이라 별도 컬럼으로 둔다.
  -- ⚠️ 후기(2층)가 아니다 — 요약 프롬프트에 절대 유입 금지(스펙↔후기 분리, ADR-0011).
  official_texture TEXT,
  beads       TEXT[] NOT NULL DEFAULT '{}',  -- 비즈/토핑 구성요소(오픈 어휘, 없으면 {})
  source_permalink TEXT,                 -- 공식 스펙 출처 인스타 게시물 URL(없으면 NULL)
  UNIQUE (market, product)
);

-- 2층 후기(주관). 추출의 제품 항목 1개 = 1행 = 1청크.
CREATE TABLE IF NOT EXISTS reviews (
  id                 BIGSERIAL PRIMARY KEY,
  source             TEXT NOT NULL,          -- 'amos' | 'instagram' (편향 집계 키)
  post_id            TEXT,
  market             TEXT,                   -- linking 정규화 결과, 보류면 NULL
  market_confidence  REAL,
  product            TEXT,                   -- mentioned_product 표면형
  spec_id            BIGINT REFERENCES specs(id),  -- 1층 조인(연결 시)
  -- 메타필터용 비정규화 컬럼(추출 attributes 에서 승격)
  slime_type         TEXT,                   -- texture.type_mentioned
  scent_sentiment    TEXT,
  texture_sentiment  TEXT,
  sound_sentiment    TEXT,
  overall_sentiment  TEXT,
  review_class       TEXT NOT NULL DEFAULT 'genuine',  -- 'genuine' | 'promo' (홍보성 분리 집계)
  attributes         JSONB NOT NULL,         -- 제품 항목 원본 전체
  evidence           TEXT,                   -- 근거 스니펫 모음(인용용)
  tokens             TEXT[],                 -- kiwipiepy 형태소 토큰(BM25)
  embedding          vector(1024),           -- BGE-M3 dense (1024차원)
  relevance_meta     JSONB,                  -- 관련성 게이트 판정(axis/M/Q/E/tau/rank 등, 미적용이면 NULL)
  source_ref         JSONB,                  -- 원문 조각 식별자({platform,url,thread_no,comment_no,shortcode}), 미보유면 NULL
  created_at         TIMESTAMPTZ DEFAULT now()
);

-- dense 근사최근접(코사인). 후기 규모 커지면 HNSW 가 IVFFlat 보다 관리 쉬움.
CREATE INDEX IF NOT EXISTS reviews_embedding_idx
  ON reviews USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS reviews_market_idx ON reviews (market);
CREATE INDEX IF NOT EXISTS reviews_type_idx   ON reviews (slime_type);
CREATE INDEX IF NOT EXISTS reviews_source_idx ON reviews (source);

-- 기존 배포 DB 멱등 마이그레이션: review_class 컬럼이 없으면 추가(기본 'genuine' → 기존 행 무해).
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS review_class TEXT NOT NULL DEFAULT 'genuine';
CREATE INDEX IF NOT EXISTS reviews_class_idx ON reviews (review_class);

-- 기존 배포 DB 멱등 마이그레이션: specs.beads 컬럼이 없으면 추가(기본 {} → 기존 행 무해).
ALTER TABLE specs ADD COLUMN IF NOT EXISTS beads TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE specs ADD COLUMN IF NOT EXISTS source_permalink TEXT;
-- 기존 배포 DB 멱등 마이그레이션: specs.official_texture 컬럼이 없으면 추가(NULL 기본 → 기존 행 무해).
ALTER TABLE specs ADD COLUMN IF NOT EXISTS official_texture TEXT;

-- 기존 배포 DB 멱등 마이그레이션: reviews.relevance_meta 컬럼이 없으면 추가(NULL 기본 → 기존 행 무해).
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS relevance_meta JSONB;

-- 기존 배포 DB 멱등 마이그레이션: reviews.source_ref 컬럼이 없으면 추가(NULL 기본 → 기존 행 무해).
-- ⚠️ 백필 없음(ADR-0009): 기존 행은 NULL 로 남고 index_gold 는 존재 post_id 를 스킵하므로
-- setup(reset=False) 로는 영원히 안 채워진다. 데모 DB 는 setup(reset=True) 로 재적재한다.
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS source_ref JSONB;
