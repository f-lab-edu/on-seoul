# agents 모듈

사용자 질문을 의도별로 분류하고 검색·답변 생성까지 처리하는 에이전트 모듈입니다.

**책임 범위**:
* LangGraph StateGraph 워크플로우 조립 및 실행 (`graph.py`, `nodes.py`)
* 참조 해소 — 규칙 기반 지시 참조 판정 (`_reference_resolution.py`)
* 행동(action) 결정 (`triage_agent.py`) + 검색 계획(retrieval_intent) (`router_agent.py`)
* 정형 데이터 조회 (`sql_agent.py`)
* 의미 기반 검색 — BM25 + vector 하이브리드 (`vector_agent.py`)
* 집계/분포 질의 (`analytics_agent.py`)
* 검색 결과 원본 hydration (`hydration_node.py`)
* 자연어 답변 + 시설 카드 생성 (`answer_agent.py`)

각 에이전트는 `AgentState`를 입력받아 필드를 채운 새 `AgentState`를 반환합니다. 상태 변이 없이 `{**state, key: value}` 스프레드 패턴을 사용합니다.

---

## 모듈 구조

```
agents/
├── graph.py                 # LangGraph StateGraph 조립·실행 (AgentGraph)
├── nodes.py                 # 노드·엣지 구현 (GraphNodes)
├── _reference_resolution.py # W1 지시 참조 규칙 (LLM 미사용)
├── triage_agent.py          # action 결정 (TriageAgent) — 무엇을 할지
├── router_agent.py          # 검색 계획 (RouterAgent) — retrieval_intent + 파라미터, RETRIEVE 경로 전용
├── sql_agent.py             # LLM 파라미터 추출 + 파라미터화 SQL 조회
├── vector_agent.py          # 질의 정제 + BM25/vector 하이브리드 검색 (RRF 결합)
├── analytics_agent.py       # 집계/분포 질의 (GROUP BY / DISTINCT)
├── hydration_node.py        # service_id → public_service_reservations 원본 hydration
├── answer_agent.py          # 검색 결과 → 자연어 답변 + 시설 카드 + 대화 제목
└── workflow.py              # LangChain(LCEL) 워크플로우 — graph.py 전환 전 레거시
```

---

## 실행 흐름

```
사용자 메시지
  └─ reference_resolution_node           # W1 규칙 기반 지시 참조 판정 (prev_entities 게이트)
       ├─ referential   → rehydrate_node (hydrate_services 재수화)
       │                    └─ describe_node (AnswerAgent.describe, 설명형 답변)
       │                         └─ search_persist_node → trace_node
       └─ non-referential → triage_node  # TriageAgent.classify() — action 결정
            ├─ RETRIEVE      → router_node → cache_check_node   # Router(검색 의도+파라미터) → Cache lookup
            │     ├─ hit  → search_persist_node (빈 채널 skip) → trace_node
            │     └─ miss → intent 분기
            │          ├─ SQL_SEARCH   → SqlAgent.search()     → on_data DB
            │          │                    └─ hydration_node → rrf_fusion_node → pre_answer_gate_node
            │          ├─ VECTOR_SEARCH → VectorAgent.search() → on_ai DB (BM25 + vector RRF)
            │          │                    └─ hydration_node → rrf_fusion_node → pre_answer_gate_node
            │          │                         ├─ 0건 → retry_prep_node → router_node 재진입
            │          │                         └─ 유건   → AnswerAgent.answer()
            │          ├─ MAP          → map_search()          → on_data DB (earthdistance) → answer
            │          └─ ANALYTICS    → AnalyticsAgent.run()  → on_data DB (집계) → answer
            ├─ DIRECT_ANSWER → direct_answer_node              # DB 없이 LLM 직접 응답
            ├─ AMBIGUOUS     → ambiguous_node                  # AnswerAgent.clarify — LLM 명확화 질문(history 반영)
            ├─ OUT_OF_SCOPE  → out_of_scope_node               # domain_outside 거절 / attribute_gap → vector
            └─ EXPLAIN       → explain_node                    # prev_reasoning 설명
                 └─ AnswerAgent.answer() / action 노드 답변
                      └─ Self-Correction 엣지     # RETRIEVE + 빈답변/0건 + retry=0 시 retry_prep → router 재진입 (최대 1회)
                           └─ cache_write_node     # Answer Cache 저장 (정상 결과만)
                                └─ search_persist_node # chat_search_queries + chat_search_results 일괄 적재 (best-effort)
                                     └─ trace_node     # chat_agent_traces 적재 (best-effort)
```

