"""비동기 SQLAlchemy 세션 팩토리 및 FastAPI DI 함수.

DB 계정 구성
- on_ai_app     : on_ai DB CRUD. service_embeddings, chat_agent_traces 관리.
- on_data_reader: on_data DB SELECT 전용. public_service_reservations 조회.
  권한 제한은 DB 계정 레벨에서 처리한다 (on_data_database_url 계정이 SELECT 전용).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from core.config import settings

# ---------------------------------------------------------------------------
# on_ai DB — AI 서비스 전용 (service_embeddings, chat_agent_traces)
# ---------------------------------------------------------------------------

# statement_cache_size=0: pgvector 타입(vector)을 asyncpg prepared statement에서 사용할 때
# 라이브러리 로드 타이밍 문제가 발생할 수 있어 캐시를 비활성화한다.
_on_ai_engine = create_async_engine(
    settings.on_ai_database_url,
    echo=settings.debug,
    connect_args={"statement_cache_size": 0},
    # SSE 클라이언트 연결 해제 시 uvicorn이 task를 cancel하면 asyncpg의
    # graceful terminate가 CancelledError로 실패한다. pool_pre_ping은 다음
    # 요청에서 해당 연결을 재사용하기 전에 살아있는지 확인해 stale 연결을 방지한다.
    pool_pre_ping=True,
    pool_recycle=300,  # 5분 이상 유휴 연결 재생성 (네트워크 레벨 타임아웃 방지)
    # 풀 사이즈 명시 (§4-3): 노드 로컬 세션(0b)으로 커넥션 점유 W가 검색 윈도우로
    # 축소되므로 컨테이너당 100 QPS에서 평균 동시 ~8, 피크 ~24. cap 25(10+15).
    pool_size=10,
    max_overflow=15,
)
_OnAiSession = async_sessionmaker(_on_ai_engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# on_data DB — 정형 데이터 읽기 전용 (public_service_reservations 등)
# ---------------------------------------------------------------------------

_on_data_engine = create_async_engine(
    settings.on_data_database_url,
    echo=settings.debug,
    # statement_cache_size=0: PgBouncer transaction mode(제안 3)와 asyncpg prepared
    # statement 충돌 방지. PgBouncer 도입 전이라도 선반영(§0-6 (3), §4-4).
    connect_args={"statement_cache_size": 0},
    pool_pre_ping=True,
    pool_recycle=300,
    # 풀 사이즈 명시 (§4-3): on_data 는 sql+hydration 두 쿼리 윈도우만 점유. 평균 동시
    # ~3, 피크 ~10. cap 15(5+10).
    pool_size=5,
    max_overflow=10,
)
_OnDataSession = async_sessionmaker(_on_data_engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# ORM Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# FastAPI Depends DI 함수
# ---------------------------------------------------------------------------


async def get_ai_db() -> AsyncGenerator[AsyncSession, None]:
    """on_ai DB 세션 — CRUD. routers / agents에서 Depends로 주입."""
    async with _OnAiSession() as session:
        yield session


async def get_data_db() -> AsyncGenerator[AsyncSession, None]:
    """on_data DB 세션 — SELECT 전용. tools/sql_search, tools/map_search에서 Depends로 주입."""
    async with _OnDataSession() as session:
        yield session


# ---------------------------------------------------------------------------
# 워크플로우 직접 호출용 context manager (FastAPI DI 외부에서 사용)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def ai_session_ctx() -> AsyncGenerator[AsyncSession, None]:
    """on_ai DB 세션 context manager — 워크플로우 / 배치 스크립트용."""
    async with _OnAiSession() as session:
        yield session


@asynccontextmanager
async def data_session_ctx() -> AsyncGenerator[AsyncSession, None]:
    """on_data DB 세션 context manager — 워크플로우 / 배치 스크립트용."""
    async with _OnDataSession() as session:
        yield session
