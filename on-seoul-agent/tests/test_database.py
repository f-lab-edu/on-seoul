"""core/database.py 단위 테스트.

get_ai_db / get_data_db DI 제너레이터의 동작을 검증한다.
실제 DB 없이 mock으로만 실행되는 단위 테스트와,
실제 DB 연결이 필요한 통합 테스트(external_api 마커)로 구분된다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# get_ai_db DI
# ---------------------------------------------------------------------------


class TestGetAiDb:
    async def test_yields_async_session(self):
        """get_ai_db는 AsyncSession을 yield한다."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("core.database._OnAiSession", return_value=mock_cm):
            from core.database import get_ai_db

            gen = get_ai_db()
            session = await gen.__anext__()

            assert session is mock_session

    async def test_session_is_closed_after_use(self):
        """컨텍스트 매니저가 정상 종료되면 __aexit__가 호출된다."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("core.database._OnAiSession", return_value=mock_cm):
            from core.database import get_ai_db

            gen = get_ai_db()
            await gen.__anext__()
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()

            mock_cm.__aexit__.assert_called_once()


# ---------------------------------------------------------------------------
# get_data_db DI
# ---------------------------------------------------------------------------


class TestGetDataDb:
    async def test_yields_async_session(self):
        """get_data_db는 AsyncSession을 yield한다."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("core.database._OnDataSession", return_value=mock_cm):
            from core.database import get_data_db

            gen = get_data_db()
            session = await gen.__anext__()

            assert session is mock_session

    async def test_uses_separate_engine_from_ai_db(self):
        """on_ai와 on_data는 별도 엔진을 사용한다."""
        from core.database import _on_ai_engine, _on_data_engine

        assert _on_ai_engine is not _on_data_engine


# ---------------------------------------------------------------------------
# on_data_reader SELECT-only 권한 검증 (실제 DB 필요)
# ---------------------------------------------------------------------------


@pytest.fixture
async def fresh_data_session():
    """NullPool 엔진으로 매 테스트마다 fresh 커넥션을 생성하는 픽스처.

    pytest-asyncio는 테스트마다 새 이벤트 루프를 생성한다.
    모듈 레벨 엔진의 커넥션 풀은 이전 루프의 커넥션을 재사용하므로
    'Future attached to a different loop' 오류가 발생한다.
    NullPool은 커넥션을 풀링하지 않아 매 요청마다 새 커넥션을 만들어 이 문제를 회피한다.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from core.config import settings

    engine = create_async_engine(settings.on_data_database_url, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        yield session

    await engine.dispose()


@pytest.mark.external_api
class TestOnDataReaderPermissions:
    """on_data_reader 계정이 SELECT 전용임을 실제 DB로 검증한다.

    실행 조건: ON_DATA_DATABASE_URL에 on_data_reader 계정 URL이 설정되어 있어야 한다.
    CI에서는 -m 'not external_api' 필터로 건너뛴다.
    """

    async def test_select_succeeds(self, fresh_data_session):
        """on_data_reader 계정으로 SELECT 조회가 성공한다."""
        from sqlalchemy import text

        result = await fresh_data_session.execute(text("SELECT 1 AS health"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == 1

    async def test_insert_is_denied(self, fresh_data_session):
        """on_data_reader 계정으로 INSERT 시도 시 권한 오류가 발생한다."""
        from sqlalchemy import text
        from sqlalchemy.exc import ProgrammingError

        with pytest.raises(ProgrammingError, match="permission denied"):
            await fresh_data_session.execute(
                text(
                    "INSERT INTO public_service_reservations "
                    "(service_id, service_name, service_status, last_synced_at) "
                    "VALUES ('_test_', '_test_', 'CLOSED', NOW())"
                )
            )
            await fresh_data_session.commit()

    async def test_update_is_denied(self, fresh_data_session):
        """on_data_reader 계정으로 UPDATE 시도 시 권한 오류가 발생한다."""
        from sqlalchemy import text
        from sqlalchemy.exc import ProgrammingError

        with pytest.raises(ProgrammingError, match="permission denied"):
            await fresh_data_session.execute(
                text(
                    "UPDATE public_service_reservations "
                    "SET service_status = 'CLOSED' WHERE 1=0"
                )
            )
            await fresh_data_session.commit()
