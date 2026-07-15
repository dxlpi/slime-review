# -*- coding: utf-8 -*-
"""
DB 헬퍼 (Phase 4) — pgvector(Postgres) 연결 한 곳.

psycopg3 커넥션 + pgvector 타입 등록. 색인/검색이 공유한다.
스키마는 sql/schema.sql (compose 가 초기화 시 적용). 앱에서도 apply_schema() 로 적용 가능.
"""

from __future__ import annotations

from .config import settings, ROOT

_SCHEMA = ROOT / "sql" / "schema.sql"


def connect():
    """pgvector 타입이 등록된 커넥션을 연다(호출자가 close 책임). psycopg 는 지연 import."""
    import psycopg
    from pgvector.psycopg import register_vector
    conn = psycopg.connect(settings.database_url)
    register_vector(conn)
    return conn


def apply_schema(conn=None) -> None:
    """sql/schema.sql 을 멱등 적용(로컬·배포 공용)."""
    import psycopg
    own = conn is None
    conn = conn or psycopg.connect(settings.database_url)
    try:
        conn.execute(_SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    # 연결·스키마·pgvector 동작 스모크 테스트(모델/키 불필요).
    apply_schema()
    with connect() as c:
        ext = c.execute("SELECT extname FROM pg_extension WHERE extname='vector'").fetchone()
        tabs = c.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name").fetchall()
        print("pgvector 확장:", ext[0] if ext else "없음")
        print("테이블:", [t[0] for t in tabs])
