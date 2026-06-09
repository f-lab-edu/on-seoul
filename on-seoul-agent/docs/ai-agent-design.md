# AI 에이전트 설계

> 이 페이지는 사용자 질문이 어떤 과정을 거쳐 응답을 생성하는지, on-seoul-agent의 에이전트/도구/그래프 구조를 중심으로 설명한다.

---

## 1. 개요

`on-seoul-agent`는 사용자의 자연어 질문을 받아, 의도를 분류하고 적절한 검색 도구를 호출한 뒤, 자연어 답변과 시설 카드를 생성하는 **멀티 에이전트** 서비스이다. LangGraph `StateGraph` 를 기반으로 노드와 조건부 엣지로 조립된다.

| 구성 요소 | 위치 | 역할 |
|---|---|---|
| **에이전트** (Agent) | `agents/` | 트리아지(행동·의도 결정), 파라미터 추출, 답변 생성 |
| **도구** (Tool) | `tools/` | DB 조회 추상화 (SQL / 벡터 / BM25 / 질문 / 지도 / 집계 / hydration) |
| **그래프** | `agents/graph.py` | LangGraph `StateGraph` 노드·엣지 조립 및 실행 |

> **W1/W2/W3 반영(2026-06-06)**: 단일 `Router Agent`가 결정하던 라우팅이 `Triage Agent`로 확장되어 **action(행동) × retrieval_intent(검색 방식)** 2축으로 분리되었다. START 직후 규칙 기반 **참조 해소(reference resolution)** 게이트가 추가되어 직전 턴 시설을 가리키는 지시 참조를 우회 경로(rehydrate → describe)로 처리한다. triage 완료 직후에는 판단 근거를 담은 `decision` SSE 이벤트를 1회 방출한다.

---

## 2. 전체 흐름

```mermaid
flowchart TD
    START(["사용자 메시지"])
    REF["reference_resolution_node<br/>(W1 규칙 기반 지시 참조 판정)"]
    REHYDRATE["rehydrate_node<br/>(target_service_ids<br/>hydrate_services 재수화)"]
    DESCRIBE["describe_node<br/>(AnswerAgent.describe<br/>설명형 답변)"]
    TRIAGE["triage_node<br/>(Triage Agent)<br/>action × retrieval_intent"]
    DIRECT["direct_answer_node"]
    AMBIG["ambiguous_node<br/>(명확화 질문)"]
    OOS["out_of_scope_node<br/>(domain_outside /<br/>attribute_gap)"]
    EXPLAIN["explain_node<br/>(prev_reasoning 설명)"]
    CACHE_CHECK["cache_check_node<br/>(Answer Cache lookup)"]
    SQL["sql_node<br/>(SQL Agent)"]
    VECTOR["vector_node<br/>(Vector Agent)<br/>BM25 + vector RRF"]
    MAP["map_node<br/>(map_search)<br/>lat/lng 미제공 시<br/>map_results=None"]
    ANALYTICS["analytics_node<br/>(Analytics Agent)<br/>analytics_search 집계"]
    HYDRATION["hydration_node<br/>(service_id → 원본 hydration)"]
    RRF["rrf_fusion_node<br/>(secondary_intent 팬아웃<br/>RRF 통합, 기본 bypass)"]
    GATE{{"pre_answer_gate_node<br/>hydrated 0건?(C2)<br/>(retry=0 캡)"}}
    ANSWER["answer_node<br/>(Answer Agent)"]
    SELF_CORR{{"self_correction<br/>빈 답변/intent별 0건?<br/>(retry=0 캡, 비-RETRIEVE 제외)"}}
    RETRY_PREP["retry_prep_node<br/>(intent별 분기:<br/>forced_intent 전환 /<br/>ANALYTICS 필터 드롭 /<br/>MAP 반경 확장 ·<br/>search_channels 리셋)"]
    CACHE_WRITE["cache_write_node<br/>(Answer Cache 저장)"]
    SEARCH_PERSIST(["search_persist_node<br/>chat_search_queries +<br/>chat_search_results 적재"])
    TRACE(["trace_node<br/>chat_agent_traces 적재"])

    START --> REF
    REF -- "referential" --> REHYDRATE
    REF -- "non-referential" --> TRIAGE
    REHYDRATE --> DESCRIBE
    DESCRIBE --> SEARCH_PERSIST

    TRIAGE -- "RETRIEVE" --> CACHE_CHECK
    TRIAGE -- "DIRECT_ANSWER" --> DIRECT
    TRIAGE -- "AMBIGUOUS" --> AMBIG
    TRIAGE -- "OUT_OF_SCOPE" --> OOS
    TRIAGE -- "EXPLAIN" --> EXPLAIN

    OOS -- "attribute_gap" --> VECTOR
    OOS -- "domain_outside" --> SEARCH_PERSIST

    CACHE_CHECK -- "hit" --> SEARCH_PERSIST
    CACHE_CHECK -- "miss · SQL_SEARCH" --> SQL
    CACHE_CHECK -- "miss · VECTOR_SEARCH" --> VECTOR
    CACHE_CHECK -- "miss · MAP" --> MAP
    CACHE_CHECK -- "miss · ANALYTICS" --> ANALYTICS
    CACHE_CHECK -- "miss · FALLBACK 또는 triage 예외" --> ANSWER

    SQL --> HYDRATION
    VECTOR --> HYDRATION
    HYDRATION --> RRF
    RRF --> GATE
    GATE -- "유건" --> ANSWER
    GATE -- "0건(C2)" --> RETRY_PREP
    MAP --> ANSWER
    ANALYTICS --> ANSWER

    DIRECT --> CACHE_WRITE
    AMBIG --> CACHE_WRITE
    EXPLAIN --> CACHE_WRITE

    ANSWER --> SELF_CORR
    SELF_CORR -- "Yes (최대 1회)" --> RETRY_PREP
    RETRY_PREP --> TRIAGE
    SELF_CORR -- "No" --> CACHE_WRITE
    CACHE_WRITE --> SEARCH_PERSIST
    SEARCH_PERSIST --> TRACE
```

