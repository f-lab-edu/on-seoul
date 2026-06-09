# tools 모듈

에이전트가 공유하는 데이터 조회 도구 모음입니다. 각 tool은 특정 DB 계정과
쿼리 전략에 특화된 독립 함수로, 에이전트가 직접 SQL을 작성하거나 벡터 연산을
다루지 않아도 되도록 추상화합니다.

```
tools/
├── sql_search.py        # 카테고리·지역·상태·키워드 필터 → list[dict]
├── vector_search.py     # 임베딩 벡터 코사인 유사도 검색 (post-filter) → list[dict]
├── bm25_search.py       # ParadeDB Lindera BM25 전문 검색 → [(service_id, score)]
├── question_search.py   # 예상 질문 임베딩 검색 (service_id별 dedup) → list[dict]
├── hydrate_services.py  # service_id → public_service_reservations 최신 원본 hydration
├── map_search.py        # earthdistance 반경 검색 → GeoJSON FeatureCollection
├── analytics_search.py  # GROUP BY COUNT / SELECT DISTINCT 집계 → list[dict]
├── tokenizer.py         # 한국어 형태소 토큰화 (Kiwi/kiwipiepy + DOMAIN_TOKENS, atokenize_query)
└── _result_columns.py   # 검색 결과 공통 컬럼 정의
```

> Vector Agent는 `vector_search`(identity/summary 트랙) + `question_search` + `bm25_search` 4채널을 RRF로 결합하고, 그래프의 `hydration_node`가 `hydrate_services`로 최신 원본을 채웁니다. W1/W2는 신규 도구를 추가하지 않았으며 `tools/tokenizer.py`(이전 `llm/tokenizer.py`에서 이동)는 BM25 토큰화 및 제안3의 `asyncio.to_thread` 오프로드(`atokenize_query`)에 쓰입니다.

---

## 도구 선택 가이드

| 상황 | 도구 |
|---|---|
| 카테고리·지역·상태·키워드로 정형 필터링 | [`sql_search`](../docs/tools/sql_search.md) |
| 자연어 의미 기반 유사도 검색 | `vector_search` + `bm25_search` + `question_search` (RRF 결합) |
| 검색 결과 service_id → 최신 원본 hydration | [`hydrate_services`](../docs/tools/hydrate_services.md) |
| 사용자 위치 기준 반경 내 시설 탐색 | [`map_search`](../docs/tools/map_search.md) |
| 개수·분포·종류 등 집계/요약 질의 | `analytics_search` |

## DB 세션 라우팅

| Tool | DB | 이유 |
|---|---|---|
| `sql_search` | `on_data` (`data_session`) | `public_service_reservations` 정형 데이터 |
| `vector_search` / `bm25_search` / `question_search` | `on_ai` (`ai_session`) | `service_embeddings` 벡터·BM25 인덱스 |
| `hydrate_services` | `on_data` (`data_session`) | `public_service_reservations` 최신 원본 (SELECT 전용) |
| `map_search` | `on_data` (`data_session`) | `public_service_reservations` 위치 데이터 (earthdistance) |
| `analytics_search` | `on_data` (`data_session`) | `public_service_reservations` GROUP BY / DISTINCT |
