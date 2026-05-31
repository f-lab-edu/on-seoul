# AI 에이전트 설계

> Phase 17(LangGraph 전환) 완료 기준. 진입 파일은 `agents/graph.py` (`AgentGraph`)이며, 기존 `agents/workflow.py`(LCEL)는 레거시로 유지된다. `AgentState` 입출력 규약은 동일하다.

---

## 1. 개요

`on-seoul-agent`는 사용자의 자연어 질문을 받아, 의도를 분류하고 적절한 검색 도구를 호출한 뒤, 자연어 답변과 시설 카드를 생성하는 **멀티 에이전트** 서비스이다. LangGraph `StateGraph` 위에서 노드와 조건부 엣지로 조립된다.

| 구성 요소 | 위치 | 역할 |
|---|---|---|
| **에이전트** (Agent) | `agents/` | 의도 분류, 파라미터 추출, 답변 생성 |
| **도구** (Tool) | `tools/` | DB 조회 추상화 (SQL / 벡터 / BM25 / 지도) |
| **그래프** | `agents/graph.py` | LangGraph `StateGraph` 노드·엣지 조립 및 실행 |

---

## 2. 전체 흐름

```mermaid
flowchart TD
    START(["사용자 메시지"])
    ROUTER["router_node<br/>(Router Agent)"]
    CACHE_CHECK["cache_check_node<br/>(Answer Cache lookup)"]
    SQL["sql_node<br/>(SQL Agent)"]
    VECTOR["vector_node<br/>(Vector Agent)<br/>BM25 + vector RRF"]
    MAP["map_node<br/>(map_search)<br/>lat/lng 미제공 시<br/>map_results=None"]
    ANALYTICS["analytics_node<br/>(Analytics Agent)<br/>analytics_search 집계"]
    ANSWER["answer_node<br/>(Answer Agent)"]
    SELF_CORR{{"self_correction<br/>answer 비고 retry=0?"}}
    RETRY_PREP["retry_prep_node<br/>(search_channels 리셋)"]
    CACHE_WRITE["cache_write_node<br/>(Answer Cache 저장)"]
    SEARCH_PERSIST(["search_persist_node<br/>chat_search_queries +<br/>chat_search_results 적재"])
    TRACE(["trace_node<br/>chat_agent_traces 적재"])

    START --> ROUTER
    ROUTER --> CACHE_CHECK
    CACHE_CHECK -- "hit" --> SEARCH_PERSIST
    CACHE_CHECK -- "miss · SQL_SEARCH" --> SQL
    CACHE_CHECK -- "miss · VECTOR_SEARCH" --> VECTOR
    CACHE_CHECK -- "miss · MAP" --> MAP
    CACHE_CHECK -- "miss · ANALYTICS" --> ANALYTICS
    CACHE_CHECK -- "miss · FALLBACK 또는 router 예외" --> ANSWER

    SQL --> ANSWER
    VECTOR --> ANSWER
    MAP --> ANSWER
    ANALYTICS --> ANSWER

    ANSWER --> SELF_CORR
    SELF_CORR -- "Yes (최대 1회)" --> RETRY_PREP
    RETRY_PREP --> ROUTER
    SELF_CORR -- "No" --> CACHE_WRITE
    CACHE_WRITE --> SEARCH_PERSIST
    SEARCH_PERSIST --> TRACE
```

각 노드는 공유 상태인 **`AgentState`** (TypedDict) 를 입력받아 부분 업데이트 dict를 반환한다. LangGraph가 상태 병합을 담당하므로 노드 내부에서 직접 변이하지 않는다. 그래프 전체에는 `recursion_limit=15`이 적용되어 무한 사이클을 방지한다.

> **종단 체인 일관성**: cache hit 경로도 `search_persist_node` 를 경유한다. 빈 `search_channels` 에서는 즉시 skip 되므로 오버헤드는 없으나, 명시적으로 통과시켜 "cache_write → search_persist → trace" 의 종단 체인 형태를 항상 동일하게 유지한다.

---

## 3. 에이전트 (Agents)

### 3-1. Router Agent — 의도 분류

LCEL `prompt | llm.with_structured_output(_IntentOutput)` 으로 사용자 메시지를 `IntentType` 4종 중 하나로 분류한다.

| 입출력 | 필드 |
|---|---|
| **in** | `message` |
| **out** | `intent` |