progress·decision SSE 이벤트는 **각 노드가 직접 emit** 한다 — `agents/_helpers.py`의 `get_stream_writer` 안전 래퍼(`emit_progress`/`emit_decision`)로 `{"_evt": ...}` custom 페이로드를 흘려보내고, `stream()`은 이를 `_evt` 타입으로만 분기해 SSE 튜플로 통과시킨다(`stream()`은 더 이상 node_name으로 단계를 역추론하지 않는다). `decision` 이벤트(`DecisionEvent`)는 전체 실행 1회 방출된다 — 비-RETRIEVE면 `triage_node`가 `routes=[]`로(`user_rationale` 있을 때만), RETRIEVE면 `router_node`가 검색 경로(`routes`)를 확정한 뒤 emit 한다. emit-once는 `AgentState`의 가드 슬롯(`decision_emitted`/`searching_emitted`/`answering_emitted`)으로 보장하며, `retry_prep_node`가 progress 가드(`searching_emitted`/`answering_emitted`)만 리셋해 재검색 시 다시 흐르게 한다(`decision_emitted`는 유지). `answering` progress는 검색 팬아웃(sql/vector)이 합류하는 단일 머지점 `hydration_node`에서 1회 emit 한다(map/analytics는 hydration을 거치지 않아 자체 emit).

검색 노드(sql/vector/map)는 `AgentState.search_channels: dict[str, ChannelData]` 에 채널별 입력(query)·출력(hits) 쌍을 채우고, 종단 `search_persist_node` 가 일괄 적재한다. `retry_prep_node` 가 재시도 시 `search_channels = RESET_CHANNELS` sentinel 을 보내 UNIQUE 위반을 방지한다 (빈 dict 는 더 이상 리셋이 아니라 no-op). 자세한 적재 정책은 `docs/chat-search-persistence.md` 참조.

## DB 세션 라우팅

DB를 쓰는 노드는 노드 내부에서 `data_session_ctx()` / `ai_session_ctx()`로 풀에서 세션을 잡고 즉시 반납합니다(acquire-use-release). `run()`/`stream()`은 세션을 주입받지 않습니다.

| 노드 / 작업 | 세션 | DB | 대상 테이블 |
|---|---|---|---|
| sql_node → `sql_search` | `data_session` | `on_data` | `public_service_reservations` |
| vector_node → `vector_search` / `bm25_search` / `question_search` | `ai_session` | `on_ai` | `service_embeddings` |
| hydration_node / rehydrate_node → `hydrate_services` | `data_session` | `on_data` | `public_service_reservations` |
| map_node → `map_search` | `data_session` | `on_data` | `public_service_reservations` (earthdistance) |
| analytics_node → `analytics_search` | `data_session` | `on_data` | `public_service_reservations` (GROUP BY / DISTINCT) |
| search_persist_node | `ai_session` | `on_ai` | `chat_search_queries`, `chat_search_results` |
| trace_node | `ai_session` | `on_ai` | `chat_agent_traces` |

> `search_persist_node`와 `trace_node`는 각자 독립 `ai_session`을 노드 내부에서 엽니다 — 서로 다른 테이블 INSERT이고 search_persist가 먼저 commit하므로 트랜잭션 공유 의존성이 없습니다.

---

## 공유 상태 — AgentState

에이전트 간 데이터는 `schemas.state.AgentState` (TypedDict)로 흐릅니다.