각 노드는 공유 상태인 **`AgentState`** 를 입력받아 부분 업데이트 dict를 반환한다. LangGraph가 상태 병합을 담당하므로 노드 내부에서 직접 변이하지 않는다. 그래프 전체에는 super-step을 22로 제한(`recursion_limit=22`)하고 재시도는 1회 캡(`retry_count==0`)을 둬 무한 사이클을 방지한다. W2 최악 경로(RETRIEVE + 1회 재시도)는 18 super-step이며 여유 4를 더해 22로 설정한다.

> **참조 해소 경로(W1)**: `reference_resolution_node`가 `prev_entities`를 근거로 현재 메시지가 직전 턴 시설을 가리키는 지시 참조인지 규칙 기반(LLM 미사용)으로 판정한다. referential이면 `rehydrate_node → describe_node`로 검색 경로를 우회하고, 비참조이면 `triage_node`로 진행한다(기존 흐름, 하위호환).

> **2축 분리(W2)**: `triage_node`는 `action`(RETRIEVE / DIRECT_ANSWER / AMBIGUOUS / OUT_OF_SCOPE / EXPLAIN)과 `retrieval_intent`(RETRIEVE일 때만)를 직교 산출한다. RETRIEVE만 기존 검색 흐름으로 진입하고, 나머지 4종은 각자의 action 노드에서 검색 없이 답변을 생성한다. `OUT_OF_SCOPE/attribute_gap`만 예외적으로 `vector_node`로 합류하여 시설 식별 검색을 수행한다.

> **종단 체인 일관성**: cache hit·참조 해소·비-RETRIEVE action 경로 모두 `search_persist_node`를 경유한다. 빈 `search_channels`에서는 즉시 skip되므로 오버헤드는 없으나, 명시적으로 통과시켜 "cache_write → search_persist → trace"의 종단 체인 형태를 항상 동일하게 유지한다.

---

## 3. 에이전트 (Agents)

하나의 LLM에게 분류·조회·답변을 한꺼번에 맡기면 제어·테스트·관측이 모두 어려워진다. 그래서 on-seoul-agent는 역할이 다른 에이전트를 파이프라인으로 잇는다 — **무엇을 할지 정하는** Triage, **데이터를 가져오는** 검색 전문가(SQL·Vector·Analytics·Map), 그리고 **답변을 쓰는** Answer. 각 에이전트는 생성자 주입으로 독립적으로 교체·테스트할 수 있고, 공유 상태 `AgentState`(§5)만 주고받는다.

이 구성을 관통하는 한 가지 원칙이 있다. **LLM은 SQL을 직접 쓰거나 DB를 직접 만지지 않는다.** LLM은 자연어에서 필터·파라미터만 구조화 출력으로 뽑아내고, 실제 조회는 파라미터화된 도구(§4)가 수행한다. 덕분에 SQL Injection 위험이 없고, LLM 출력이 흔들려도 쿼리의 형태는 고정된다.

### 3-1. Triage Agent — 무엇을 할지 정한다 (2축 분리, W2)

`TriageAgent`(`agents/triage_agent.py`)는 사용자 메시지를 받아 다음 단계를 결정하는 첫 관문이다.

초기 설계는 의도 하나(`SQL_SEARCH`/`VECTOR_SEARCH` 등)만 분류하는 `RouterAgent`였다. 그런데 "안녕", "넌 뭘 할 수 있어?", "방금 그건 왜 추천했어?", "서울 인구 알려줘"(서비스 범위 밖) 같은 질문까지 전부 검색 경로로 흘러 어색한 답이 나왔다. 근본 원인은 **"검색을 할지 말지"와 "검색이라면 어떤 검색인지"가 서로 다른 차원**인데 이를 한 축에 뭉뚱그렸다는 점이다.

W2에서 이를 **2축**으로 분리했다.

- **action 축** — 무엇을 할지: `RETRIEVE`(검색한다), `DIRECT_ANSWER`(바로 답한다), `AMBIGUOUS`(되묻는다), `OUT_OF_SCOPE`(범위 밖), `EXPLAIN`(직전 판단 근거를 설명한다).
- **retrieval_intent 축** — `RETRIEVE`일 때만, 어떤 검색인지: `SQL_SEARCH` / `VECTOR_SEARCH` / `MAP` / `ANALYTICS`.