**예외 처리**: `_router_node` 내부에서 예외가 발생하면 `error`(예외 메시지)와 `answer`(fallback 안내 메시지)를 모두 state에 주입하고, `_node_path` 에 `"router_error"` 를 append한다. 후속 self-correction 엣지는 `answer`가 비어있지 않으므로 즉시 `trace_node` 로 종료한다 (무한루프 방지).

### 3-2. SQL Agent — 정형 데이터 조회

LLM이 SQL을 직접 생성하지 않는다. 메시지에서 필터 파라미터만 구조화 출력으로 추출한 뒤 `sql_search` 도구를 호출한다.

| 입출력 | 필드 |
|---|---|
| **in** | `message` |
| **out** | `sql_results` |

### 3-3. Vector Agent — 의미 기반 검색 (4채널 RRF 하이브리드)

> Task 1~6에서 4채널 순차 실행 + RRF 결합으로 확장되었다.

1. **질의 정제** — LLM으로 사용자 질의를 벡터 검색용 문장으로 정제하고, post-filter용 파라미터(`max_class_name`, `area_name`, `service_status`)와 `vector_sub_intent`를 함께 추출한다.
2. **4채널 순차 실행 (ai_session)** — asyncpg 단일 세션 제약으로 `gather` 대신 순차 실행한다.
   - **Track A**: `vector_search(row_kind="identity")` — 시설 신원 임베딩, post-filter 적용
   - **Track B**: `vector_search(row_kind="summary")` — 자연어 요약 임베딩, post-filter 미적용
   - **Track C**: `question_search()` — 예상 질문 임베딩, `service_id`별 dedup
   - **BM25**: `bm25_search()` — `llm/tokenizer.py` (Lindera KoDic + `DOMAIN_TOKENS`)로 토큰화 후 호출
3. **RRF 결합** — `core/rrf.py`의 `reciprocal_rank_fusion`으로 4채널 결과를 통합한다. Phase 1은 `rrf_unweighted_baseline=True` (모든 채널 가중치 1.0).
4. **원본 Hydration (data_session)** — RRF 결과의 `service_id`로 `tools/hydrate_services`를 호출하여 `public_service_reservations` 최신 원본 행을 가져온다. 임베딩 metadata의 stale 필드(`service_status`·`receipt_*_dt` 등) 우회 목적. 개별 service_id 가 원본 테이블에 없거나 soft-delete 된 경우 해당 행만 결과에서 제외된다. `hydrate_services` 도구 호출 자체가 예외를 던지면 `vector_results = []` 로 폴백하여 stale metadata 가 답변에 노출되지 않도록 한다.
5. `vector_results`에 hydrated 결과 + `rrf_score`를 저장한다. 스키마는 `sql_results`와 동일.

| 입출력 | 필드 |
|---|---|
| **in** | `message`, `vector_sub_intent` |
| **out** | `refined_query`, `vector_results` |

> Phase 18부터 VectorAgent.search()는 `ai_session`(검색) + `data_session`(hydration) 두 세션을 모두 받는다.

**BM25 도입 배경**: ParadeDB Lindera의 `user_dictionary`는 SQL API 레벨에서 지원되지 않아 커스텀 사전 적용에 소스 빌드가 필요했다. Python 레이어에서 lindera-py로 사전 토크나이징한 뒤 BM25 쿼리 조건을 구성하는 방식으로 우회한다. 도메인 용어(예: "따릉이", "한강공원")는 `DOMAIN_TOKENS` 화이트리스트로 보존된다.

### 3-4. Analytics Agent — 집계 질의 처리

LLM이 SQL을 직접 생성하지 않는다. 메시지에서 집계 파라미터(group_by, metric, keyword 등)를 구조화 출력으로 추출한 뒤 `analytics_search` 도구를 호출한다. hydration 단계는 거치지 않는다.

| 입출력 | 필드 |
|---|---|
| **in** | `message`, `max_class_name`, `area_name`, `service_status` |
| **out** | `analytics_results`, `analytics_group_by`, `analytics_metric`, `analytics_keyword` |

### 3-5. Answer Agent — 답변 생성

검색 결과를 자연어 답변 + 시설 카드로 가공한다.

| 입출력 | 필드 |
|---|---|
| **in** | `message`, `intent`, `sql_results`, `vector_results`, `map_results`, `title_needed` |
| **out** | `answer`, `title` (`title_needed=True` 일 때) |

