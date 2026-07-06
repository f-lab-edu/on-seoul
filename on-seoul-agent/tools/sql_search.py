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

from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools._result_columns import PUBLIC_SERVICE_RESERVATIONS_COLUMNS
from tools.target_audience import build_audience_sql

TOP_K: int = 10
_LIKE_ESCAPE_TABLE = str.maketrans(
    {
        "\\": "\\\\",
        "%": "\\%",
        "_": "\\_",
    }
)


def _escape_like(value: str) -> str:
    """ILIKE 패턴에서 와일드카드 문자를 이스케이프한다.

    PostgreSQL ILIKE의 특수 문자(%·_·\\)를 리터럴로 취급하도록
    백슬래시로 이스케이프한다. SQL 쪽에는 ESCAPE '\\' 절을 함께 사용한다.
    """
    return value.translate(_LIKE_ESCAPE_TABLE)


_RESULT_COLUMNS = PUBLIC_SERVICE_RESERVATIONS_COLUMNS


async def sql_search(
    session: AsyncSession,
    *,
    max_class_name: str | None = None,
    area_name: list[str] | None = None,
    service_status: str | None = None,
    payment_type: str | None = None,
    target_audience: str | None = None,
    keyword: str | None = None,
    receipt_date_from: date | None = None,
    receipt_date_to: date | None = None,
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
        서울 자치구 이름 리스트 필터 (예: ["강남구"] 또는 ["성동구","광진구"]).
        area_name = ANY(:areas) 로 다중 지역 OR 매칭. None/빈 리스트면 미적용.
    service_status:
        예약 상태 필터 (접수중·예약마감·접수종료·예약일시중지·안내중). None이면 미적용.
    target_audience:
        대상 그룹 필터 (CHILD/ADULT/SENIOR/FAMILY). 토큰맵 기반 OR-LIKE 술어로
        target_info 를 부분문자열 매칭한다(제한없음/가족은 항상 통과). None이면 미적용.
    payment_type:
        결제 유형 필터. "무료"이면 payment_type = '무료' 정확 매칭.
        "유료"이면 payment_type LIKE '유료%' 접두 매칭(원천 데이터의
        "유료"·"유료(요금안내문의)"를 모두 포괄). None이면 미적용.
        DB distinct 확인(2026-06-03): {"유료","무료","유료(요금안내문의)"}.
    keyword:
        service_name 또는 place_name에 대한 ILIKE 검색 키워드. None이면 미적용.
    receipt_date_from:
        접수 기간 시작 필터. receipt_end_dt >= 이 날짜인 서비스만 포함 (구간 겹침). None이면 미적용.
    receipt_date_to:
        접수 기간 종료 필터. receipt_start_dt <= 이 날짜인 서비스만 포함 (구간 겹침). None이면 미적용.
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

    # area_name 다중값: area_name = ANY(:areas) 로 여러 자치구를 OR 매칭한다.
    # 사용자 값은 리스트째 bind 파라미터로만 전달(인젝션 방지). 빈 리스트는 미적용.
    # 방어: 스칼라 str 이 새어들어와도 chars 로 쪼개지지 않게 감싼다
    # (_shared.py:115 와 동일 패턴, 계약상 리스트지만 상류 오주입 belt-and-suspenders).
    if isinstance(area_name, str):
        area_name = [area_name]
    if area_name:
        conditions.append("area_name = ANY(:areas)")
        bind["areas"] = list(area_name)

    if service_status is not None:
        conditions.append("service_status = :service_status")
        bind["service_status"] = service_status

    # target_audience: 토큰맵에서 파생한 OR-LIKE 술어(제한없음/가족 항상 통과).
    # 토큰은 서버측 고정맵이지만 전부 bind 파라미터로 전달한다(값 삽입 금지).
    audience_sql, audience_bind = build_audience_sql(target_audience)
    if audience_sql is not None:
        conditions.append(audience_sql)
        bind.update(audience_bind)

    # payment_type: 무료=정확 매칭, 유료=접두("유료%") 매칭.
    # 접두는 "유료"·"유료(요금안내문의)" 변형을 모두 포괄한다. bind only(인젝션 방지).
    if payment_type == "무료":
        conditions.append("payment_type = :payment_type")
        bind["payment_type"] = "무료"
    elif payment_type == "유료":
        conditions.append("payment_type LIKE :payment_type ESCAPE '\\'")
        bind["payment_type"] = "유료%"

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

    # 접수 기간 구간 겹침 필터:
    #   [receipt_date_from, receipt_date_to] ∩ [receipt_start_dt, receipt_end_dt] ≠ ∅
    #   ↔ receipt_end_dt >= date_from AND receipt_start_dt <= date_to
    if receipt_date_from is not None:
        conditions.append("receipt_end_dt >= :receipt_date_from")
        bind["receipt_date_from"] = receipt_date_from

    if receipt_date_to is not None:
        conditions.append("receipt_start_dt <= :receipt_date_to")
        bind["receipt_date_to"] = receipt_date_to

    where = " AND ".join(conditions)
    sql = text(f"""
        SELECT {_RESULT_COLUMNS}
        FROM public_service_reservations
        WHERE {where}
        ORDER BY receipt_start_dt DESC NULLS LAST, service_id ASC
        LIMIT :top_k
    """)

    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
