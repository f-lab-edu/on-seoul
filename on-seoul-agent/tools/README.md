# tools 모듈

에이전트가 공유하는 데이터 조회 도구 모음입니다. 각 tool은 특정 DB 계정과
쿼리 전략에 특화된 독립 함수로, 에이전트가 직접 SQL을 작성하거나 벡터 연산을
다루지 않아도 되도록 추상화합니다.

```
tools/
├── sql_search.py     # 카테고리·지역·상태·키워드 필터 → list[dict]
├── vector_search.py  # 임베딩 벡터 코사인 유사도 검색 → list[dict]
└── map_search.py     # earthdistance 반경 검색 → GeoJSON FeatureCollection
```

---

## 도구 선택 가이드

| 상황 | 도구 |
|---|---|
| 카테고리·지역·상태·키워드로 정형 필터링 | [`sql_search`](../docs/tools/sql_search.md) |
| 자연어 의미 기반 유사도 검색 | [`vector_search`](../docs/tools/vector_search.md) |
| 사용자 위치 기준 반경 내 시설 탐색 | [`map_search`](../docs/tools/map_search.md) |

## DB 세션 라우팅

| Tool | DB | 이유 |
|---|---|---|
| `sql_search` | `on_data` (`data_session`) | `public_service_reservations` 정형 데이터 |
| `vector_search` | `on_ai` (`ai_session`) | `service_embeddings` 벡터 인덱스 |
| `map_search` | `on_data` (`data_session`) | `public_service_reservations` 위치 데이터 |