특이사항:

- `sql_results` / `vector_results` / `map_results` / `analytics_results` 를 intent별로 분기하여 LLM에 전달한다.
- `service_url` 이 없으면 `https://yeyak.seoul.go.kr` 로 fallback한다.
- `title_needed=True` 이면 대화 제목(10자 이내)을 별도 LLM 호출로 생성한다.
- 입력 state에 이미 `error` + `answer` 가 모두 채워져 있으면(router 예외 fast-path) 추가 LLM 호출 없이 즉시 반환한다.

**2-tier 프롬프트 조립 구조:**

- **Tier 1 (정적 조립)**: MAP / ANALYTICS / FALLBACK 프롬프트는 `__init__` 시 1회 조립되어 `_static_prompts` dict에 캐시된다. 실행 경로마다 프롬프트 객체를 재생성하지 않는다.
- **Tier 2 (런타임 조립)**: 카드형(SQL / VECTOR) 프롬프트는 매 호출마다 조건부 절을 코드로 판단해 조합한다. 접수중 여부, 자치구 명시 여부에 따라 해당 절을 포함하거나 제외하여 LLM 컨텍스트를 최소화한다.
- ANALYTICS / FALLBACK intent는 `service_cards=[]`를 반환한다 — 집계/안내 답변은 카드 UI를 사용하지 않는다.

### 3-6. IntentType 분류 기준

| IntentType | 분류 기준 | 예시 | 비고 |
|---|---|---|---|
| `SQL_SEARCH` | 카테고리·자치구·접수 상태·날짜 등 정형 조건 | "지금 접수 중인 수영장" | 개별 목록 반환 |
| `VECTOR_SEARCH` | 키워드·의미 기반 유사 시설 탐색 | "아이랑 체험할 수 있는 곳" | `vector_sub_intent`: `identification` / `detail` / `semantic` |
| `MAP` | 지도·위치·반경 탐색 | "내 주변 500m 이내 체육관" | GeoJSON 반환 |
| `ANALYTICS` | 개수·분포·종류 등 집계/요약 질의 | "강남구에 체육시설이 몇 개야?" | 통계/카운트 반환. hydration 생략. SQL_SEARCH(개별 목록)와 구별 |
| `FALLBACK` | 인사·기능 문의 등 그 외 | "어떤 서비스를 제공하나요?" | service_cards=[] |

---

## 4. 도구 (Tools)

에이전트는 SQL을 직접 작성하거나 벡터 연산을 직접 다루지 않는다. DB 조회는 아래 다섯 도구로 위임된다.

### 4-1. `sql_search` — 정형 필터 조회

`on_data.public_service_reservations` 테이블을 파라미터화 SQL로 조회한다. 모든 필터 값은 bind 파라미터로 전달하므로 SQL Injection 위험이 없다.

| 파라미터 | 설명 |
|---|---|
| `max_class_name` | 대분류 카테고리 (체육시설·문화행사·시설대관·교육·진료) |
| `area_name` | 서울 자치구 (예: 마포구) |
| `service_status` | 예약 상태 (접수중·예약마감·접수종료·예약일시중지·안내중) |
| `keyword` | 시설명·장소명 키워드 (`%keyword%` ILIKE) |
| `top_k` | 최대 반환 건수 (기본값: 10) |

### 4-2. `vector_search` — 의미 기반 유사도 검색 (post-filter)

전체 임베딩에서 유사도 상위 `scan_k`(`top_k × SCAN_K_MULTIPLIER`)를 먼저 뽑은 뒤, 서브쿼리 외부에서 `max_class_name`, `area_name`, `service_status` 를 post-filter로 적용한다.

**Post-filter를 채택한 이유 (Phase 15)**: pgvector HNSW 인덱스는 WHERE 조건과 동시 동작하지 않아, pre-filter를 적용하면 인덱스를 우회해 sequential scan으로 빠진다. 전체를 HNSW로 먼저 검색하고 후처리로 필터링하는 쪽이 인덱스 효율과 검색 품질 모두 유리하다. `scan_k`를 충분히 크게 잡아 필터 탈락으로 인한 결과 부족을 완충한다.

