"""tools/hydrate_services.py 단위 테스트.

Mock AsyncSession으로 입력 검증, bind 파라미터, 반환 순서를 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest  # noqa: F401

from tools.hydrate_services import hydrate_services


def _make_session(rows: list[dict]) -> MagicMock:
    """fake AsyncSession — execute 호출 시 rows를 반환한다."""
    mock_result = MagicMock()
    if rows:
        mock_result.keys.return_value = list(rows[0].keys())
        mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    else:
        mock_result.keys.return_value = []
        mock_result.fetchall.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    return session


class TestHydrateEmptyInput:
    async def test_empty_service_ids_returns_empty_list(self):
        """service_id 리스트가 비어 있으면 DB 호출 없이 빈 리스트 반환."""
        session = _make_session([])
        result = await hydrate_services(session, [])
        assert result == []
        session.execute.assert_not_called()