`RETRIEVE`만 검색 흐름으로 들어가고, 나머지 4종은 검색 없이 곧바로 답한다(아래 "action — 비검색 응답"). 기존 코드는 여전히 단일 `intent` 필드를 읽으므로, action=RETRIEVE이면 `intent=primary_intent`, 그 외에는 `intent=FALLBACK`으로 자동 동기화해 하위호환을 지킨다.

#### retrieval_intent — 검색 의도(IntentType) 분류 기준

action=RETRIEVE일 때 어떤 검색으로 보낼지 고르는 4종이다. 비-RETRIEVE action은 `intent=FALLBACK`으로 동기화된다.

| IntentType | 분류 기준 | 예시 |
|---|---|---|
| `SQL_SEARCH` | 카테고리·자치구·접수 상태·날짜 등 정형 조건 | "지금 접수 중인 수영장" |
| `VECTOR_SEARCH` | 키워드·의미 기반 유사 시설 탐색 | "아이랑 체험할 수 있는 곳" |
| `MAP` | 지도·위치·반경 탐색 | "내 주변 500m 이내 체육관" |
| `ANALYTICS` | 개수·분포·종류 등 집계 질의 | "강남구에 체육시설이 몇 개야?" |
| `FALLBACK` | 위 검색에 해당하지 않음 (비-RETRIEVE action의 동기화 값) | "어떤 서비스를 제공하나요?" |

#### action — 비검색 응답 노드 + decision 이벤트 (W3)

RETRIEVE를 제외한 4종은 `triage_node` 직후 `route_by_action` 분기로 각자의 노드에 도달한다. 모두 검색 없이 답변을 만들고 종단 체인으로 진행한다.

| action | 노드 | 동작 |
|---|---|---|
| `DIRECT_ANSWER` | `direct_answer_node` | DB 없이 바로 답변. 기존 FALLBACK 안내문을 대체한다 |
| `AMBIGUOUS` | `ambiguous_node` | 무엇을 찾는지 되묻는 명확화 질문 1개를 만든다 |
| `OUT_OF_SCOPE` | `out_of_scope_node` | 범위 밖(`domain_outside`)이면 즉시 거절한다. 단, 시설은 맞는데 속성만 모자란 경우(`attribute_gap`)는 시설 식별을 위해 `vector_node`로 합류한다 |
| `EXPLAIN` | `explain_node` | 직전 턴의 판단 근거(`prev_reasoning`)를 설명한다. 근거가 없으면 `DIRECT_ANSWER`로 폴백한다 |

**decision 이벤트 (W3)**: Triage가 실제로 LLM 분류를 수행하면, `stream()`이 triage 직후 `decision` SSE 이벤트(`schemas/events.py`의 `DecisionEvent`)를 한 번 방출한다. "왜 이렇게 판단했는지"를 사용자에게 투명하게 보여주기 위한 것으로, 결정된 action·검색 경로(`routes`)·사용자 노출용 근거 한 문장(`user_rationale`)을 담는다. 근거 문장은 내부 식별자가 새지 않도록 정제(`sanitize_user_rationale`)하며, `forced_intent` 재시도나 레거시 경로처럼 LLM 분류를 건너뛴 경우에는 방출하지 않는다.

> **참고사항**
> - 추출한 필터(`area_name`·`service_status`·`payment_type` 등)는 도메인 화이트리스트 밖 값이면 `None`으로 정규화한다 — 잘못된 값이 캐시 키를 오염시키거나 빈 검색을 만들지 않도록.
> - 재시도 경로에서 `forced_intent`가 들어오면 LLM 재분류를 건너뛰고 그 의도로 한 번만 검색한다(1회성 소비, §7-2).
> - triage 내부에서 예외가 나면 사용자에게 안내 메시지를 답으로 주고 종료한다(무한 루프 방지).
> - 프로덕션에서는 `AgentGraph()`가 항상 `TriageAgent`를 주입한다. 레거시 `RouterAgent`는 명시 주입 시 하위호환 경로로만 쓰인다.
> - **참조 해소 (W1)**: "두 번째 거 알려줘"처럼 직전 턴 결과를 가리키는 후속 질문은 Triage *이전* 단계인 `reference_resolution_node`가 규칙 기반(LLM 미사용)으로 가려낸다. 이 경우 검색을 건너뛰고 직전 시설을 최신 원본으로 다시 읽어(`rehydrate_node`) 설명형 답변(`describe_node`)을 만든다. 직전 카드(`prev_entities`)가 없으면 기존 흐름과 동일하다.

### 3-2. SQL Agent — 정형 조건 조회

"마포구에서 지금 접수 중인 수영장"처럼 카테고리·자치구·상태·키워드로 또렷이 걸러지는 질의를 담당한다. LLM은 메시지에서 필터 값만 뽑고, 실제 조회는 `sql_search` 도구가 파라미터화 SQL로 수행한다. 결과는 개별 시설 목록이다.

