# agents 모듈

사용자 질문을 의도별로 분류하고 검색·답변 생성까지 처리하는 에이전트 모듈입니다.

**책임 범위**:
* LangGraph StateGraph 워크플로우 조립 및 실행 (`graph.py`)
* 의도 분류 (`router_agent.py`)
* 정형 데이터 조회 (`sql_agent.py`)
* 의미 기반 검색 — BM25 + vector 하이브리드 (`vector_agent.py`)
* 자연어 답변 + 시설 카드 생성 (`answer_agent.py`)

각 에이전트는 `AgentState`를 입력받아 필드를 채운 새 `AgentState`를 반환합니다. 상태 변이 없이 `{**state, key: value}` 스프레드 패턴을 사용합니다.

---

## 모듈 구조

```
agents/
├── graph.py          # LangGraph StateGraph 워크플로우 (Router → Search → Answer → Self-Correction)
├── workflow.py       # LangChain(LCEL) 워크플로우 — graph.py 전환 전 레거시
├── router_agent.py   # 사용자 의도 분류 (IntentType 4종)
├── sql_agent.py      # LLM 파라미터 추출 + 파라미터화 SQL 조회
├── vector_agent.py   # 질의 정제 + BM25/vector 하이브리드 검색 (RRF 결합)
└── answer_agent.py   # 검색 결과 → 자연어 답변 + 시설 카드 + 대화 제목
```

---

## 실행 흐름

```
사용자 메시지
  └─ RouterAgent.classify()              # 의도 + refined_query + post-filter 산출
       └─ cache_check_node               # Answer Cache lookup
            ├─ hit  → search_persist_node (빈 채널 skip) → trace_node
            └─ miss → intent 분기
                 ├─ SQL_SEARCH  → SqlAgent.search()        → on_data DB
                 ├─ VECTOR_SEARCH → VectorAgent.search()   → on_ai DB (BM25 + vector RRF)
                 ├─ MAP         → map_search()             → on_data DB (earthdistance)
                 └─ FALLBACK    → (검색 생략)
                      └─ AnswerAgent.answer()              # 자연어 답변 생성
                           └─ Self-Correction 엣지         # answer 빈 + retry=0 시 retry_prep → Router 재진입 (최대 1회)
                                └─ cache_write_node       # Answer Cache 저장 (정상 결과만)
                                     └─ search_persist_node # chat_search_queries + chat_search_results 일괄 적재 (best-effort)
                                          └─ trace_node    # chat_agent_traces 적재 (best-effort)
```

검색 노드(sql/vector/map)는 `AgentState.search_channels: dict[str, ChannelData]` 에 채널별 입력(query)·출력(hits) 쌍을 채우고, 종단 `search_persist_node` 가 일괄 적재한다. `retry_prep_node` 가 재시도 시 `search_channels = {}` 리셋 시그널을 보내 UNIQUE 위반을 방지한다. 자세한 적재 정책은 `docs/chat-search-persistence.md` 참조.

세션 라우팅:

| 에이전트 / 노드 | DB | 이유 |
|---|---|---|
| `SqlAgent` | `on_data` (`data_session`) | `public_service_reservations` 정형 데이터 |
| `VectorAgent` 검색 | `on_ai` (`ai_session`) | `service_embeddings` 벡터 인덱스 |
| `VectorAgent` hydration | `on_data` (`data_session`) | `public_service_reservations` 최신 원본 |
| `search_persist_node` | `on_ai` (`ai_session`) | `chat_search_queries` + `chat_search_results` |
| `trace_node` | `on_ai` (`ai_session`) | `chat_agent_traces` 실행 메타데이터 |

---

## 공유 상태 — AgentState

에이전트 간 데이터는 `schemas.state.AgentState` (TypedDict)로 흐릅니다.

