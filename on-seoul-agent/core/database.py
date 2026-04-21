"""비동기 SQLAlchemy 세션 팩토리 및 FastAPI DI 함수.

DB 계정 구성
- on_ai_app     : on_ai DB CRUD. service_embeddings, chat_agent_traces 관리.
- on_data_reader: on_data DB SELECT 전용. public_service_reservations 조회.
  권한 제한은 DB 계정 레벨에서 처리한다 (on_data_database_url 계정이 SELECT 전용).
"""

from collections.abc import AsyncGenerator

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
)
_OnAiSession = async_sessionmaker(_on_ai_engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# on_data DB — 정형 데이터 읽기 전용 (public_service_reservations 등)
# ---------------------------------------------------------------------------

_on_data_engine = create_async_engine(settings.on_data_database_url, echo=settings.debug)
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