| 필드 | 타입 | 작성 주체 | 설명 |
|---|---|---|---|
| `room_id` | `int` | 호출자 | 대화 방 ID |
| `message_id` | `int` | 호출자 | 메시지 ID (trace 참조용) |
| `message` | `str` | 호출자 | 사용자 원본 질문 |
| `title_needed` | `bool` | 호출자 | 대화 제목 생성 필요 여부 (첫 메시지) |
| `user_lat`, `user_lng` | `float \| None` | 호출자 | MAP intent 용 위치 좌표 |
| `history` | `list[dict]` | 호출자 | 직전 N턴 대화 이력 |
| `prev_entities` | `list[dict] \| None` | 호출자(W1) | 직전 턴 결과 엔티티 `[{service_id, label}]`. 참조 해소 게이트 |
| `prev_intent`, `prev_reasoning` | `... \| None` | 호출자(W1) | 직전 턴 intent·판단 근거 (carryover) |
| `target_service_ids` | `list[str] \| None` | reference_resolution_node(W1) | referential 시 바인딩된 service_id. None=비참조 |
| `action` | `ActionType \| None` | TriageAgent | 행동 유형 5종 |
| `out_of_scope_type` | `str \| None` | TriageAgent | `domain_outside` / `attribute_gap` |
| `user_rationale` | `str \| None` | TriageAgent | 사용자 노출용 판단 근거. decision SSE에 포함 |
| `intent` | `IntentType \| None` | RouterAgent | RETRIEVE 경로 검색 의도, 비-RETRIEVE는 FALLBACK |
| `secondary_intent` | `IntentType \| None` | RouterAgent | SQL↔VECTOR 팬아웃용. `enable_secondary_intent=True`일 때 유효 |
| `forced_intent`, `retry_radius_m` | `... \| None` | retry_prep_node | 방향성 재시도 신호 |
| `refined_query` | `str \| None` | RouterAgent / VectorAgent | 벡터 검색용 정제 질의 (router 1차, 미산출 시 VectorAgent fallback) |
| `max_class_name`, `area_name`, `service_status`, `payment_type` | `str \| None` | RouterAgent | post-filter 메타데이터 |
| `sql_results` | `list[dict] \| None` | SqlAgent | SQL 조회 결과 |
| `sql_keyword` | `str \| None` | SqlAgent | SQL 키워드 (search_persist 의 sql 채널 query_text) |
| `vector_results` | `list[dict] \| None` | VectorAgent | 유사도 검색 결과 (메타데이터·service_id·rrf_score) |
| `rrf_merged_ids` | `list[str] \| None` | rrf_fusion_node(W2) | 팬아웃 RRF 통합 service_id 순서 |
| `hydrated_services` | `list[dict] \| None` | hydration_node / rehydrate_node | service_id → 최신 원본 |
| `service_cards` | `list[dict] \| None` | answer_node / describe_node | 카드 UI용 상위 N건 |
| `map_results` | `dict \| None` | map_node | 반경 검색 GeoJSON |
| `analytics_results`, `analytics_group_by`, `analytics_metric`, `analytics_keyword` | `... \| None` | analytics_node | 집계 결과·차원·방식·키워드 |
| `search_channels` | `dict[str, ChannelData]` | sql/vector/map_node, retry_prep_node | 채널별 입력(query)+출력(hits). reducer 누적, 명시적 리셋은 `RESET_CHANNELS` sentinel (빈 dict 는 no-op). `search_persist_node` 가 일괄 적재 |
| `node_path` | `list[str]` | 각 노드 | 실행 경로 누적 (reducer append) |
| `started_at` | `float \| None` | 호출자(prepare) | 실행 시작 시각 (elapsed_ms 산출용) |
| `answer` | `str \| None` | AnswerAgent / action 노드 | 최종 자연어 답변 |
| `title` | `str \| None` | AnswerAgent | 대화 제목 (`title_needed=True`일 때) |
| `cache_hit` | `bool` | cache_check_node | Answer Cache hit 여부 |
| `trace` | `dict \| None` | trace_node | 실행 메타데이터 (intent, node_path, elapsed_ms) |
| `error` | `str \| None` | 각 노드 | 오류 메시지 |
| `retry_count` | `int` | retry_prep_node | 자기 교정 재시도 횟수 (0 = 아직 재시도 없음, 최대 1) |
| `retry_relaxed` | `bool` | retry_prep_node | 0건 완화 재시도 신호 |
| `decision_emitted` | `bool` | triage/router_node | decision SSE emit-once 가드. 전체 실행 1회(재시도에도 유지) |
| `searching_emitted`, `answering_emitted` | `bool` | router/검색/hydration_node | progress SSE emit-once 가드(단계별 1회). `retry_prep_node`가 리셋해 재검색 시 다시 흐름 |

---

## 주요 컴포넌트

