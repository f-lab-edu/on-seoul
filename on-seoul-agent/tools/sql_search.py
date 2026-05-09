"""SQL Search Tool — public_service_reservations 파라미터화 조회.

SQL Injection 방지: 모든 필터 값은 bind 파라미터로만 전달한다.
LLM이 생성하거나 사용자로부터 입력받은 값을 SQL 문자열에 직접 삽입하지 않는다.

사용 방법:
    from tools.sql_search import sql_search

    rows = await sql_search(
        session,
        max_class_name="체육시설",
        area_name="마포구",
        service_status="접수중",
    )
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TOP_K: int = 10
_LIKE_ESCAPE_TABLE = str.maketrans({
    "\\": "\\\\",
    "%":  "\\%",
    "_":  "\\_",
})

def _escape_like(value: str) -> str:
    """ILIKE 패턴에서 와일드카드 문자를 이스케이프한다.

    PostgreSQL ILIKE의 특수 문자(%·_·\\)를 리터럴로 취급하도록
    백슬래시로 이스케이프한다. SQL 쪽에는 ESCAPE '\\' 절을 함께 사용한다.
    """
    return value.translate(_LIKE_ESCAPE_TABLE)

_RESULT_COLUMNS = """
    service_id, service_name, max_class_name, min_class_name,
    area_name, place_name, service_status, payment_type,
    service_url, receipt_start_dt, receipt_end_dt,
    service_open_start_dt, service_open_end_dt,
    coord_x, coord_y, target_info
"""


async def sql_search(
    session: AsyncSession,
    *,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    keyword: str | None = None,
    top_k: int = TOP_K,
) -> list[dict]:
    """public_service_reservations를 파라미터화 SQL로 조회한다.

    조건 조합이 없으면 deleted_at IS NULL 기준 최신 순으로 최대 top_k 건 반환한다.

    Parameters
    ----------
    session:
        on_data_reader 계정 AsyncSession (SELECT 전용).
    max_class_name:
        대분류 카테고리 필터 (체육시설·문화행사·시설대관·교육·진료). None이면 미적용.
    area_name:
        서울 자치구 이름 필터 (예: 강남구). None이면 미적용.
    service_status:
        예약 상태 필터 (접수중·접수예정·마감·대기). None이면 미적용.
    keyword:
        service_name 또는 place_name에 대한 ILIKE 검색 키워드. None이면 미적용.
    top_k:
        반환할 최대 결과 수. 기본값: 10.

    Returns
    -------
    list[dict]
        _RESULT_COLUMNS에 정의된 컬럼을 가진 딕셔너리 리스트.
        결과 없으면 빈 리스트.
    """
    # WHERE 조건 목록 — 정적 문자열만 추가한다 (사용자 값은 절대 삽입하지 않음)
    conditions: list[str] = ["deleted_at IS NULL"]
    bind: dict = {"top_k": top_k}

    if max_class_name is not None:
        conditions.append("max_class_name = :max_class_name")
        bind["max_class_name"] = max_class_name

    if area_name is not None:
        conditions.append("area_name = :area_name")
        bind["area_name"] = area_name

    if service_status is not None:
        conditions.append("service_status = :service_status")
        bind["service_status"] = service_status

    if keyword is not None:
        # 인덱스 식과 일치하는 연결 표현식으로 ILIKE 적용.
        # idx_psr_trgm_name_combined:
        #   gin((COALESCE(service_name,'') || ' ' || COALESCE(place_name,'')) gin_trgm_ops)
        # OR 절(두 컬럼 개별 ILIKE)을 사용하면 BitmapOr 결합 비용 추정 실패로
        # 인덱스를 무시하므로, 단일 연결 표현식을 사용한다.
        conditions.append(
            "(COALESCE(service_name, '') || ' ' || COALESCE(place_name, '')) ILIKE :keyword ESCAPE '\\'"
        )
        bind["keyword"] = f"%{_escape_like(keyword)}%"

    where = " AND ".join(conditions)
    sql = text(f"""
        SELECT {_RESULT_COLUMNS}
        FROM public_service_reservations
        WHERE {where}
        ORDER BY receipt_start_dt DESC NULLS LAST
        LIMIT :top_k
    """)

    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