| 파라미터 | 설명 |
|---|---|
| `query_vector` | 쿼리 임베딩 벡터 |
| `max_class_name`, `area_name`, `service_status` | post-filter (None이면 미적용) |
| `top_k` | 최종 반환 건수 (기본값: 10) |
| `min_similarity` | 코사인 유사도 하한 (기본값 0.6) |

### 4-3. `bm25_search` — ParadeDB 전문 검색 (Phase 14 신설)

`service_embeddings` 테이블의 ParadeDB BM25 인덱스를 사용해 한국어 형태소 기반 키워드 매칭을 수행한다. 토큰 배열은 Python 레이어에서 lindera-py 로 사전 분해한 결과를 사용한다.

| 파라미터 | 설명 |
|---|---|
| `tokens` | `llm/tokenizer.py` 로 사전 분해된 토큰 배열 |
| `top_k` | 반환 건수 (기본값: 10) |

반환: `(service_id, bm25_score)` 목록. Vector Agent에서 vector 결과와 RRF로 결합된다.

### 4-3.5. `hydrate_services` — 검색 결과 원본 hydration (Phase 18 신설)

RRF 결합 후 추출한 `service_id` 리스트로 `public_service_reservations`에서 최신 원본 행을 조회한다. 임베딩 metadata의 stale 필드를 우회하여 답변 정확도를 보장한다.

| 파라미터 | 설명 |
|---|---|
| `session` | `on_data_reader` 계정 AsyncSession (SELECT 전용) |
| `service_ids` | 검색 순위 순서대로 정렬된 `service_id` 리스트 |

반환: 입력 순서를 유지한 원본 행 리스트. 원본에 없거나 soft-delete된 service_id는 자동 제외. 스키마는 `sql_search` 와 동일. 컬럼 목록은 `docs/tools/hydrate_services.md` 를 참조한다.

### 4-4. `map_search` — 위치 기반 반경 검색

PostgreSQL `earthdistance` + `cube` 확장으로 사용자 위치(위도·경도) 기준 반경 내 시설을 거리 오름차순으로 조회하고 GeoJSON FeatureCollection으로 반환한다. lat/lng 미전송 시 FALLBACK으로 대체된다.

| 파라미터 | 설명 |
|---|---|
| `user_lat`, `user_lng` | 기준점 위도·경도 |
| `radius_m` | 검색 반경 (미터, 기본값: 1000) |
| `top_k` | 최대 반환 건수 (기본값: 20) |

반환값: `GeoJSON FeatureCollection` — 각 Feature의 `properties` 에 시설 정보와 `distance_m` 포함.

### 4-5. `analytics_search` — 집계/분포 조회

`on_data.public_service_reservations` 테이블에서 GROUP BY COUNT 또는 SELECT DISTINCT 집계를 실행한다. LLM이 SQL을 생성하지 않으며, 컬럼명은 화이트리스트 dict 값만 f-string으로 삽입하여 SQL Injection을 원천 차단한다. 필터 값과 top_k는 전부 bind 파라미터로 처리한다.

| 파라미터 | 설명 |
|---|---|
| `group_by` | 집계 차원. 허용값: `area_name` / `max_class_name` / `min_class_name` / `service_status` |
| `metric` | 집계 방식. `count`(건수 집계) 또는 `distinct`(고유값 목록) |
| `max_class_name` | 필터: 대분류 카테고리. None이면 미적용 |
| `area_name` | 필터: 서울 자치구. None이면 미적용 |
| `service_status` | 필터: 예약 상태. None이면 미적용 |
| `keyword` | 필터: 시설명·장소명 키워드 (`%keyword%` ILIKE). None이면 미적용 |
| `top_k` | 최대 반환 건수 (기본값: 20) |

반환: `metric=count` → `[{"group_value": ..., "count": ...}]`, `metric=distinct` → `[{"group_value": ...}]`. 결과가 없으면 빈 리스트.

`on_data_reader`(SELECT 전용) 세션을 사용한다. 호출처: `AnalyticsAgent` (`agents/analytics_agent.py`).

### 4-6. 도구 선택 기준

| 상황 | 도구 |
|---|---|
| 카테고리·지역·상태·키워드로 정형 필터링 | `sql_search` |
| 자연어 의미 기반 유사도 검색 | `vector_search` + `bm25_search` (RRF 결합) |
| 사용자 위치 기준 반경 내 시설 탐색 | `map_search` |
| 개수·분포·종류 등 집계/요약 질의 | `analytics_search` |