### graph.py — LangGraph 워크플로우

`AgentGraph.run(state)` 한 번 호출로 전체 파이프라인을 실행합니다. DB 세션은 인자로 받지 않고, 각 노드가 실행 시점에 컨텍스트(`ai_session_ctx`/`data_session_ctx`)에서 자체 획득합니다.

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
)
# result["answer"], result["title"], result["trace"] 사용
```

각 에이전트는 생성자 주입으로 교체할 수 있어 테스트에서 Mock으로 대체합니다.

```python
graph = AgentGraph(router=mock_router, sql_agent=mock_sql)
```

**스트리밍**: `stream()`은 `astream(stream_mode=["values","custom"])`으로 실행합니다. `"values"`는 LangGraph가 reducer(`node_path`/`search_channels` 등)를 적용한 전체 state 스냅샷으로, 가장 최근 값을 최종 `("result", state)`로 yield 합니다(수동 `accumulated.update` 누적 없음 — reducer 정합성 보존). `"custom"`은 노드가 `get_stream_writer`로 보낸 progress/decision 페이로드로, `_evt` 타입에 따라 그대로 SSE 튜플로 통과시킵니다.

**오류 처리**: `triage_node` 예외 시 fallback answer를 state에 주입하고 `action=DIRECT_ANSWER`로 설정하여 Self-Correction 없이 종단 체인(cache_write → search_persist → trace)으로 진행합니다. trace / search_persist 적재는 모두 best-effort로 실행되어 저장 실패가 워크플로우 결과에 영향을 주지 않습니다.

**Self-Correction**: RETRIEVE action에서 answer가 비어 있거나 검색 0건이고 `retry_count == 0`이면 `retry_prep_node` 를 거쳐 `router_node`로 재진입해 재검색을 시도합니다(triage는 거치지 않음 — action은 이미 RETRIEVE 확정. 비-RETRIEVE action은 제외). 0건 게이트(`pre_answer_gate_node`)는 hydrated 0건 시 answer LLM 호출 없이 곧장 재시도로 보냅니다. `retry_prep_node` 는 `retry_count` 증가와 함께 `search_channels = RESET_CHANNELS` sentinel 을 보내 이전 시도의 채널 데이터를 비웁니다. 최대 1회로 제한됩니다 (`recursion_limit=28`).

---

### 조건부 엣지 (상태 → 제어)

LangGraph는 데이터(상태)와 제어(엣지)를 분리합니다. 노드는 `AgentState`를 읽어 부분 업데이트를 반환할 뿐 다음 노드를 직접 지목하지 않습니다. 전이는 무조건 엣지(`add_edge`)와 조건부 엣지(`add_conditional_edges`)로 선언되며, 조건부 엣지의 분기 함수는 state만 읽는 순수 함수입니다. 현재 조건부 엣지는 6개입니다.

| source | 분기 함수 | 제어 신호 | 분기 |
|---|---|---|---|
| `reference_resolution_node` | `route_after_reference` | `target_service_ids` | referential → `rehydrate_node`. 비참조 → `triage_node`. |
| `triage_node` | `route_by_action` | `action`, `error`, `answer` | `RETRIEVE` → `router_node`(→ `cache_check_node`). 그 외 action → 동명 노드. error+answer → `answer_node`. |
| `out_of_scope_node` | `_out_of_scope_route` | `out_of_scope_type` | `attribute_gap` → `vector_node`. `domain_outside` → `search_persist_node`. |
| `cache_check_node` | `post_cache_check` | `cache_hit`, `intent` | hit → `search_persist_node`. miss → `intent`로 sql/vector/map/analytics 분기. |
| `pre_answer_gate_node` | `route_pre_answer_gate` | `action`, `hydrated_services`, `retry_count` | hydrated 0건(retry=0) → `retry_prep_node`. 그 외 → `answer_node`. |
| `answer_node` | `self_correction_edge` | `action`, `retry_count`, `answer`, `*_results` | 재시도 필요 시 `retry_prep_node`, 아니면 `cache_write_node`. |

노드 함수와 분기 함수는 `GraphNodes`의 **바운드 메서드**로 `_build_graph(nodes)`에서 그래프에 직접 등록됩니다(`builder.add_node(..., nodes.xxx)`). `_out_of_scope_route`만 graph.py 모듈 수준 순수 함수입니다. `GraphNodes`는 무상태 싱글톤이고 `AgentGraph`를 역참조하지 않으므로 순환 참조가 없으며, `_dispatch_*` 우회 함수나 `_ACTIVE_NODES` ContextVar 같은 회피 계층은 없습니다. 그래프는 `AgentGraph.__init__`에서 인스턴스 단위로 1회 컴파일됩니다(`self._compiled_graph = _build_graph(self._nodes)` — 컴파일 비용이 저렴해 클래스 수준 캐시는 두지 않습니다).

---

### Self-Correction 방향성 재시도

검색 0건이거나 답변이 비면 **1회만** 다시 시도합니다. 트리거는 위에서부터 먼저 매칭되는 하나만 적용합니다: ① 비-RETRIEVE action → 종료, ② `retry_count != 0` → 종료(1회 소진), ③ 빈 답변 → 재시도, ④ intent별 0건 → 재시도. 재시도는 단순 "조건 완화"가 아니라 방향성 전환에 가깝습니다.

| 원 intent | 동작 | 다음 intent |
|---|---|---|
| `SQL_SEARCH` | 강제 전환 (`forced_intent` 주입 + 정형 필터 비움) | `VECTOR_SEARCH` |
| `VECTOR_SEARCH` | 완화 재분류 (`refined_query`·필터 리셋) | (재분류) |
| `ANALYTICS` | 제약 큰 필터 1개 드롭 (status→area, `max_class_name` 유지) | `ANALYTICS` |
| `MAP` | 반경 확장 (`retry_radius_m=3000`) | `MAP` |

빈 검색 결과로 답변 LLM을 낭비하지 않도록, 검색 직후 `pre_answer_gate_node`가 hydrated 0건이면 답변 생성 전에 곧장 재시도로 보냅니다(0건 게이트). `forced_intent`는 `router_node`가 honor 후 즉시 None으로 소비(1회성)하므로 무한 전환이 없고, 재시도는 `retry_count` 캡으로 최대 1회(`recursion_limit=28`)입니다. 재시도 시 `retry_relaxed=True`로 `AnswerAgent`가 완화 사실을 답변에 명시합니다.

---

### triage_agent.py — 행동(action) 결정

`TriageAgent`는 LCEL `llm.with_structured_output(TriageOutput)`으로 사용자 메시지의 **action**을 분류합니다: `RETRIEVE` / `DIRECT_ANSWER` / `AMBIGUOUS` / `OUT_OF_SCOPE` / `EXPLAIN`. 함께 `out_of_scope_type`(domain_outside/attribute_gap)·`user_rationale`·`reasoning`을 산출합니다. 검색 방식·필터는 다루지 않습니다(→ RouterAgent).

`RETRIEVE`면 `route_by_action`이 `router_node`로 보내고, 나머지 4종은 검색 없이 각 action 노드에서 답변합니다. 비-RETRIEVE에서는 `intent`가 `FALLBACK`으로 남아 다운스트림이 대화형 분기를 탑니다.

---

### router_agent.py — 검색 계획 (retrieval_intent)

`RouterAgent`는 action=RETRIEVE일 때만 호출되어 어떤 검색으로 어떻게 찾을지 계획합니다. 한 LLM 호출로 다음을 산출합니다.

- **retrieval_intent**(`intent`) — `IntentType` 4종
- **refined_query** — 벡터 검색용 정제 질의
- **post-filter** — `max_class_name`·`area_name`·`service_status`·`payment_type` (화이트리스트 밖 값은 None 정규화)
- **vector_sub_intent** — identification / detail / semantic (VECTOR 전용)
- **secondary_intent** — SQL↔VECTOR 경계 모호 시 팬아웃용 (SQL/VECTOR만 허용, `enable_secondary_intent` 플래그)

재시도(`retry_prep_node`)와 `forced_intent` honor가 모두 `router_node`로 재진입합니다 — action은 이미 확정됐고 검색 *계획*만 다시 세우면 되기 때문입니다.

| IntentType | 분류 기준 | 예시 |
|---|---|---|
| `SQL_SEARCH` | 카테고리·자치구·접수 상태·날짜 등 정형 조건 | "지금 접수 중인 수영장" |
| `VECTOR_SEARCH` | 키워드·의미 기반 유사 시설 탐색 | "아이랑 체험할 수 있는 곳" |
| `MAP` | 지도·위치·반경 탐색 | "내 주변 500m 이내 체육관" |
| `ANALYTICS` | 개수·분포·종류 등 집계/요약 질의 | "강남구에 체육시설이 몇 개야?" |

---

### _reference_resolution.py — 지시 참조 해소 (W1)

`reference_resolution_node`가 START 직후 `resolve_reference(message, prev_entities)`로 현재 메시지가 직전 턴 시설을 가리키는 지시 참조인지 규칙 기반(LLM 미사용)으로 판정합니다. 신호 3종: 지시대명사("이곳/방금/해당"), 서수(한글 "첫번째"~"열번째" + 아라비아 "3번째"), 직전 라벨 부분일치. `prev_entities`가 비어 있으면 무조건 non-referential(하위호환). referential 시 `target_service_ids`를 바인딩하고 `rehydrate_node`(hydrate_services 재수화) → `describe_node`(설명형 답변)로 검색을 우회합니다.

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

조회에는 `tools.vector_search.vector_search()`를 사용합니다. `vector_search`는 post-filter 전략을 사용합니다 — 전체 임베딩에서 유사도 상위 `scan_k`를 먼저 뽑고, 서브쿼리 외부에서 `max_class_name`·`area_name`·`service_status` 필터를 적용합니다

---

### answer_agent.py — 답변 생성

`sql_results` / `vector_results` / `map_results`를 단일 목록으로 합쳐 LLM에 전달하고, 자연어 답변과 카드 데이터를 생성합니다.

**시설 카드 필드**: `service_id`, `service_name`, `area_name`, `place_name`, `service_status`, `receipt_start_dt`, `receipt_end_dt`, `service_url`

- `service_url` 이 없으면 `https://yeyak.seoul.go.kr` 으로 fallback합니다.
- `vector_results`의 `metadata` JSONB가 중첩된 경우 자동으로 언팩합니다.
- `title_needed=True` 이면 대화 제목(10자 이내)을 별도 LLM 호출로 생성합니다.