| 필드 | 타입 | 작성 주체 | 설명 |
|---|---|---|---|
| `room_id` | `int` | 호출자 | 대화 방 ID |
| `message_id` | `int` | 호출자 | 메시지 ID (trace 참조용) |
| `message` | `str` | 호출자 | 사용자 원본 질문 |
| `title_needed` | `bool` | 호출자 | 대화 제목 생성 필요 여부 (첫 메시지) |
| `lat`, `lng` | `float \| None` | 호출자 | MAP intent 용 위치 좌표 |
| `intent` | `IntentType \| None` | RouterAgent | 분류된 의도 |
| `refined_query` | `str \| None` | RouterAgent / VectorAgent | 벡터 검색용 정제 질의 (Router 1차, 미산출 시 VectorAgent fallback) |
| `max_class_name`, `area_name`, `service_status` | `str \| None` | RouterAgent | post-filter 메타데이터 |
| `sql_results` | `list[dict] \| None` | SqlAgent | SQL 조회 결과 |
| `sql_keyword` | `str \| None` | SqlAgent | SQL 키워드 (search_persist 의 sql 채널 query_text) |
| `vector_results` | `list[dict] \| None` | VectorAgent | 유사도 검색 결과 |
| `map_results` | `dict \| None` | map_node | 반경 검색 GeoJSON |
| `search_channels` | `dict[str, ChannelData]` | sql/vector/map_node, retry_prep_node | 채널별 입력(query)+출력(hits). reducer 누적, `{}` 리셋. `search_persist_node` 가 일괄 적재 |
| `answer` | `str \| None` | AnswerAgent | 최종 자연어 답변 |
| `title` | `str \| None` | AnswerAgent | 대화 제목 (`title_needed=True`일 때) |
| `cache_hit` | `bool \| None` | cache_check_node | Answer Cache hit 여부 |
| `trace` | `dict \| None` | trace_node | 실행 메타데이터 (intent, node_path, elapsed_ms) |
| `error` | `str \| None` | 각 노드 | 오류 메시지 |
| `retry_count` | `int` | retry_prep_node | 자기 교정 재시도 횟수 (0 = 아직 재시도 없음, 최대 1) |

---

## 주요 컴포넌트

### graph.py — LangGraph 워크플로우

`AgentGraph.run(state, *, data_session, ai_session)` 한 번 호출로 전체 파이프라인을 실행합니다.

```python
from agents.graph import AgentGraph

graph = AgentGraph()
result = await graph.run(
    state={
        "room_id": 1, "message_id": 42,
        "message": "마포구 접수 중인 수영장",
        "title_needed": True,
        "retry_count": 0,
        # 나머지 필드는 None으로 초기화
    },
    data_session=data_session,
    ai_session=ai_session,
)
# result["answer"], result["title"], result["trace"] 사용
```

각 에이전트는 생성자 주입으로 교체할 수 있어 테스트에서 Mock으로 대체합니다.

```python
graph = AgentGraph(router=mock_router, sql_agent=mock_sql)
```

**오류 처리**: `_router_node` 예외 시 fallback answer를 state에 주입하고 Self-Correction 없이 종단 체인(cache_write → search_persist → trace)으로 진행합니다. trace / search_persist 적재는 모두 best-effort로 실행되어 저장 실패가 워크플로우 결과에 영향을 주지 않습니다.

**Self-Correction**: answer가 비어 있고 `retry_count == 0`이면 `retry_prep_node` 를 거쳐 router_node로 재진입해 재검색을 시도합니다. `retry_prep_node` 는 `retry_count` 증가와 함께 `search_channels = {}` 리셋 시그널을 보내 이전 시도의 채널 데이터를 비웁니다. 최대 1회로 제한됩니다 (`recursion_limit=15`).

---

### router_agent.py — 의도 분류

LCEL `prompt | llm.with_structured_output(_IntentOutput)` 체인으로 사용자 메시지를 `IntentType` 4종 중 하나로 분류합니다.

| IntentType | 분류 기준 | 예시 |
|---|---|---|
| `SQL_SEARCH` | 카테고리·자치구·접수 상태·날짜 등 정형 조건 | "지금 접수 중인 수영장" |
| `VECTOR_SEARCH` | 키워드·의미 기반 유사 시설 탐색 | "아이랑 체험할 수 있는 곳" |
| `MAP` | 지도·위치·반경 탐색 | "내 주변 500m 이내 체육관" |
| `FALLBACK` | 인사·기능 문의 등 위 세 가지 외 | "어떤 서비스를 제공하나요?" |

---

### sql_agent.py — 정형 데이터 조회

LLM이 SQL을 직접 생성하지 않습니다. 사용자 메시지에서 필터 파라미터를 구조화 출력으로 추출한 뒤, 고정된 SQL 템플릿에 바인드 파라미터로 주입합니다. SQL Injection 위험이 없습니다.