---

## 5. 공유 상태 — AgentState

에이전트 간 데이터는 `AgentState` (TypedDict)로 흐른다. LangGraph가 부분 업데이트 dict를 자동으로 병합한다.

| 필드 | 작성 주체 | 설명 |
|---|---|---|
| `room_id`, `message_id`, `message`, `title_needed` | 호출자 | 입력 |
| `user_lat`, `user_lng` | 호출자 | MAP intent 용 위치 |
| `intent` | router_node | 분류된 의도 |
| `vector_sub_intent` | router_node | Router가 분류한 벡터 검색 세부 의도 (`identification`/`detail`/`semantic`). VECTOR_SEARCH 전용 |
| `refined_query` | router_node / vector_node | 벡터 검색용 정제 질의 (router 1차 산출, 미산출 시 vector_node fallback) |
| `max_class_name`, `area_name`, `service_status` | router_node | post-filter 메타데이터 |
| `sql_results` | sql_node | SQL 조회 결과 |
| `sql_keyword` | sql_node | SqlAgent 가 LLM 으로 추출한 keyword (search_persist 의 sql 채널 query_text 로 사용) |
| `vector_results` | vector_node | BM25 + vector RRF 결합 결과 |
| `map_results` | map_node | 반경 검색 GeoJSON |
| `analytics_results` | analytics_node | `analytics_search` 집계 결과 리스트 |
| `analytics_group_by` | analytics_node | LLM이 추출한 집계 차원 (area_name / max_class_name / min_class_name / service_status) |
| `analytics_metric` | analytics_node | LLM이 추출한 집계 방식 (`count` / `distinct`) |
| `analytics_keyword` | analytics_node | LLM이 추출한 키워드 필터 (없으면 None) |
| `search_channels` | sql/vector/map_node + retry_prep_node | `dict[str, ChannelData]` — 채널별 입력(query) + 출력(hits). reducer `search_channels_reducer` 로 누적, `{}` 시그널로 리셋 |
| `answer`, `title` | answer_node | 최종 답변 / 대화 제목 |
| `cache_hit` | cache_check_node | Answer Cache hit 여부 |
| `trace` | trace_node | `intent`, `node_path`, `elapsed_ms` |
| `error` | 각 노드 | 오류 메시지 |
| `retry_count` | retry_prep_node | 자기 교정 재시도 횟수 (0=초기, 최대 1) |

---

## 6. DB 세션 라우팅

| 노드 / 작업 | 세션 | DB | 대상 테이블 |
|---|---|---|---|
| sql_node → `sql_search` | `data_session` | `on_data` | `public_service_reservations` |
| vector_node → `vector_search` / `bm25_search` | `ai_session` | `on_ai` | `service_embeddings` |
| vector_node → `hydrate_services` | `data_session` | `on_data` | `public_service_reservations` |
| map_node → `map_search` | `data_session` | `on_data` | `public_service_reservations` (earthdistance) |
| analytics_node → `analytics_search` | `data_session` | `on_data` | `public_service_reservations` (GROUP BY / DISTINCT) |
| search_persist_node | `ai_session` | `on_ai` | `chat_search_queries`, `chat_search_results` |
| trace_node | `ai_session` | `on_ai` | `chat_agent_traces` |

`search_persist_node` 와 `trace_node` 는 동일 `ai_session` 을 사용한다. `search_persist_node` 가 항상 commit() 또는 rollback() 으로 트랜잭션을 닫으므로 trace_node 진입 시 세션은 clean 상태가 보장된다.

---

## 7. 그래프 실행

`AgentGraph.run(state, *, data_session, ai_session)` 한 번 호출로 router → 분기 → answer → self-correction → trace 적재가 끝난다. `AgentGraph.stream(...)`은 동일 실행을 `(event_type, data)` 튜플 스트림으로 반환하여 SSE 릴레이에 사용된다.

```python
from agents.graph import AgentGraph

graph = AgentGraph()
result = await graph.run(
    state={
        "room_id": 1, "message_id": 42,
        "message": "마포구 접수 중인 수영장",
        "title_needed": True,
        "retry_count": 0,
        # 나머지 필드는 None 으로 초기화
    },
    data_session=data_session,
    ai_session=ai_session,
)
```