---

## Search Persistence 

`search_persist_node` 가 그래프 종단부에서 `search_channels` 를 두 테이블에 일괄 적재합니다.

- `chat_search_queries` — 채널 1개당 1행. `query_text` + `parameters(JSONB)` 로 "무엇으로 검색했는가" 기록.
- `chat_search_results` — 채널 1개당 N행. `rank` + `service_id` + `score` + `meta(JSONB)` 로 "무엇이 반환됐는가" 기록.

두 테이블은 `(message_id, channel)` 키로 묶이고 `kind` 는 양쪽에 denormalize 됩니다(`sql`/`vector`/`bm25`/`rrf`/`map`/`final` CHECK 화이트리스트). 채널 추가는 마이그레이션 없이 가능합니다.

각 검색 노드는 `schemas.search.ChannelData(kind, query, hits)` 한 묶음으로 자기 채널을 채우고, 적재는 종단 노드가 단일 트랜잭션으로 처리합니다. 0건 결과여도 query 행은 기록됩니다 (recall 진단 신호). 운영 가이드 + 분석 쿼리 예시 6종은 `docs/chat-search-persistence.md` 참조.

---

## LangGraph 전환
LangChain LCEL 기반 `workflow.py`에서 LangGraph `StateGraph` 기반 `graph.py`로 전환됐습니다.

| 항목 | 전환 전 (LCEL) | 전환 후 (LangGraph) |
|---|---|---|
| 진입 파일 | `workflow.py` | `graph.py` |
| 분기 | `_dispatch()` if/elif | `StateGraph` 조건부 엣지 |
| Self-Correction | 미지원 | 빈 answer/0건 → router 재진입 (최대 1회) |
| 상태 규약 | `AgentState` TypedDict | 동일 (`AgentState` 유지, `retry_count` 추가) |

`AgentState` 기반 입출력 규약을 유지하므로 각 에이전트 파일(`triage_agent.py`, `sql_agent.py`, `vector_agent.py`, `answer_agent.py`, `analytics_agent.py`)은 수정 없이 재사용됩니다. `workflow.py`는 레거시로 유지됩니다.
