"""Analytics Search Tool — public_service_reservations 파라미터화 집계 조회.

집계/분포 질의(ANALYTICS intent)를 위한 GROUP BY COUNT / DISTINCT 도구.
sql_search 의 "원본 상세행 LIMIT" 템플릿과 달리 차원별 개수·종류를 산출한다.

SQL Injection 방지:
- GROUP BY 차원(컬럼명)은 _DIMENSION_COLUMNS 화이트리스트 dict 값만 f-string 삽입한다.
  group_by 키가 화이트리스트 외면 KeyError 로 즉시 방어하여 DB 도달 전 차단한다.
  (사용자/LLM 문자열을 컬럼 위치에 직접 삽입하지 않는다.)
- 필터 값과 top_k 는 전부 bind 파라미터로만 전달한다.

on_data_reader 계정 AsyncSession(SELECT 전용) 사용 전제 — sql_search 와 동일 계정.

사용 방법:
    from tools.analytics_search import analytics_search

    rows = await analytics_search(
        session,
        group_by="area_name",
        metric="count",
        keyword="테니스장",
    )
"""

from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools.sql_search import _escape_like

TOP_K: int = 25

# GROUP BY 차원 화이트리스트 (key → 실제 컬럼명).
# 이 dict 의 값(value)만 SQL 문자열에 f-string 으로 삽입된다 — 인젝션 불가.
_DIMENSION_COLUMNS: dict[str, str] = {
    "area_name": "area_name",
    "max_class_name": "max_class_name",
    "min_class_name": "min_class_name",
    "service_status": "service_status",
}


async def analytics_search(
    session: AsyncSession,
    *,
    group_by: str,
    metric: Literal["count", "distinct"],
    max_class_name: list[str] | None = None,
    area_name: list[str] | None = None,
    service_status: str | None = None,
    payment_type: str | None = None,
    keyword: str | None = None,
    top_k: int = TOP_K,
) -> list[dict]:
    """public_service_reservations 를 단일 차원으로 집계한다.

    Parameters
    ----------
    session:
        on_data_reader 계정 AsyncSession (SELECT 전용).
    group_by:
        집계 차원. _DIMENSION_COLUMNS 의 키만 허용 (화이트리스트 외면 KeyError).
    metric:
        "count" → GROUP BY + COUNT(*), "distinct" → SELECT DISTINCT.
    max_class_name:
        대분류 카테고리 리스트 필터. max_class_name = ANY(:classes) 로 다중 OR 매칭.
        None/빈 리스트면 미적용.
    area_name:
        서울 자치구 이름 리스트 필터. area_name = ANY(:areas) 로 다중 OR 매칭.
        None/빈 리스트면 미적용.
    service_status:
        예약 상태 필터. None이면 미적용.
    payment_type:
        결제 유형 필터. "무료"이면 payment_type = '무료' 정확 매칭.
        "유료"이면 payment_type LIKE '유료%' 접두 매칭(원천 데이터의
        "유료"·"유료(요금안내문의)"를 모두 포괄). sql_search 와 동일 규칙. None이면 미적용.
    keyword:
        service_name 또는 place_name 에 대한 ILIKE 검색 키워드. None이면 미적용.
    top_k:
        반환할 최대 결과 수. 기본값: 25.

    Returns
    -------
    list[dict]
        count: [{"group_value": ..., "count": ...}, ...] (count DESC 정렬).
        distinct: [{"group_value": ...}, ...] (차원 컬럼 정렬, count 없음).
        결과 없으면 빈 리스트.
    """
    # 화이트리스트 강제: 키가 없으면 KeyError 로 즉시 방어 (DB 도달 전 차단).
    column = _DIMENSION_COLUMNS[group_by]

    # WHERE 조건 목록 — 정적 문자열만 추가한다 (사용자 값은 절대 삽입하지 않음).
    conditions: list[str] = ["deleted_at IS NULL"]
    bind: dict = {"top_k": top_k}

    # 다중값 필터 — = ANY(:param) 로 OR 매칭(sql_search 동일 패턴). 스칼라 str 오주입
    # 방어로 리스트 래핑(char-split 방지). 빈 리스트/None 이면 조건 생략.
    if isinstance(max_class_name, str):
        max_class_name = [max_class_name]
    if max_class_name:
        conditions.append("max_class_name = ANY(:classes)")
        bind["classes"] = list(max_class_name)

    if isinstance(area_name, str):
        area_name = [area_name]
    if area_name:
        conditions.append("area_name = ANY(:areas)")
        bind["areas"] = list(area_name)

    if service_status is not None:
        conditions.append("service_status = :service_status")
        bind["service_status"] = service_status

    # payment_type: 무료=정확 매칭, 유료=접두("유료%") 매칭 (sql_search 동일 규칙).
    # 접두는 "유료"·"유료(요금안내문의)" 변형을 모두 포괄한다. bind only(인젝션 방지).
    if payment_type == "무료":
        conditions.append("payment_type = :payment_type")
        bind["payment_type"] = "무료"
    elif payment_type == "유료":
        conditions.append("payment_type LIKE :payment_type ESCAPE '\\'")
        bind["payment_type"] = "유료%"

    if keyword is not None:
        # sql_search 와 동일한 연결 표현식·이스케이프 재사용 (idx_psr_trgm_name_combined 일관성).
        conditions.append(
            "(COALESCE(service_name, '') || ' ' || COALESCE(place_name, '')) ILIKE :keyword ESCAPE '\\'"
        )
        bind["keyword"] = f"%{_escape_like(keyword)}%"

    # 차원 컬럼 NULL 행은 집계에서 제외.
    conditions.append(f"{column} IS NOT NULL")
    where = " AND ".join(conditions)

    if metric == "distinct":
        sql = text(f"""
            SELECT DISTINCT {column} AS group_value
            FROM public_service_reservations
            WHERE {where}
            ORDER BY {column}
            LIMIT :top_k
        """)
    else:
        sql = text(f"""
            SELECT {column} AS group_value, COUNT(*) AS count
            FROM public_service_reservations
            WHERE {where}
            GROUP BY {column}
            ORDER BY count DESC, {column}
            LIMIT :top_k
        """)

    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
