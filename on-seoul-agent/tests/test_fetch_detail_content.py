"""fetch_detail_content tool 단위 테스트.

focal service_id 단건 detail_content SELECT(파라미터 바인딩·AsyncSession).
미존재→None. raw 블롭 격리 회귀(_result_columns/카드에 미포함).
"""

from unittest.mock import AsyncMock, MagicMock

from tools.fetch_detail_content import fetch_detail_content


async def test_returns_detail_content_for_existing_id():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = "3. 상세내용\n폭염 운영 안내."
    session.execute.return_value = result

    out = await fetch_detail_content(session, "A1")
    assert out == "3. 상세내용\n폭염 운영 안내."


async def test_binds_service_id_as_parameter():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = "본문"
    session.execute.return_value = result

    await fetch_detail_content(session, "A1' OR '1'='1")

    # service_id 는 bind 파라미터로 전달되어야 한다(SQL 문자열 직접 삽입 금지).
    _, bind = session.execute.call_args[0]
    assert bind == {"service_id": "A1' OR '1'='1"}


async def test_returns_none_when_not_found():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    out = await fetch_detail_content(session, "missing")
    assert out is None


async def test_empty_service_id_returns_none_without_db_call():
    session = AsyncMock()
    out = await fetch_detail_content(session, "")
    assert out is None
    session.execute.assert_not_called()


def test_detail_content_not_in_hydration_columns():
    """블롭 격리 회귀: 일반 hydration 컬럼 셋에 detail_content 가 없다."""
    from tools._result_columns import PUBLIC_SERVICE_RESERVATIONS_COLUMNS

    assert "detail_content" not in PUBLIC_SERVICE_RESERVATIONS_COLUMNS