### 3-3. Vector Agent — 의미 기반 검색 (4채널 하이브리드)

"아이랑 체험할 수 있는 곳"처럼 정형 필터로는 잡히지 않는 **의미 중심 질의**를 담당한다.

의미 검색을 잘하려면 한 가지 방식만으로는 부족하다. 그래서 서로 다른 관점의 **4개 채널**을 함께 돌리고 결과를 합친다.

- **Track A (identity)** — 시설 신원 임베딩. 자치구·상태 등 post-filter를 적용한다.
- **Track B (summary)** — 자연어 요약 임베딩.
- **Track C (question)** — "이 시설로 답할 만한 예상 질문" 임베딩.
- **BM25** — 정확 키워드 매칭(전문 검색). 한국어는 `tools/tokenizer.py`가 Kiwi로 의미 형태소만 추려 검색어로 쓴다. 토크나이저가 두 계층(쿼리 전처리 Kiwi + 색인 매칭 `korean_lindera`)으로 나뉜 배경은 [하이브리드 검색 전략](hybrid-search-strategy.md)을 참조한다.

채널마다 점수 척도가 달라 단순 합산이 어렵다. 그래서 각 채널의 *순위*만 모아 통합하는 **RRF(Reciprocal Rank Fusion)**를 쓴다(`core/rrf.py`). asyncpg 단일 세션 제약 때문에 4채널은 순차로 실행한다.

> **원본 재조회 분리 (W2)**: 검색은 임베딩에 붙은 메타데이터를 쓰는데, 이 값(상태·접수일 등)은 시간이 지나면 실제와 어긋난다(stale). 그래서 검색이 끝나면 별도 `hydration_node`가 `service_id`로 원본 테이블을 다시 읽어 `hydrated_services`에 채운다. Answer Agent는 검색 경로와 무관하게 이 단일 슬롯만 본다 — SQL 경로는 이미 원본 행이라 그대로 통과한다.

### 3-4. Analytics Agent — 집계·분포 질의

"강남구에 체육시설이 몇 개야?"처럼 개수·분포·종류를 묻는 집계 질의를 담당한다. 개별 목록을 주는 SQL_SEARCH와 구별되며, 집계 값만 필요하므로 원본 재조회(hydration)는 거치지 않는다. 여기서도 LLM은 집계 파라미터만 뽑고 `analytics_search`가 GROUP BY/DISTINCT를 실행한다.

### 3-5. Answer Agent — 답변 생성

모든 검색 경로의 결과는 마지막에 `AnswerAgent` 하나로 모여 자연어 답변과 시설 카드가 된다. 답변·카드 포맷을 한곳에서 관리해 일관성을 유지하기 위해서다.

핵심 규칙은 두 가지다. 시설에 `service_url`이 없으면 서울 예약 포털(`https://yeyak.seoul.go.kr`)로 fallback하고, 입력에 이미 `error`와 `answer`가 모두 채워져 있으면(triage 예외 등) LLM을 다시 부르지 않고 즉시 반환한다. 성능을 위해 정적인 프롬프트(MAP/ANALYTICS/FALLBACK)는 시작 시 한 번만 조립해 캐시하고, 내용이 매번 달라지는 카드형(SQL/VECTOR) 프롬프트만 호출 시점에 조건부로 조합한다.

---

## 4. 도구 (Tools)

에이전트는 SQL을 직접 작성하거나 벡터 연산을 직접 다루지 않는다. DB 조회는 아래 도구로 위임된다: `sql_search`, `vector_search`, `bm25_search`, `question_search`, `hydrate_services`, `map_search`, `analytics_search`. 토큰화 유틸은 `tools/tokenizer.py`(W1/W2가 신규 도구를 추가하진 않음).

### 4-1. `sql_search` — 정형 필터 조회

`on_data.public_service_reservations` 테이블을 파라미터화 SQL로 조회한다. 모든 필터 값은 bind 파라미터로 전달하므로 SQL Injection 위험이 없다.

| 파라미터 | 설명 |
|---|---|
| `max_class_name` | 대분류 카테고리 (체육시설·문화체험·공간시설·교육강좌·진료복지) |
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