각 에이전트는 생성자 주입으로 교체 가능하여 단위 테스트에서 Mock으로 대체한다. `CompiledStateGraph`는 `ClassVar` 로 캐시되어 프로세스당 1회만 컴파일된다.

### 7-1. 조건부 엣지

| 엣지 | 분기 함수 | 동작 |
|---|---|---|
| `router_node → ?` | `_route_by_intent` | `intent` 값으로 다음 노드 결정 (`SQL_SEARCH`→sql, `VECTOR_SEARCH`→vector, `MAP`→map, `ANALYTICS`→analytics, `FALLBACK`/그 외→answer). router 예외 시 `error + answer` 가 채워져 있으면 `answer_node` 로 단락. `MAP` 의 lat/lng 미제공 처리는 `map_node` 내부에서 담당한다. |
| `answer_node → ?` | `_self_correction_edge` | `not answer.strip() and retry_count == 0` 이면 `router_node` 재진입, 아니면 `trace_node` 로 진행. |

### 7-2. Self-Correction 사이클 (Phase 17)

LangGraph 의 cycle 기능을 활용한 1회 한정 재시도 루프:

```
answer_node → [_self_correction_edge]
                  ├─ needs_retry → router_node (retry_count=1로 증가)
                  └─ otherwise   → trace_node (종료)
```

**재진입 감지 (`_router_node`)**: `_node_path`에 정상/예외 경로 어느 것이든 `"router"`로 시작하는 항목이 있으면 재진입으로 간주한다.

```python
is_retry = any(p.startswith("router") for p in self._node_path)
retry_count = 1 if is_retry else state.get("retry_count", 0)
```

**재시도 트리거 조건 (`_self_correction_edge`)**:

```python
needs_retry = not answer.strip() and retry_count == 0
```

- `answer`가 비어 있을 때만 재검색이 의미 있다. error + fallback_answer 조합은 이미 최선의 응답이므로 재시도하지 않는다.
- `retry_count >= 1`이면 항상 `trace_node` 로 진행 — 최대 1회 보장.
- 추가로 그래프 호출 시 `recursion_limit=10`을 명시하여 어떤 경우에도 무한 사이클을 차단한다.

---

## 8. 오류 처리

운영 시점에 사용자 응답 품질과 디버깅에 직결되는 정책이므로 별도 섹션으로 정리한다.

### 8-1. `error` 필드의 의미

- 노드 실행 중 발생한 예외 메시지를 문자열로 저장한다.
- `None` 이면 정상 완료, 값이 있으면 그래프 어딘가에서 예외가 발생했음을 의미한다.
- `trace.error` 에도 동일한 값이 기록되어 `chat_agent_traces` 테이블로 영구 보존된다.
- SSE `workflow_error` 이벤트로 전달될 때는 내부 메시지가 그대로 노출되지 않도록 sanitize 된다 ("서비스 처리 중 오류가 발생했습니다.").

### 8-2. Fallback 메시지 정책

| 상황 | 처리 |
|---|---|
| router_node 예외 발생 | `answer` 에 안내 메시지 주입, `error` 에 원인 기록, `node_path` 에 `"router_error"` append, self-correction 우회 |
| sql/vector/map/answer 노드 예외 | `error` 필드 기록, `node_path` 에 `"*_error"` append, 가능하면 빈 결과로 다음 노드 진행 |
| `MAP` intent 인데 `lat`/`lng` 미제공 | `map_node` 내부에서 검색을 생략하고 `map_results=None`을 반환한 뒤 `answer_node` 로 진행. `node_path` 에는 정상 경로와 동일하게 `"map_node"`가 append된다. |
| Answer Agent 결과의 `service_url` 누락 | `https://yeyak.seoul.go.kr` 로 fallback |

예외 발생 시 사용자에게 노출되는 메시지:

> 죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.

### 8-3. Trace best-effort 정책

`chat_agent_traces` 적재는 `trace_node` 에서 실행되며, 다음 원칙을 따른다.

- **저장 실패는 그래프 결과에 영향을 주지 않는다.** 사용자 응답이 trace 저장 실패 때문에 손실되지 않도록 보장한다.
- 저장 실패 시 `logger.warning` 으로만 기록하고 세션을 rollback한다.
- 본문 노드에서 예외가 발생하더라도 `trace_node` 가 종단 노드로 항상 도달하므로, 실패한 실행도 분석 가능하다.

