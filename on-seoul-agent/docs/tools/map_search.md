# map_search

PostgreSQL `earthdistance` + `cube` 확장을 사용하여 사용자 위치(위도·경도) 기준
반경 내 시설을 거리 오름차순으로 조회하고 GeoJSON FeatureCollection으로 반환합니다.

**전제 조건:** DB에 `cube` 및 `earthdistance` 확장이 활성화되어 있어야 합니다.
(`scripts/ddl_indexes_on_data.sql` 참고)

## 시그니처

```python
async def map_search(
    session: AsyncSession,
    lat: float,
    lng: float,
    *,
    radius_m: int = DEFAULT_RADIUS_M,  # 기본값: 1000
    top_k: int = TOP_K,                # 기본값: 20
) -> dict:
```

## 파라미터

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `session` | `AsyncSession` | `on_data_reader` 계정 세션 (SELECT 전용) |
| `lat` | `float` | 기준점 위도. 유효 범위: -90.0 ~ 90.0 |
| `lng` | `float` | 기준점 경도. 유효 범위: -180.0 ~ 180.0 |
| `radius_m` | `int` | 검색 반경 (미터). 기본값: 1000 |
| `top_k` | `int` | 최대 반환 건수. 기본값: 20 |

## 반환값

`dict` — GeoJSON FeatureCollection. 각 Feature의 `geometry.coordinates`는
`[경도, 위도]` 순서(GeoJSON 표준)이며, `properties`에는 시설 정보와
`distance_m`(정수, 미터)이 포함됩니다.

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Point",
        "coordinates": [126.9010, 37.5580]
      },
      "properties": {
        "service_id": "M001",
        "service_name": "마포 체육관",
        "distance_m": 320
      }
    }
  ]
}
```

시설이 없으면 `{"type": "FeatureCollection", "features": []}` 를 반환합니다.

## 사용 예

```python
from tools.map_search import map_search

geojson = await map_search(
    session,
    lat=37.5665,
    lng=126.9780,
    radius_m=500,
)
```