`service_embeddings` 테이블의 ParadeDB BM25 인덱스를 사용해 한국어 형태소 기반 키워드 매칭을 수행한다. 토큰 배열은 Python 레이어(`tools/tokenizer.py`)에서 Kiwi(kiwipiepy)로 사전 분해한 결과를 사용한다.

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
| `top_k` | 최대 반환 건수 (기본값: 25) |

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
| `history` | 호출자 | 직전 N턴 대화 이력. triage가 맥락으로 활용 |
| `prev_entities` | 호출자(W1) | 직전 턴 결과 엔티티 `[{service_id, label}]`. reference_resolution_node가 지시 참조 바인딩에 사용. 미전송 시 `[]` → 항상 non-referential |
| `prev_intent` | 호출자(W1) | 직전 턴 분류 intent (carryover 슬롯) |
| `prev_reasoning` | 호출자(W1) | 직전 턴 판단 근거. triage의 EXPLAIN 판정 + explain_node가 소비 |
| `target_service_ids` | reference_resolution_node(W1) | referential 판정 시 바인딩된 service_id 리스트. None([])=비참조(기존 흐름) |
| `intent` | triage_node | 분류된 의도 (action=RETRIEVE면 primary_intent, 그 외 FALLBACK) |
| `action` | triage_node(W2) | 행동 유형 5종 (RETRIEVE/DIRECT_ANSWER/AMBIGUOUS/OUT_OF_SCOPE/EXPLAIN) |
| `secondary_intent` | triage_node(W2) | SQL↔VECTOR 경계 모호 시 팬아웃용. None=단일 라우트. `enable_secondary_intent=True`일 때만 유효 |
| `out_of_scope_type` | triage_node(W2) | OUT_OF_SCOPE 서브타입 (`domain_outside` / `attribute_gap`) |
| `user_rationale` | triage_node(W2/W3) | 사용자 노출용 판단 근거 1문장. decision SSE 이벤트에 포함 |
| `forced_intent` | retry_prep_node | 방향성 재시도 시 다음 순회 intent 강제(예: SQL_SEARCH→VECTOR_SEARCH). triage_node 가 LLM 분류 skip 후 honor + 즉시 None 소비(1회성). None=일반 분류 |
| `retry_radius_m` | retry_prep_node | MAP 0건 재시도 시 확장 반경(m). map_node 가 기본 반경 대신 사용. None=기본 1000m |
| `vector_sub_intent` | triage_node | 벡터 검색 세부 의도 (`identification`/`detail`/`semantic`). VECTOR_SEARCH 전용 |
| `refined_query` | triage_node / vector_node | 벡터 검색용 정제 질의 (triage 1차 산출, 미산출 시 vector_node fallback) |
| `max_class_name`, `area_name`, `service_status`, `payment_type` | triage_node | post-filter 메타데이터 |
| `sql_results` | sql_node | SQL 조회 결과 |
| `sql_keyword` | sql_node | SqlAgent 가 LLM 으로 추출한 keyword (search_persist 의 sql 채널 query_text 로 사용) |
| `vector_results` | vector_node | BM25 + vector RRF 결합 결과 (메타데이터·service_id·rrf_score) |
| `rrf_merged_ids` | rrf_fusion_node(W2) | secondary_intent 팬아웃 결과를 RRF 통합한 service_id 순서. None=단일 라우트. hydration_node가 우선 참조 |
| `hydrated_services` | hydration_node / rehydrate_node | service_id → public_service_reservations 최신 원본. AnswerAgent/describe_node 가 사용 |
| `service_cards` | answer_node / describe_node | 카드 UI용 상위 N건 dict 리스트 |
| `map_results` | map_node | 반경 검색 GeoJSON |
| `analytics_results` | analytics_node | `analytics_search` 집계 결과 리스트 |
| `analytics_group_by` | analytics_node | LLM이 추출한 집계 차원 (area_name / max_class_name / min_class_name / service_status) |
| `analytics_metric` | analytics_node | LLM이 추출한 집계 방식 (`count` / `distinct`) |
| `analytics_keyword` | analytics_node | LLM이 추출한 키워드 필터 (없으면 None) |
| `search_channels` | sql/vector/map_node + retry_prep_node | `dict[str, ChannelData]` — 채널별 입력(query) + 출력(hits). reducer `search_channels_reducer` 로 누적, `RESET_CHANNELS` sentinel 로 리셋(빈 dict는 no-op) |
| `node_path` | 각 노드 | 실행 경로 누적. `node_path_reducer`가 부분 리스트를 append (재시도 포함) |
| `started_at` | 호출자(prepare) | 실행 시작 시각(time.monotonic). elapsed_ms 산출용 |
| `answer`, `title` | answer_node / 각 action 노드 | 최종 답변 / 대화 제목 |
| `cache_hit` | cache_check_node | Answer Cache hit 여부 |
| `trace` | trace_node | `intent`, `node_path`, `elapsed_ms` |
| `error` | 각 노드 | 오류 메시지 |
| `retry_count` | retry_prep_node | 자기 교정 재시도 횟수 (0=초기, 최대 1) |
| `retry_relaxed` | retry_prep_node | 0건 완화 재시도 신호. AnswerAgent가 완화 사실을 답변에 명시 |

---

## 6. DB 세션 라우팅

DB를 쓰는 노드는 노드 내부에서 `data_session_ctx()` / `ai_session_ctx()`로 풀에서 세션을 잡고 즉시 반납한다(acquire-use-release, 제안 0-6). `run()`/`stream()`은 세션을 주입받지 않는다.

| 노드 / 작업 | 세션 | DB | 대상 테이블 |
|---|---|---|---|
| sql_node → `sql_search` | `data_session` | `on_data` | `public_service_reservations` |
| vector_node → `vector_search` / `bm25_search` / `question_search` | `ai_session` | `on_ai` | `service_embeddings` |
| hydration_node → `hydrate_services` | `data_session` | `on_data` | `public_service_reservations` |
| rehydrate_node → `hydrate_services` (W1) | `data_session` | `on_data` | `public_service_reservations` |
| map_node → `map_search` | `data_session` | `on_data` | `public_service_reservations` (earthdistance) |
| analytics_node → `analytics_search` | `data_session` | `on_data` | `public_service_reservations` (GROUP BY / DISTINCT) |
| search_persist_node | `ai_session` | `on_ai` | `chat_search_queries`, `chat_search_results` |
| trace_node | `ai_session` | `on_ai` | `chat_agent_traces` |