**추출 파라미터** (`_SqlParams`):

| 필드 | 설명 | 예시 |
|---|---|---|
| `max_class_name` | 대분류 카테고리 | `"체육시설"` |
| `area_name` | 서울 자치구 | `"마포구"` |
| `service_status` | 접수 상태 | `"접수중"` |
| `keyword` | 시설명·장소명 키워드 (ILIKE) | `"수영장"` |

조회 대상: `on_data.public_service_reservations` / 최대 10건 / `receipt_start_dt DESC` 정렬

---

### vector_agent.py — 의미 기반 검색

1. LLM으로 사용자 질의를 벡터 검색에 최적화된 문장으로 정제합니다.
2. 정제된 문장을 Gemini 임베딩 모델로 벡터화합니다.
3. `on_ai.service_embeddings`에서 코사인 유사도 상위 K개를 반환합니다.

조회에는 `tools.vector_search.vector_search()`를 사용합니다. `vector_search`는 post-filter 전략을 사용합니다 — 전체 임베딩에서 유사도 상위 `scan_k`를 먼저 뽑고, 서브쿼리 외부에서 `max_class_name`·`area_name`·`service_status` 필터를 적용합니다. Phase 14에서 Vector Agent가 메시지로부터 이 파라미터를 추출해 `vector_search`에 전달하는 연동을 구현합니다.

---

### answer_agent.py — 답변 생성

`sql_results` / `vector_results` / `map_results`를 단일 목록으로 합쳐 LLM에 전달하고, 자연어 답변과 카드 데이터를 생성합니다.

**시설 카드 필드**: `service_id`, `service_name`, `area_name`, `place_name`, `service_status`, `receipt_start_dt`, `receipt_end_dt`, `service_url`

- `service_url` 이 없으면 `https://yeyak.seoul.go.kr` 으로 fallback합니다.
- `vector_results`의 `metadata` JSONB가 중첩된 경우 자동으로 언팩합니다.
- `title_needed=True` 이면 대화 제목(10자 이내)을 별도 LLM 호출로 생성합니다.

---

## Search Persistence (Phase 19)

`search_persist_node` 가 그래프 종단부에서 `search_channels` 를 두 테이블에 일괄 적재합니다.

- `chat_search_queries` — 채널 1개당 1행. `query_text` + `parameters(JSONB)` 로 "무엇으로 검색했는가" 기록.
- `chat_search_results` — 채널 1개당 N행. `rank` + `service_id` + `score` + `meta(JSONB)` 로 "무엇이 반환됐는가" 기록.

두 테이블은 `(message_id, channel)` 키로 묶이고 `kind` 는 양쪽에 denormalize 됩니다(`sql`/`vector`/`bm25`/`rrf`/`map`/`final` CHECK 화이트리스트). 채널 추가는 마이그레이션 없이 가능합니다.

각 검색 노드는 `schemas.search.ChannelData(kind, query, hits)` 한 묶음으로 자기 채널을 채우고, 적재는 종단 노드가 단일 트랜잭션으로 처리합니다. 0건 결과여도 query 행은 기록됩니다 (recall 진단 신호). 운영 가이드 + 분석 쿼리 예시 6종은 `docs/chat-search-persistence.md` 참조.

---

## LangGraph 전환 (Phase 17 완료)

LangChain LCEL 기반 `workflow.py`에서 LangGraph `StateGraph` 기반 `graph.py`로 전환됐습니다.

| 항목 | 전환 전 (LCEL) | 전환 후 (LangGraph) |
|---|---|---|
| 진입 파일 | `workflow.py` | `graph.py` |
| 분기 | `_dispatch()` if/elif | `StateGraph` 조건부 엣지 |
| Self-Correction | 미지원 | 빈 answer → router 재진입 (최대 1회) |
| 상태 규약 | `AgentState` TypedDict | 동일 (`AgentState` 유지, `retry_count` 추가) |

`AgentState` 기반 입출력 규약을 유지하므로 각 에이전트 파일(`router_agent.py`, `sql_agent.py`, `vector_agent.py`, `answer_agent.py`)은 수정 없이 재사용됩니다. `workflow.py`는 레거시로 유지됩니다.
