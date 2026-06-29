"""Fetch Detail Content Tool — focal service_id 단건 detail_content 조회.

운영-상세(operational_detail) 답변 경로 전용. 사용자가 지목한 focal 시설 1건의
원시 detail_content(TEXT, 최대 641KB)를 SELECT 한다.

설계 결정사항:
  - **단건 한정**: focal service_id 1개만 조회한다(멀티건 detail 은 비채택).
    raw 블롭이 전 카드/answer 에 실리지 않도록 일반 hydration(tools/hydrate_services)
    과 분리한다. _result_columns(PUBLIC_SERVICE_RESERVATIONS_COLUMNS)에는 절대
    추가하지 않는다(블롭 격리).
  - 노출 전 정제·발췌는 상류(agents/detail_excerpt)가 담당한다 — 이 도구는 raw
    문자열을 그대로 반환하는 결정적 데이터 접근만 책임진다.

SQL Injection 방지:
    service_id 는 단일 bind 파라미터(:service_id)로 전달한다. SQL 템플릿에 값을
    직접 삽입하지 않는다.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def fetch_detail_content(
    session: AsyncSession,
    service_id: str,
) -> str | None:
    """focal service_id 단건의 detail_content 원문을 조회한다.

    Parameters
    ----------
    session:
        on_data_reader 계정 AsyncSession (SELECT 전용).
    service_id:
        조회 대상 focal service_id. 빈 문자열이면 DB 호출 없이 None 반환.

    Returns
    -------
    str | None
        detail_content 원문. 미존재/soft-delete/빈 값이면 None.
    """
    if not service_id:
        return None

    sql = text("""
        SELECT detail_content
        FROM public_service_reservations
        WHERE service_id = :service_id
          AND deleted_at IS NULL
    """)

    result = await session.execute(sql, {"service_id": service_id})
    value = result.scalar_one_or_none()
    if not value:
        return None
    return value