`search_persist_node` 와 `trace_node` 는 각자 독립 `ai_session` 을 노드 내부에서 연다(서로 다른 테이블 INSERT이고 search_persist가 먼저 commit하므로 트랜잭션 공유 의존성이 없다 — 제안 0-6).

---

## 7. 그래프 실행

`AgentGraph.run(state)` 한 번 호출로 reference_resolution → triage → 분기 → answer → self-correction → 종단 체인 적재가 끝난다. `AgentGraph.stream(state)`은 동일 실행을 `(event_type, data)` 튜플 스트림(`progress` / `decision` / `result`)으로 반환하여 SSE 릴레이에 사용된다. DB 세션은 인자로 받지 않고 각 노드가 실행 시점에 컨텍스트에서 획득한다(제안 0-6).

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
)
```

각 에이전트는 생성자 주입으로 교체 가능하여 단위 테스트에서 Mock으로 대체한다(`AgentGraph(triage=..., sql_agent=...)`). `CompiledStateGraph`는 `ClassVar` 로 캐시되어 프로세스당 1회만 컴파일된다.

### 7-0. 상태 → 엣지 제어 메커니즘

LangGraph는 **데이터(상태)와 제어(엣지)를 분리**한다. 노드는 `AgentState`(§5)를 읽어 부분 업데이트 dict를 반환할 뿐 다음 노드를 직접 지목하지 않는다. 노드 간 전이는 그래프 빌드(`agents/graph.py`의 `_build_shared_graph`)에서 두 종류의 엣지로 선언된다.

- **무조건 엣지** `add_edge(source, target)` — 분기 없이 항상 다음 노드로 진행한다. (예: `rehydrate_node → describe_node`, `sql_node → hydration_node → rrf_fusion_node → pre_answer_gate_node`, `cache_write_node → search_persist_node → trace_node`.)
- **조건부 엣지** `add_conditional_edges(source, 분기함수, 매핑dict)` — 분기 함수가 `state`를 읽어 **다음 노드 키(문자열)** 를 반환하고, 매핑 dict가 그 키를 실제 노드로 해석한다.

현재 조건부 엣지는 **5개**다(W1/W2 확장).

| source | 분기 함수 | 제어 신호(읽는 state 필드) | 분기 |
|---|---|---|---|
| `reference_resolution_node` | `route_after_reference` | `target_service_ids` | referential → `rehydrate_node`(검색 우회). 비참조 → `triage_node`. |
| `triage_node` | `route_by_action` | `action`, `error`, `answer` | `RETRIEVE` → `cache_check_node`. `DIRECT_ANSWER`/`AMBIGUOUS`/`OUT_OF_SCOPE`/`EXPLAIN` → 동명 action 노드. error+answer → `answer_node`. |
| `out_of_scope_node` | `_dispatch_out_of_scope_route` | `out_of_scope_type` | `attribute_gap` → `vector_node`(검색 합류). `domain_outside` → `search_persist_node`(답변 이미 설정, 종단 체인). |
| `cache_check_node` | `post_cache_check` | `cache_hit`, (miss 시) `intent`·`error`·`answer` | `cache_hit=True` → `search_persist_node`(검색 우회). miss면 `route_by_intent` 위임 — `intent` 로 sql/vector/map/analytics 분기. |
| `pre_answer_gate_node` | `route_pre_answer_gate` | `action`, `hydrated_services`, `retry_count` | hydrated 0건(C2, retry=0) → `retry_prep_node`. 그 외 → `answer_node`. |
| `answer_node` | `self_correction_edge` | `action`, `retry_count`, `answer`, `intent`, `*_results` | 재시도 필요 시 `retry_prep_node`, 아니면 `end_normal`(=`cache_write_node`). 비-RETRIEVE action은 즉시 `end_normal`. 평가 순서는 §7-2. |

분기 함수는 모두 **state만 읽는 순수 함수**로 부수효과가 없다. 제어 신호는 전부 노드가 앞서 state에 써 둔 필드이며 분기 함수는 이를 판독만 한다.

구현상 분기 함수와 노드는 모듈 수준 dispatch 함수(`_dispatch_route_after_reference`·`_dispatch_route_by_action`·`_dispatch_post_cache_check`·`_dispatch_route_pre_answer_gate`·`_dispatch_self_correction_edge` 등)로 그래프에 등록되고, 실제 호출 대상 `GraphNodes` 인스턴스는 `_ACTIVE_NODES` ContextVar로 조회한다 — `CompiledStateGraph → AgentGraph` 순환 참조를 회피하기 위함이다.

> `route_by_action_fanout`은 `enable_secondary_intent=True`일 때 RETRIEVE 경로에서 SQL+VECTOR 병렬 팬아웃(`["sql_node", "vector_node"]`)을 수행하는 분기 함수다. 기본 플래그(False)에서는 `route_by_intent`로 단일 라우트한다.

### 7-2. Self-Correction 사이클 (방향성 재시도)

LangGraph 의 cycle 기능을 활용한 1회 한정 재시도 루프:

```
answer_node → [self_correction_edge]
                  ├─ retry → retry_prep_node → triage_node (retry_count=1로 증가)
                  └─ 종료  → cache_write_node → search_persist_node → trace_node