### 8-4. Search Persist best-effort 정책

`chat_search_queries` + `chat_search_results` 적재는 `search_persist_node` 에서 실행되며, trace 와 동일한 best-effort 원칙을 따른다.

- **두 테이블은 단일 트랜잭션** 으로 묶여 함께 commit / rollback 된다 — 한쪽만 적재되는 일관성 깨짐을 방지한다.
- 0건 결과여도 `chat_search_queries` 의 query 행은 기록한다 ("검색했는데 결과 없음" 도 recall / stopword 진단의 신호).
- INSERT 실패 시 `logger.warning` + rollback + `node_path += "search_persist_error"` 만 남기고 `trace_node` 로 진행한다.
- 빈 `search_channels` 에서는 INSERT 없이 즉시 skip — cache hit 경로와 FALLBACK intent 가 여기에 해당한다.
- self-correction 재시도 시 `retry_prep_node` 가 `search_channels = {}` 를 반환하면 `search_channels_reducer` 가 누적 dict 를 리셋한다. 마지막 시도의 채널만 적재된다 — UNIQUE `(message_id, channel)` 위반 방지.
- 방어적 안전망으로 두 INSERT 모두 `ON CONFLICT DO NOTHING` 으로 묶인다.

운영 가이드와 분석 쿼리 예시는 [`docs/chat-search-persistence.md`](chat-search-persistence.md) 를 참조한다.

---

## 9. 변경 이력

| 날짜 | Phase | 변경 |
|---|---|---|
| Phase 14 | BM25 하이브리드 검색 | `llm/tokenizer.py` + `tools/bm25_search.py` 추가, Vector Agent에서 BM25/vector RRF 결합 |
| Phase 15 | vector_search post-filter | pre-filter(WHERE) → post-filter(서브쿼리) 전환. Vector Agent에서 post-filter 파라미터 전달 |
| Phase 16 | 통합 테스트 | `test_integration_workflow.py`, `test_chat_router.py` E2E 시나리오 |
| Phase 17 | LangGraph 전환 | `agents/graph.py` 신설(`AgentGraph`), Self-Correction 사이클, `AgentState.retry_count` 추가, `_router_node` 예외 시 fallback_answer fast-path, `recursion_limit=10` |
| Phase 18 | 원본 hydration 도입 | `tools/hydrate_services` 신설, VectorAgent에서 RRF 후 `public_service_reservations` 조회. 답변 컨텍스트가 항상 최신 원본 값을 사용하도록 변경. `AnswerAgent._normalize`의 metadata 언팩 분기 제거. |
| Phase 19 | 검색 채널 적재 (chat-search-persistence) | `chat_search_queries` + `chat_search_results` 두 테이블 도입. `AgentState.search_channels: dict[str, ChannelData]` 필드(reducer 적용) + 종단 `search_persist_node` 신설. sql/vector/map 노드가 채널별 `ChannelData(kind, query, hits)` 를 채우고, `retry_prep_node` 가 재시도 시 `search_channels = {}` 리셋. cache hit 경로도 `search_persist_node` 경유로 종단 체인 일관성 유지. `recursion_limit` 10 → 15 상향. |
| 2026-05-31 | ANALYTICS intent 신설 (Phase A-E) | `analytics_search` 도구(`tools/analytics_search.py`) 신설 — GROUP BY COUNT / SELECT DISTINCT 집계, 차원 화이트리스트 4종, 전 필터값 bind 파라미터. `AnalyticsAgent`(`agents/analytics_agent.py`) 신설 — LLM 구조화 추출 후 `analytics_search` 호출, hydration 생략. `AgentState`에 `analytics_results` / `analytics_group_by` / `analytics_metric` / `analytics_keyword` 필드 추가. Router에 ANALYTICS 분류 기준 + 경계 케이스 few-shot 추가. Answer Agent에 intent별 2-tier 프롬프트 조립 구조 도입 (Tier 1 정적 캐시, Tier 2 런타임 조건부 조립). AI 서비스 전체. |

`AgentState` 입출력 규약을 유지하므로 각 Agent 클래스(`router_agent.py`, `sql_agent.py`, `vector_agent.py`, `answer_agent.py`)는 수정 없이 재사용된다. `agents/workflow.py` (LCEL)는 레거시로 유지된다.
