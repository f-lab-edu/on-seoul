"""Map Search Tool — earthdistance/cube 기반 반경 검색.

PostgreSQL earthdistance + cube 확장을 사용하여
public_service_reservations의 coord_x(경도)/coord_y(위도)를 기준으로
사용자 위치 반경 내 시설을 거리 오름차순으로 조회한다.

전제 조건:
  - PostgreSQL DB에 `cube` 및 `earthdistance` 확장이 활성화되어 있어야 한다.
  - coord_x = 경도(longitude), coord_y = 위도(latitude).
  - coord_x/coord_y는 NUMERIC 또는 VARCHAR 타입으로 저장될 수 있으므로 CAST 처리.

사용 방법:
    from tools.map_search import map_search

    geojson = await map_search(
        session,
        lat=37.5665,
        lng=126.9780,
        radius_m=1000,
    )
    # geojson["type"] == "FeatureCollection"
    # geojson["features"] 는 거리 오름차순 Feature 리스트
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_RADIUS_M: int = 1000
# map_search는 지도 표시용으로 더 많은 결과를 반환한다 (sql_search.TOP_K=10 대비 확장).
TOP_K: int = 20

# 정적 컬럼 목록 — 사용자 입력 유래 값 없음. text() f-string 삽입 안전.
_RESULT_COLUMNS = (
    "service_id, service_name, max_class_name, area_name, place_name, "
    "service_status, service_url, receipt_start_dt, receipt_end_dt, "
    "coord_x, coord_y, target_info"
)


async def map_search(
    session: AsyncSession,
    lat: float,
    lng: float,
    *,
    radius_m: int = DEFAULT_RADIUS_M,
    top_k: int = TOP_K,
) -> dict:
    """반경 내 공공서비스 시설을 GeoJSON FeatureCollection 형태로 반환한다.

    Parameters
    ----------
    session:
        on_data_reader 계정 AsyncSession (SELECT 전용).
    lat:
        검색 기준점 위도 (latitude). 한국 범위: 33.0 ~ 43.0.
    lng:
        검색 기준점 경도 (longitude). 한국 범위: 124.0 ~ 132.0.
    radius_m:
        검색 반경 (미터). 기본값: 1000.
    top_k:
        반환할 최대 결과 수. 기본값: 20.

    Returns
    -------
    dict
        GeoJSON FeatureCollection.
        각 Feature의 properties에는 시설 정보와 distance_m(정수, 미터)이 포함된다.
        시설이 없으면 features=[] 인 FeatureCollection을 반환한다.
    """
    # CTE로 distance_m을 한 번만 계산하여 SELECT와 WHERE에서 중복 없이 재사용한다.
    # coord_x/coord_y가 NULL인 행은 IS NOT NULL로 사전 제거하여 CAST 오류를 방지한다.
    # (빈 문자열 필터는 NUMERIC 컬럼에서 타입 오류를 유발하므로 제외; IS NOT NULL이 충분)
    #
    # `<@ earth_box(...)` 조건: idx_psr_active_ll_to_earth GiST 인덱스가 반경 조건을
    # CTE 내부에서 직접 활용할 수 있도록 추가한다 (earth_distance 계산 전 row 수 감소).
    #
    # _RESULT_COLUMNS 는 정적 상수 — 사용자 입력 유래 값 없으므로 f-string 삽입 안전.
    sql = text(f"""
        WITH candidates AS (
            SELECT
                {_RESULT_COLUMNS},
                CAST(
                    earth_distance(
                        ll_to_earth(:lat, :lng),
                        ll_to_earth(
                            CAST(coord_y AS float),
                            CAST(coord_x AS float)
                        )
                    ) AS int
                ) AS distance_m
            FROM public_service_reservations
            WHERE deleted_at IS NULL
              AND coord_x IS NOT NULL
              AND coord_y IS NOT NULL
              AND ll_to_earth(CAST(coord_y AS float), CAST(coord_x AS float))
                  <@ earth_box(ll_to_earth(:lat, :lng), :radius_m)
        )
        SELECT *
        FROM candidates
        WHERE distance_m <= :radius_m
        ORDER BY distance_m ASC
        LIMIT :top_k
    """)  # noqa: S608

    bind = {"lat": lat, "lng": lng, "radius_m": radius_m, "top_k": top_k}
    result = await session.execute(sql, bind)
    keys = list(result.keys())
    rows = [dict(zip(keys, row)) for row in result.fetchall()]

    return _to_geojson(rows)


def _to_geojson(rows: list[dict]) -> dict:
    """DB 조회 행 리스트를 GeoJSON FeatureCollection으로 변환한다."""
    features = []
    for row in rows:
        coord_x = row.get("coord_x")
        coord_y = row.get("coord_y")

        try:
            # coord_x = 경도(longitude), coord_y = 위도(latitude)
            # GeoJSON 표준: coordinates = [longitude, latitude]
            lng_val = float(coord_x)
            lat_val = float(coord_y)
            geometry: dict | None = {
                "type": "Point",
                "coordinates": [lng_val, lat_val],
            }
        except (TypeError, ValueError):
            geometry = None

        # distance_m은 properties에 포함하고 좌표 컬럼은 geometry로 이동했으므로 제외
        properties = {k: v for k, v in row.items() if k not in ("coord_x", "coord_y")}

        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": properties,
        })

    return {
        "type": "FeatureCollection",
        "features": features,
    }