pre_answer_gate_node → [route_pre_answer_gate]   # C2 게이트
                  ├─ hydrated 0건(retry=0) → retry_prep_node → triage_node
                  └─ 유건                  → answer_node
```

> **C2 pre-answer 0건 게이트(제안1)**: SQL/VECTOR 경로는 `hydration_node → rrf_fusion_node → pre_answer_gate_node`를 거친다. `pre_answer_gate_node` 직후 `route_pre_answer_gate`가 `hydrated_services=[]`(retry=0)이면 answer LLM을 **호출하지 않고** 곧장 `retry_prep_node`로 보낸다. 빈 검색 결과로 답변 LLM을 낭비하지 않는다.

**재시도 트리거 평가 순서 (`self_correction_edge`)** — 다중 조건 동시 참 시 비결정성을 제거하기 위해 위에서부터 먼저 매칭되는 하나만 적용한다(1회 캡):

0. **비-RETRIEVE action** (DIRECT_ANSWER/AMBIGUOUS/OUT_OF_SCOPE/EXPLAIN) → `end_normal` (self-correction 제외).
1. **`retry_count != 0`** → `end_normal` (이미 1회 소진, 무한 루프 방지).
2. **빈 답변** (`not answer.strip()`) → `retry_prep_node`. intent 무관 최우선. error + fallback_answer 조합은 answer 가 차있어 통과(재시도 안 함).
3. **intent별 0건** (상호배타):
   - `SQL_SEARCH` / `VECTOR_SEARCH` → `_hard_filter_zero_hits` (hydrated/sql/vector 모두 빈).
   - `ANALYTICS` → `_analytics_zero_hits` (`analytics_results` 0행 또는 `error`).
   - `MAP` → `_map_zero_hits` (`features=[]` 만 재시도, `map_results=None`(lat/lng 미제공)은 제외).

**재시도 동작 — 완화(relax)가 아니라 방향성(directed) 전환/완화 (`retry_prep_node`)**: intent별로 분기한다.

| 원 intent | 동작 | 다음 순회 intent | 메커니즘 |
|---|---|---|---|
| `SQL_SEARCH` | **강제 전환** | `VECTOR_SEARCH` | `forced_intent` 주입 + 정형 필터 전부 비움(전환 경로 자체 정제). 레지스트리 `_RETRY_FALLBACK_INTENT` 로 확장 가능. |
| `VECTOR_SEARCH` | 기존 완화 | (재분류) | `refined_query`·필터 None 리셋. |
| `ANALYTICS` | **필터 1개 드롭** | `ANALYTICS` | `_ANALYTICS_DROP_ORDER`(status→area) 중 첫 비어있지 않은 1개만 드롭. `max_class_name` 유지. `analytics_keyword`는 제외 — `analytics_search`에 전달되는 keyword는 state 슬롯(trace 관측 전용)이 아니라 `AnalyticsAgent.run`이 매 실행 LLM으로 message에서 재추출하는 `params.keyword`라 state 드롭이 무효(0건 재현·무효 재시도 낭비). 드롭할 게 없으면 no-op 후 정직한 0건 안내. |
| `MAP` | **반경 확장** | `MAP` | `retry_radius_m=3000`(기본 1000). `map_node` 가 이 값을 우선 사용. intent 전환 아님. |

`forced_intent` 는 `triage_node` 가 LLM 재분류를 skip 하고 honor 한 뒤 **즉시 None 으로 소비**(1회성)하므로 무한 전환이 없다. 강제 전환 시 `refined_query=None` 이라 `cache_check` 가 pass-through 되어 0건이던 원 질의의 캐시 오hit 도 없다. `retry_prep_node`는 다음 순회를 `triage_node`로 재진입시킨다.

- 모든 분기가 `retry_count` 캡을 동일하게 받아 **최대 1회**. 재시도 후에도 0건이면 정직한 "결과 없음" 안내.
- 재시도 시 `retry_relaxed=True` 로 `AnswerAgent` 가 완화 사실(예: 조건 완화)을 답변에 반드시 명시한다(과잉완화 노출 가드).
- 그래프 호출 시 `recursion_limit=22` 으로 무한 사이클을 차단한다(W2 최악 경로: RETRIEVE 1회 정상 11 super-step + 재시도 7 = 18, 여유 4 포함).
- 재시도 진입 시 `stream()` 이 `re_searching` progress 이벤트("다른 방식으로 다시 검색하고 있습니다...")를 1회 emit 하고 검색/답변 진행 플래그를 리셋해 전환 경로의 `searching`/`answering` 이벤트가 다시 흐르게 한다.

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
| triage_node 예외 발생 | `answer` 에 안내 메시지 주입, `error` 에 원인 기록, `action=DIRECT_ANSWER` 설정, `node_path` 에 `"triage_error"` append, self-correction 우회(비-RETRIEVE) |
| sql/vector/map/analytics/hydration/answer 노드 예외 | `error` 필드 기록, `node_path` 에 `"*_error"` append, 가능하면 빈 결과로 다음 노드 진행 |
| rehydrate_node 예외 | `hydrated_services=[]` 로 폴백, `node_path` 에 `"rehydrate_error"` append. describe_node가 정직한 0건 안내 |
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
| 2026-06-06 | docs | 문서 정합성 정정(§3-1 IntentType 5종 + `_IntentOutput` 산출 필드 reflect, `_router_node`/`_route_by_intent`/`_self_correction_edge` → 실제 심볼명·`_dispatch_*` 등록명으로 drift 정정, §7-1↔§7-2 self_correction_edge 평가 순서 정합) + §7-0 상태기반 엣지 제어(데이터/제어 분리, 조건부 엣지 2개, `_ACTIVE_NODES` ContextVar) 설명 보강. |
| 2026-06-05 | 방향성 self-correction 재시도 | 재시도가 "router 재분류"(완화)에서 "방향성 전환/완화"로 강화. `AgentState`에 `forced_intent` / `retry_radius_m` 추가. `retry_prep_node` intent별 분기: SQL_SEARCH→VECTOR_SEARCH 강제 전환(레지스트리 `_RETRY_FALLBACK_INTENT`), ANALYTICS 제약 큰 effective 필터 1개 드롭(`_ANALYTICS_DROP_ORDER`=status→area, max_class_name 유지, analytics_keyword는 LLM 재추출이라 제외), MAP 반경 확장(`_MAP_RETRY_RADIUS_M=3000`). `self_correction_edge` 트리거 평가 순서 명문화 + ANALYTICS/MAP 0건 트리거 추가(`_analytics_zero_hits`/`_map_zero_hits`). `router_node` 가 `forced_intent` honor 후 즉시 소비(1회성). `stream()` 재시도 진입 시 `re_searching` progress 1회 emit + 진행 플래그 리셋. 1회 캡·`recursion_limit=16` 유지. |
| 2026-06-06 | W1 — 결과 엔티티 carryover + 참조 해소 | `reference_resolution_node`(START 직후 선판정) + `rehydrate_node` + `describe_node` 신설. 규칙 기반 지시 참조 판정(`agents/_reference_resolution.py` — 지시어/한국어·아라비아 서수/라벨 부분일치). `ChatRequest`에 `prev_entities`/`prev_intent`/`prev_reasoning` 추가, `AgentState`에 동명 슬롯 + `target_service_ids` 추가. referential 시 `hydrate_services(target_service_ids)` 재수화 → `AnswerAgent.describe()` 설명형 답변(정체성만 carryover, 사실은 재-hydrate). |
| 2026-06-06 | W2 + 제안1 — TriageAgent 2축 분리 | `RouterAgent`를 대체·포함하는 `TriageAgent`(`agents/triage_agent.py`, `llm/prompts/triage.py`) 신설. action×retrieval_intent 직교 출력(`TriageOutput`). action 5종(RETRIEVE/DIRECT_ANSWER/AMBIGUOUS/OUT_OF_SCOPE/EXPLAIN), `triage_node`(=router_node alias) 이후 `route_by_action` 분기 + action별 노드(`direct_answer_node`/`ambiguous_node`/`out_of_scope_node`/`explain_node`). 제안1: `rrf_fusion_node` + `pre_answer_gate_node`(C2 0건 게이트) + `enable_secondary_intent` 피처플래그(기본 False). hydration이 `vector_node` 내부에서 `hydration_node`(별도)로 분리. `AgentState`에 `action`/`out_of_scope_type`/`user_rationale`/`secondary_intent`/`rrf_merged_ids` 추가. `recursion_limit` 15 → 22 상향. |
| 2026-06-06 | W3 — decision SSE 이벤트 | `schemas/events.py`에 `DecisionEvent`(event/action/routes/user_rationale/sources) 신설. `triage_node` 완료 직후 `stream()`이 1회 emit(LLM 분류 수행 시만). `sanitize_user_rationale()`(내부 `__` 식별자 패턴 줄 제거 + 200자 절단). |
| 2026-06-06 | 제안3 — 1000 QPS 수평 확장(동시성 관점) | `core/concurrency.py` 글로벌 fan-out 세마포어(`vector_global_concurrency`), `tools/tokenizer.py` `atokenize_query()` `asyncio.to_thread` 오프로드, OpenAI httpx 싱글톤. (PgBouncer/Nginx 등 인프라 상세는 `docs/scaling.md` 참조.) |

`AgentState` 입출력 규약을 유지하므로 각 Agent 클래스(`triage_agent.py`, `sql_agent.py`, `vector_agent.py`, `answer_agent.py`, `analytics_agent.py`)는 수정 없이 재사용된다. `agents/workflow.py` (LCEL)는 레거시로 유지된다.
