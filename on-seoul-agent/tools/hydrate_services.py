"""Hydrate Services Tool — service_id 리스트로 public_service_reservations 원본 조회.

service_embeddings(on_ai DB)는 검색 인덱스로만 쓰고, 답변 컨텍스트는
public_service_reservations(on_data DB)의 최신 원본에서 직접 조회한다.
임베딩 시점의 stale metadata(service_status·receipt_*_dt 등)를 우회하기 위함.

SQL Injection 방지:
    service_id 값은 단일 ARRAY bind 파라미터로 전달한다.
    SQL 템플릿에 service_id 값을 직접 삽입하지 않는다.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# sql_search._RESULT_COLUMNS와 동일하게 유지하여
# vector_results와 sql_results의 스키마를 일치시킨다.
_RESULT_COLUMNS = """
    service_id, service_name, max_class_name, min_class_name,
    area_name, place_name, service_status, payment_type,
    service_url, receipt_start_dt, receipt_end_dt,
    service_open_start_dt, service_open_end_dt,
    coord_x, coord_y, target_info
"""


async def hydrate_services(
    session: AsyncSession,
    service_ids: list[str],
) -> list[dict]:
    """service_id 리스트로 public_service_reservations 원본 행을 조회한다.

    입력 순서(검색 순위)를 그대로 유지하여 반환한다.
    원본에 없거나 soft-delete된 service_id는 결과에서 제외한다.

    Parameters
    ----------
    session:
        on_data_reader 계정 AsyncSession (SELECT 전용).
    service_ids:
        조회 대상 service_id 리스트. 빈 리스트면 DB 호출 없이 빈 리스트 반환.

    Returns
    -------
    list[dict]
        _RESULT_COLUMNS 컬럼을 가진 딕셔너리 리스트.
        입력 순서를 보존하며, 원본 누락분은 제외된다.
    """
    if not service_ids:
        return []

    sql = text(f"""
        SELECT {_RESULT_COLUMNS}
        FROM public_service_reservations
        WHERE service_id = ANY(:service_ids)
          AND deleted_at IS NULL
    """)

    result = await session.execute(sql, {"service_ids": service_ids})
    keys = result.keys()
    rows = [dict(zip(keys, row)) for row in result.fetchall()]

    # 입력 순서를 보존: dict 인덱싱 후 service_ids 순서대로 재정렬.
    # 원본에 없는 service_id는 자동 제외된다.
    by_id = {r["service_id"]: r for r in rows}
    return [by_id[sid] for sid in service_ids if sid in by_id]
