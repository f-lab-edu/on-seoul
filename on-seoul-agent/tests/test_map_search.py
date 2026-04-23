"""tools/map_search.py 단위 테스트.

Mock DB 세션으로 SQL 실행 경로·bind 파라미터·GeoJSON 변환을 검증한다.
실제 DB 및 earthdistance 확장에 접근하지 않는다.
"""

from unittest.mock import AsyncMock, MagicMock

from tools.map_search import DEFAULT_RADIUS_M, TOP_K, _to_geojson, map_search


def _make_session(rows: list[dict]) -> MagicMock:
    """fake AsyncSession. execute 호출 시 rows를 반환한다."""
    mock_result = MagicMock()
    if rows:
        mock_result.keys.return_value = list(rows[0].keys())
        mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    else:
        mock_result.keys.return_value = [
            "service_id", "service_name", "area_name", "distance_m",
        ]
        mock_result.fetchall.return_value = []
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


_SAMPLE_ROWS = [
    {
        "service_id": "M001",
        "service_name": "마포 체육관",
        "area_name": "마포구",
        "coord_x": "126.9010",
        "coord_y": "37.5580",
        "distance_m": 320,
    }
]

_CENTER_LAT = 37.5665
_CENTER_LNG = 126.9780


class TestMapSearchReturnType:
    async def test_returns_geojson_feature_collection(self):
        """결과가 GeoJSON FeatureCollection 형태로 반환된다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await map_search(session, _CENTER_LAT, _CENTER_LNG)
        assert result["type"] == "FeatureCollection"
        assert "features" in result

    async def test_empty_result_returns_empty_feature_collection(self):
        """결과가 없으면 features=[] 인 FeatureCollection을 반환한다."""
        session = _make_session([])
        result = await map_search(session, _CENTER_LAT, _CENTER_LNG)
        assert result["type"] == "FeatureCollection"
        assert result["features"] == []


class TestMapSearchBindParams:
    async def test_lat_lng_in_bind(self):
        """lat, lng가 bind 파라미터에 전달된다."""
        session = _make_session([])
        await map_search(session, _CENTER_LAT, _CENTER_LNG)
        bind = session.execute.call_args[0][1]
        assert bind["lat"] == _CENTER_LAT
        assert bind["lng"] == _CENTER_LNG

    async def test_default_radius_m_in_bind(self):
        """기본 radius_m이 bind 파라미터에 포함된다."""
        session = _make_session([])
        await map_search(session, _CENTER_LAT, _CENTER_LNG)
        bind = session.execute.call_args[0][1]
        assert bind["radius_m"] == DEFAULT_RADIUS_M

    async def test_custom_radius_m_in_bind(self):
        """사용자 지정 radius_m이 bind에 반영된다."""
        session = _make_session([])
        await map_search(session, _CENTER_LAT, _CENTER_LNG, radius_m=500)
        bind = session.execute.call_args[0][1]
        assert bind["radius_m"] == 500

    async def test_default_top_k_in_bind(self):
        """기본 top_k가 bind 파라미터에 포함된다."""
        session = _make_session([])
        await map_search(session, _CENTER_LAT, _CENTER_LNG)
        bind = session.execute.call_args[0][1]
        assert bind["top_k"] == TOP_K

    async def test_custom_top_k_in_bind(self):
        """사용자 지정 top_k가 bind에 반영된다."""
        session = _make_session([])
        await map_search(session, _CENTER_LAT, _CENTER_LNG, top_k=5)
        bind = session.execute.call_args[0][1]
        assert bind["top_k"] == 5


class TestMapSearchSqlContent:
    async def test_earthdistance_in_sql(self):
        """SQL 문자열에 earth_distance 함수 호출이 포함된다."""
        executed_sqls: list[str] = []

        async def _capture(stmt, params=None):
            executed_sqls.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await map_search(session, _CENTER_LAT, _CENTER_LNG)

        sql_text = executed_sqls[0]
        assert "earth_distance" in sql_text
        assert "ll_to_earth" in sql_text
        assert "deleted_at IS NULL" in sql_text

    async def test_cte_used_for_single_distance_computation(self):
        """CTE(WITH candidates)로 distance_m을 한 번만 계산한다 (중복 계산 방지)."""
        executed_sqls: list[str] = []

        async def _capture(stmt, params=None):
            executed_sqls.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await map_search(session, _CENTER_LAT, _CENTER_LNG)

        sql_text = executed_sqls[0]
        # CTE 구조: WITH candidates AS (...) SELECT * FROM candidates WHERE distance_m <= ...
        assert "WITH" in sql_text or "candidates" in sql_text
        # earth_distance 호출이 정확히 1번만 나타나야 한다 (중복 없음)
        assert sql_text.count("earth_distance") == 1

    async def test_earth_box_prefilter_in_sql(self):
        """GiST 인덱스 활용을 위한 earth_box <@ 조건이 CTE 내부에 포함된다."""
        executed_sqls: list[str] = []

        async def _capture(stmt, params=None):
            executed_sqls.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await map_search(session, _CENTER_LAT, _CENTER_LNG)

        sql_text = executed_sqls[0]
        assert "earth_box" in sql_text
        assert "<@" in sql_text

    async def test_coord_null_filter_in_sql(self):
        """SQL에 coord_x/coord_y IS NOT NULL 필터가 포함된다 (NUMERIC 호환).

        coord_x/coord_y 는 NUMERIC 또는 VARCHAR 타입일 수 있다.
        빈 문자열 비교(`<> ''`)는 NUMERIC 타입에서 타입 오류를 유발하므로 사용하지 않는다.
        IS NOT NULL 만으로 NULL 행을 충분히 제거한다.
        """
        executed_sqls: list[str] = []

        async def _capture(stmt, params=None):
            executed_sqls.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await map_search(session, _CENTER_LAT, _CENTER_LNG)

        sql_text = executed_sqls[0]
        assert "coord_x IS NOT NULL" in sql_text
        assert "coord_y IS NOT NULL" in sql_text
        assert "coord_x <> ''" not in sql_text
        assert "coord_y <> ''" not in sql_text


class TestToGeoJson:
    def test_feature_has_point_geometry(self):
        """각 Feature는 Point 타입의 geometry를 가진다."""
        rows = [{"service_id": "M001", "coord_x": "126.9", "coord_y": "37.5", "distance_m": 100}]
        result = _to_geojson(rows)
        feature = result["features"][0]
        assert feature["geometry"]["type"] == "Point"

    def test_coordinates_are_lng_lat_order(self):
        """GeoJSON 표준에 따라 coordinates는 [경도, 위도] 순서다."""
        rows = [{"service_id": "M001", "coord_x": "126.9", "coord_y": "37.5", "distance_m": 100}]
        result = _to_geojson(rows)
        coords = result["features"][0]["geometry"]["coordinates"]
        assert coords == [126.9, 37.5]  # [lng, lat]

    def test_coord_fields_excluded_from_properties(self):
        """coord_x/coord_y는 geometry에 들어가므로 properties에서 제외된다."""
        rows = [{"service_id": "M001", "coord_x": "126.9", "coord_y": "37.5", "distance_m": 100}]
        result = _to_geojson(rows)
        props = result["features"][0]["properties"]
        assert "coord_x" not in props
        assert "coord_y" not in props

    def test_distance_m_in_properties(self):
        """distance_m은 properties에 포함된다."""
        rows = [{"service_id": "M001", "coord_x": "126.9", "coord_y": "37.5", "distance_m": 320}]
        result = _to_geojson(rows)
        assert result["features"][0]["properties"]["distance_m"] == 320

    def test_invalid_coord_yields_none_geometry(self):
        """coord_x/coord_y가 숫자로 변환 불가능하면 geometry=None이다."""
        rows = [{"service_id": "M001", "coord_x": "N/A", "coord_y": None, "distance_m": 0}]
        result = _to_geojson(rows)
        assert result["features"][0]["geometry"] is None

    def test_empty_rows_returns_empty_features(self):
        """빈 rows 입력 시 features=[] 를 반환한다."""
        result = _to_geojson([])
        assert result == {"type": "FeatureCollection", "features": []}

    def test_multiple_rows(self):
        """여러 행이 각각 Feature로 변환된다."""
        rows = [
            {"service_id": "M001", "coord_x": "126.9", "coord_y": "37.5", "distance_m": 100},
            {"service_id": "M002", "coord_x": "126.8", "coord_y": "37.4", "distance_m": 500},
        ]
        result = _to_geojson(rows)
        assert len(result["features"]) == 2
        assert result["features"][0]["properties"]["service_id"] == "M001"
        assert result["features"][1]["properties"]["service_id"] == "M002"
