# Chat Search Results Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 질의별 **검색 결과**(sql / vector 트랙별 / bm25 / rrf 병합 / map / final 등)를 DB에 적재하여 운영·디버깅·평가·향후 가중치 튜닝의 근거 데이터로 활용한다. 기존 `chat_agent_traces`(실행 메타데이터 — intent / node_path / elapsed_ms / error)와 직교한 별도 테이블·노드로 추가한다.

**Scope:** AgentState에 channel-level 결과를 담는 `search_channels` 컨테이너 도입 + 신규 종단 노드 `search_results_node` 가 일괄 적재. **채널 집합은 진화하지만(Phase 1→2→3) 스키마는 고정**이라는 원칙으로 설계한다.

**Architecture:**
- **두 테이블 + 채널 키로 묶기**:
  - `chat_search_queries(message_id, kind, channel, query_text, parameters JSONB, ...)` — **input**. 무엇으로 검색했는지(임베딩된 텍스트 / SQL 파라미터 / BM25 토큰 / lat·lng·radius 등).
  - `chat_search_results(message_id, kind, channel, rank, service_id, score, meta JSONB, ...)` — **output**. 채널이 반환한 시설 순위.
  - 두 테이블은 `(message_id, channel)` 키로 묶인다. `kind` 는 양쪽에 동일하게 들어가 분석 시 단일 테이블만 보고도 종류 그룹화가 가능하다.
- **`kind` 컬럼 (분석 그룹화)**: `sql` / `vector` / `bm25` / `rrf` / `map` / `final`. CHECK 제약으로 화이트리스트 강제. 채널 추가는 마이그레이션 없이 가능하지만 새 kind 가 필요해지면 ALTER로 추가 (kind는 안정적이라 빈도 낮음).
- **`channel` 컬럼 (세부 디스크리미네이터)**: kind 안의 세부 채널. CHECK 미적용 (freeform) — 향후 새 채널 추가가 자유로움.
- **AgentState.search_channels**: `dict[str, ChannelData]` 형태. 각 노드가 자기 채널 키 하나를 `ChannelData(query, hits)` 로 채운다. query + hits를 한 묶음으로 둬 짝을 잃지 않는다.
- **종단 노드 `search_persist_node`**: `trace_node` 직전 best-effort 적재 노드. AgentState 의 `search_channels` 를 순회하여 **queries 테이블과 results 테이블에 동일 트랜잭션으로 INSERT**.
- **채널 / kind 매핑**:

  | kind | 채널 (Phase 진화) |
  |---|---|
  | `sql` | `sql` |
  | `vector` | `vector` (Phase 1) → `vector_a` · `vector_b` · `vector_c` (Phase 2) · `hyde_vector` (Phase 3) |
  | `bm25` | `bm25` |
  | `rrf` | `rrf` (Phase 2+) |
  | `map` | `map` |
  | `final` | `final` |

- **`final` 채널**: hydration · dedup · top_k 절단까지 끝난, **실제 사용자에게 노출된 시설 목록**. 다른 채널과 의미가 달라 분석 용도로 매우 중요.
- **`rrf` / `final` 의 query 의미**: 원본 검색을 수행하지 않고 상위 채널 결과를 병합/필터하는 채널이므로 `query_text` 는 `NULL` 로 두고 `parameters` 에 source 채널 목록과 가중치/dedup 정보를 기록한다.

**Tech Stack:** Python 3.13, SQLAlchemy async, LangGraph, pytest

---

## 관련 문서

- **읽기 (검토 완료)**:
  - `docs/ai-agent-design.md` — 현재 그래프 구조, `chat_agent_traces` 동작, trace_node 패턴
  - `docs/superpowers/plans/2026-05-18-triple-track-embedding-pipeline.md` — `service_embeddings` 통합 테이블, Phase 1 단일 경쟁 vector + BM25 union
  - `docs/superpowers/plans/2026-05-18-rrf-hybrid-search.md` — Phase 2의 4 채널 + RRF 결합 흐름

- **갱신 대상**:
  - `docs/ai-agent-design.md` — `AgentState.search_channels` 필드, `search_results_node` 노드, 흐름 다이어그램
  - `2026-05-18-rrf-hybrid-search.md` — VectorAgent 가 `search_channels` 에 채널별 결과를 채우도록 흐름 보강
  - `2026-05-18-triple-track-embedding-pipeline.md` — Task 6-2 (Phase 1 vector_search) 가 `search_channels` 에 `vector` / `bm25` 채널을 채우도록 추가

---

## File Map

| 파일 | 역할 | 변경 |
|------|------|------|
| `scripts/ddl/chat_search.sql` | `chat_search_queries` + `chat_search_results` 두 테이블 + 인덱스 + COMMENT | 신규 |
| `scripts/ddl_chat_entities.sql` | 신규 DDL 파일을 `\i` 로 include | 수정 |
| `schemas/state.py` | `AgentState` 에 `search_channels: dict[str, ChannelData]` 필드 추가 | 수정 |
| `schemas/search.py` | `ChannelHit` / `ChannelQuery` / `ChannelData` TypedDict, 채널명 상수 (`SearchChannel`), kind 상수 (`SearchKind`), 채널→kind 매핑 함수 | 신규 |
| `agents/nodes.py` | `search_persist_node` 추가 — queries+results 일괄 best-effort INSERT. `sql_node` / `map_node` 가 `ChannelData` 채우도록 보강 | 수정 |
| `agents/graph.py` | `search_persist_node` 를 종단부에 등록. 엣지: `answer_node → search_persist_node → trace_node → END` (self-correction 후) | 수정 |
| `agents/sql_agent.py` | 결과 후 `ChannelHit` 변환 로직 호출 (또는 노드 측에서 변환) | 수정 |
| `agents/vector_agent.py` | Phase 1: `vector` / `bm25` / `final` 채널 채움. Phase 2 도입 시 `vector_a/b/c`/`rrf` 추가 (RRF 계획에서 반영) | 수정 |
| `agents/answer_agent.py` | answer 생성 후 `final` 채널을 state에 채움 (실제 응답에 들어간 service_id 목록) | 수정 |
| `tests/test_chat_search_ddl.py` | 두 테이블 DDL 적용 + COMMENT/제약 검증 | 신규 |
| `tests/test_search_persist_node.py` | 노드 단위 — queries+results 두 테이블 INSERT, best-effort 동작, 빈 채널 skip | 신규 |
| `tests/test_graph_search_persist.py` | 그래프 end-to-end — 각 intent별 적재되는 channel/kind 검증 | 신규 |
| `docs/ai-agent-design.md` | `search_channels`/`search_results_node` 반영 | 수정 |
| `docs/chat-search-persistence.md` | 운영 가이드 (두 테이블 스키마, kind/channel 의미, 분석 쿼리 예시, 보존 정책) | 신규 |

---

## Task 1: DDL — `chat_search_queries` + `chat_search_results`

### 위치

전용 파일 `scripts/ddl/chat_search.sql` 에 두 테이블을 함께 작성하고 `scripts/ddl_chat_entities.sql` 에서 `\i` 로 include. 두 테이블은 항상 짝으로 사용되므로 한 파일에 둔다.

### 테이블 1: `chat_search_queries` (input)

무엇으로 검색했는지. `(message_id, channel)` 이 유일.

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `message_id` | BIGINT NOT NULL | API 서비스의 `chat_messages.id` 논리 FK |
| `kind` | VARCHAR(8) NOT NULL | `sql` / `vector` / `bm25` / `rrf` / `map` / `final` (CHECK 화이트리스트) |
| `channel` | VARCHAR(32) NOT NULL | 세부 채널 (`sql`, `vector_a`, `rrf`, `hyde_vector` 등). CHECK 미적용 |
| `query_text` | TEXT | 임베딩된 텍스트 / SQL keyword / BM25 토큰 join / 좌표 표현 등 사람-읽기 가능한 단일 표현. rrf/final은 NULL |
| `parameters` | JSONB | 구조화 파라미터 — SQL filters, top_k, min_similarity, BM25 tokens, lat/lng/radius, RRF weights & source_channels 등 |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**제약**: `UNIQUE (message_id, channel)`, `CHECK (kind IN ('sql','vector','bm25','rrf','map','final'))`

**인덱스**:
- B-tree (`message_id`) — 메시지별 모든 채널 query 조회
- B-tree (`message_id, channel`) — 특정 채널 query 조회 (results와 짝)

### 테이블 2: `chat_search_results` (output)

채널이 반환한 시설 순위. `(message_id, channel, rank)` 이 유일.

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `message_id` | BIGINT NOT NULL | API 서비스의 `chat_messages.id` 논리 FK |
| `kind` | VARCHAR(8) NOT NULL | queries 테이블과 동일 값 (denormalize — 조인 없이 분석 가능) |
| `channel` | VARCHAR(32) NOT NULL | queries 테이블과 동일 채널 |
| `rank` | SMALLINT NOT NULL | 1-based |
| `service_id` | VARCHAR(255) NOT NULL | |
| `score` | DOUBLE PRECISION | 채널 native 점수 (similarity / bm25_score / rrf_score / distance_m 등) |
| `meta` | JSONB | 채널별 부가 정보 (`intent_label`, `embedding_text` 등) |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**제약**: `UNIQUE (message_id, channel, rank)`, `CHECK (rank >= 1)`, `CHECK (kind IN ('sql','vector','bm25','rrf','map','final'))`

**인덱스**:
- B-tree (`message_id`)
- B-tree (`message_id, channel`)
- B-tree (`service_id`) — 시설 역추적
- B-tree (`message_id, kind`) — kind별 일괄 조회

### 두 테이블이 분리된 이유

- queries는 채널당 1행, results는 채널당 1~N행. 카디널리티가 다르므로 정규화가 자연스럽다.
- queries.parameters에 큰 JSONB가 들어갈 수 있는데(SQL의 모든 필터, RRF 가중치 매트릭스 등), 이를 results의 모든 row에 복제하면 낭비.
- 분석 시 "어떤 텍스트로 검색했는가" 와 "무엇이 반환됐는가" 는 독립적인 질문이라 자연스럽게 분리된다.
- INSERT는 트랜잭션 하나로 묶이므로 정합성 보장.

### kind ↔ channel 매핑 (애플리케이션 컨벤션)

| kind | channels |
|---|---|
| `sql` | `sql` |
| `vector` | `vector` (Phase 1), `vector_a`, `vector_b`, `vector_c` (Phase 2), `hyde_vector` (Phase 3) |
| `bm25` | `bm25` |
| `rrf` | `rrf` (Phase 2+) |
| `map` | `map` |
| `final` | `final` |

### COMMENT 정책

테이블 2개, 모든 컬럼, 모든 인덱스, 모든 제약에 `COMMENT ON ...` 추가. service_embeddings DDL과 동일 정책. 특히:
- `kind` 컬럼 코멘트: 허용 값 6종과 각각의 의미
- `channel` 컬럼 코멘트: 현재 알려진 채널 목록 (CHECK는 없지만 가시성 확보)
- `query_text` / `parameters` 컬럼: 채널 종류별로 어떤 형태가 들어가는지 명시

### 분석 쿼리 패턴

```sql
-- 메시지 1건의 모든 채널: 무엇으로 검색했고 무엇이 나왔는지
SELECT q.channel, q.kind, q.query_text, q.parameters,
       r.rank, r.service_id, r.score
FROM chat_search_queries  q
JOIN chat_search_results  r USING (message_id, channel)
WHERE q.message_id = $1
ORDER BY q.channel, r.rank;

-- kind별 평균 결과 수 (vector 계열 일괄)
SELECT kind, AVG(cnt) FROM (
    SELECT message_id, channel, count(*) AS cnt
    FROM chat_search_results
    WHERE kind = 'vector' AND created_at >= NOW() - INTERVAL '7 days'
    GROUP BY message_id, channel
) t GROUP BY kind;

-- 특정 검색어로 임베딩된 질의 찾기 (BM25 stopword 튜닝 등에 활용)
SELECT message_id, channel, query_text
FROM chat_search_queries
WHERE kind = 'vector' AND query_text ILIKE '%수영장%';
```

**Files:**
- Create: `scripts/ddl/chat_search.sql`
- Modify: `scripts/ddl_chat_entities.sql`
- Create: `tests/test_chat_search_ddl.py`

- [x] **Step 1: DDL 파일 작성** (두 테이블 + 제약 + 인덱스 + COMMENT 모두 포함)

- [x] **Step 2: include 추가**

```sql
-- scripts/ddl_chat_entities.sql
\i scripts/ddl/service_embeddings.sql
\i scripts/ddl/chat_search.sql
```

- [x] **Step 3: 적용 + 검증**

```bash
psql "$ON_AI_DSN" -f scripts/ddl_chat_entities.sql
psql "$ON_AI_DSN" -c "\d+ chat_search_queries"
psql "$ON_AI_DSN" -c "\d+ chat_search_results"
```

- [x] **Step 4: 제약/COMMENT 검증 테스트**

```python
class TestChatSearchDDL:
    async def test_queries_unique_message_channel(self, ai_session): ...
    async def test_results_unique_message_channel_rank(self, ai_session): ...
    async def test_rank_must_be_positive(self, ai_session): ...
    async def test_kind_check_whitelist(self, ai_session):
        """invalid kind('xyz') INSERT 시 CHECK 위반."""
    async def test_freeform_channel_accepted(self, ai_session):
        """미등록 채널명 'hyde_vector' / 'future_kind' insert 통과 (channel은 CHECK 없음)."""
    async def test_all_columns_have_comments(self, ai_session): ...
    async def test_query_text_nullable_for_rrf_final(self, ai_session): ...
```

---

## Task 2: `schemas/search.py` — `ChannelData` + kind/channel 상수

`AgentState.search_channels` 의 값 타입(`ChannelData`)을 정의하고, kind/channel을 코드 상수로 관리한다.

```python
# schemas/search.py
from typing import Any, Final, TypedDict


class ChannelHit(TypedDict):
    rank: int                    # 1-based
    service_id: str
    score: float | None
    meta: dict[str, Any]         # 채널별 부가 정보. 빈 dict 허용


class ChannelQuery(TypedDict):
    query_text: str | None       # 임베딩 텍스트/SQL keyword/BM25 토큰 join 등. rrf/final은 None
    parameters: dict[str, Any]   # 구조화 파라미터


class ChannelData(TypedDict):
    """채널 1개의 입력(query)과 출력(hits)을 한 묶음으로 보관."""
    kind: str                    # SearchKind 상수 값 중 하나
    query: ChannelQuery
    hits: list[ChannelHit]


class SearchKind:
    """kind 화이트리스트. DB CHECK 와 동기화 유지."""
    SQL: Final[str]    = "sql"
    VECTOR: Final[str] = "vector"
    BM25: Final[str]   = "bm25"
    RRF: Final[str]    = "rrf"
    MAP: Final[str]    = "map"
    FINAL: Final[str]  = "final"


class SearchChannel:
    """채널명 상수. DB CHECK는 없지만 코드에서는 이 상수만 사용한다."""
    # kind=sql
    SQL: Final[str]            = "sql"
    # kind=vector
    VECTOR: Final[str]         = "vector"           # Phase 1 단일 경쟁
    VECTOR_A: Final[str]       = "vector_a"         # Phase 2+
    VECTOR_B: Final[str]       = "vector_b"
    VECTOR_C: Final[str]       = "vector_c"
    HYDE_VECTOR: Final[str]    = "hyde_vector"      # Phase 3+
    # kind=bm25
    BM25: Final[str]           = "bm25"
    # kind=rrf
    RRF: Final[str]            = "rrf"
    # kind=map
    MAP: Final[str]            = "map"
    # kind=final
    FINAL: Final[str]          = "final"


_CHANNEL_TO_KIND: dict[str, str] = {
    SearchChannel.SQL:         SearchKind.SQL,
    SearchChannel.VECTOR:      SearchKind.VECTOR,
    SearchChannel.VECTOR_A:    SearchKind.VECTOR,
    SearchChannel.VECTOR_B:    SearchKind.VECTOR,
    SearchChannel.VECTOR_C:    SearchKind.VECTOR,
    SearchChannel.HYDE_VECTOR: SearchKind.VECTOR,
    SearchChannel.BM25:        SearchKind.BM25,
    SearchChannel.RRF:         SearchKind.RRF,
    SearchChannel.MAP:         SearchKind.MAP,
    SearchChannel.FINAL:       SearchKind.FINAL,
}


def kind_of(channel: str) -> str:
    """채널명에서 kind 를 조회. 미등록 채널은 ValueError (typo 방지)."""
    if channel not in _CHANNEL_TO_KIND:
        raise ValueError(f"unknown channel: {channel!r}")
    return _CHANNEL_TO_KIND[channel]
```

**Files:**
- Create: `schemas/search.py`
- Modify: `schemas/state.py` — `AgentState` 에 `search_channels: Annotated[dict[str, ChannelData], operator.or_]` 추가 (reducer 적용은 Task 4 참조)

- [x] **Step 1: 모델 작성 + AgentState 확장**

- [x] **Step 2: 상수/매핑 단위 테스트**

```python
class TestSearchSchemas:
    def test_kind_of_returns_correct_kind(self):
        assert kind_of("vector_a") == "vector"
        assert kind_of("hyde_vector") == "vector"
        assert kind_of("sql") == "sql"
        assert kind_of("rrf") == "rrf"

    def test_unknown_channel_raises(self):
        with pytest.raises(ValueError):
            kind_of("xyz")

    def test_all_known_channels_mapped(self):
        """SearchChannel 의 모든 상수가 _CHANNEL_TO_KIND 에 등록되어 있다."""
```

- [x] **Step 3: 회귀 — 모든 dict 리터럴 초기화 지점 확인**

```bash
uv run pytest tests/ -v -k "state or agent or graph or chat"
```

`KeyError: 'search_channels'` 발생 지점에서 빈 dict로 초기화 추가.

---

## Task 3: `search_persist_node` — 종단 적재 노드 (queries + results 일괄)

`trace_node` 와 동일한 best-effort 패턴. 그래프 종단부에 위치하며 **한 트랜잭션에서 두 테이블을 동시에 적재**한다.

### 동작

```python
async def search_persist_node(self, state: AgentState) -> dict[str, Any]:
    """search_channels 를 순회하여 chat_search_queries + chat_search_results 일괄 적재.

    best-effort:
      - INSERT 실패는 그래프 결과에 영향 없음 (logger.warning + rollback)
      - 빈 채널은 INSERT 0건으로 skip
      - 두 테이블은 같은 트랜잭션에서 커밋 → 한쪽만 적재되는 일관성 깨짐 방지
    """
    channels = state.get("search_channels") or {}
    if not channels:
        return {}

    message_id = state["message_id"]
    query_rows: list[dict] = []
    result_rows: list[dict] = []

    for channel_name, data in channels.items():
        kind = data["kind"]
        query = data["query"]
        hits = data["hits"]

        # queries 테이블: 채널당 1행 (hits가 비어도 query는 기록 — 0건 결과 분석에 필요)
        query_rows.append({
            "message_id": message_id,
            "kind":       kind,
            "channel":    channel_name,
            "query_text": query.get("query_text"),
            "parameters": json.dumps(query.get("parameters") or {}, default=str),
        })

        # results 테이블: 채널당 N행
        for hit in hits:
            result_rows.append({
                "message_id": message_id,
                "kind":       kind,
                "channel":    channel_name,
                "rank":       hit["rank"],
                "service_id": hit["service_id"],
                "score":      hit.get("score"),
                "meta":       json.dumps(hit.get("meta") or {}, default=str),
            })

    try:
        if query_rows:
            await self._ai_session.execute(INSERT_QUERIES_SQL, query_rows)
        if result_rows:
            await self._ai_session.execute(INSERT_RESULTS_SQL, result_rows)
        await self._ai_session.commit()
    except Exception:
        logger.warning("chat_search 적재 실패 (message_id=%s)", message_id, exc_info=True)
        try:
            await self._ai_session.rollback()
        except Exception:
            pass
    return {}
```

**0건 결과 정책**: hits가 비어도 query 행은 기록한다. "검색했는데 결과 없음" 도 분석 가치가 있는 신호이기 때문 (recall 부족, stopword 과적용 등).

### 그래프 배선

```
answer_node
   └─ self_correction_edge
        ├─ needs_retry → router_node (재진입)
        └─ otherwise   → search_persist_node → trace_node → END
```

`trace_node` 가 final terminal이라는 기존 정책 유지. `search_persist_node` 적재 실패가 `trace_node` 호출을 막지 않는다.

**Files:**
- Modify: `agents/nodes.py` — `search_persist_node` 추가
- Modify: `agents/graph.py` — dispatch 함수 + 엣지 배선
- Create: `tests/test_search_persist_node.py`

- [x] **Step 1: 노드 단위 테스트 + 통과**

```python
class TestSearchPersistNode:
    async def test_empty_channels_skip(self, ai_session):
        """search_channels 가 빈 dict면 두 테이블 모두 INSERT 0건."""

    async def test_inserts_queries_and_results(self, ai_session):
        """sql / vector / final 채널 → queries 3행 + results N행 (kind/channel 일치)."""

    async def test_zero_hits_still_writes_query(self, ai_session):
        """hits가 비어도 chat_search_queries 에는 1행 INSERT (0건 결과 분석용)."""

    async def test_kind_denormalized_consistently(self, ai_session):
        """동일 채널의 queries.kind 와 results.kind 가 같다."""

    async def test_best_effort_on_failure(self, ai_session, mocker):
        """INSERT 예외 시 logger.warning + rollback + 빈 dict 반환."""

    async def test_transactional_consistency(self, ai_session, mocker):
        """results 적재 도중 실패 시 queries 도 rollback (둘 다 0행)."""

    async def test_freeform_channel_accepted(self, ai_session):
        """미등록 채널명도 INSERT 통과 (channel은 CHECK 없음)."""
```

- [x] **Step 2: 그래프 엣지 배선 + 통합 테스트**

```python
class TestGraphSearchPersistRouting:
    async def test_answer_then_persist_then_trace(self, mocker):
        """노드 호출 순서: answer → search_persist → trace."""
    async def test_persist_failure_does_not_block_trace(self, mocker):
        """search_persist_node 실패 시에도 trace_node 가 호출된다."""
```

---

## Task 4: 노드별 ChannelData 채움

각 노드가 자기 책임의 채널을 `search_channels` 에 `ChannelData(kind, query, hits)` 한 묶음으로 기록한다.

### state 병합 규약

`search_channels` 는 reducer 적용 필드로 선언한다:

```python
# schemas/state.py
from typing import Annotated
import operator

class AgentState(TypedDict):
    ...
    search_channels: Annotated[dict[str, ChannelData], operator.or_]
```

각 노드가 자기 채널 키 1개만 담은 dict를 반환해도 LangGraph가 `operator.or_` 로 누적 병합한다. self-correction 재시도 시는 `retry_prep_node` 에서 `{"search_channels": {}}` 로 명시 리셋한다 (UNIQUE 위반 방지).

### 4-1. `sql_node` (SqlAgent)

```python
filters = {"max_class_name": ..., "area_name": ..., "service_status": ..., "keyword": ...}
sql_rows = await sql_search(session, **filters, top_k=TOP_K)

channel_data: ChannelData = {
    "kind": SearchKind.SQL,
    "query": {
        "query_text": filters.get("keyword"),    # primary 표현
        "parameters": {**filters, "top_k": TOP_K},
    },
    "hits": _to_hits(sql_rows, score_field=None),
}
return {
    "sql_results": sql_rows,
    "search_channels": {SearchChannel.SQL: channel_data},
}
```

### 4-2. `map_node`

```python
geojson = await map_search(session, lat, lng, radius_m=R, top_k=TOP_K)
features = geojson["features"]

channel_data: ChannelData = {
    "kind": SearchKind.MAP,
    "query": {
        "query_text": f"lat={lat},lng={lng},r={R}m",
        "parameters": {"lat": lat, "lng": lng, "radius_m": R, "top_k": TOP_K},
    },
    "hits": [
        {
            "rank": i + 1,
            "service_id": f["properties"]["service_id"],
            "score": float(f["properties"]["distance_m"]),
            "meta": {"distance_m": f["properties"]["distance_m"]},
        }
        for i, f in enumerate(features)
    ],
}
return {"map_results": geojson, "search_channels": {SearchChannel.MAP: channel_data}}
```

### 4-3. `vector_node` — Phase 1 (현재)

```python
refined = await self._refine_chain.ainvoke({"message": state["message"]})
query_vec = await embedder.embed(refined.refined_query)
vector_rows = await vector_search(ai_session, query_vec, top_k=TOP_K)

bm25_tokens = tokenize_and_filter(refined.refined_query)
bm25_rows = await bm25_search(ai_session, bm25_tokens) if bm25_tokens else []

# 단순 union dedup → hydration
hydrated = await hydrate_services(data_session, _union_ids(vector_rows, bm25_rows))

return {
    "vector_results": hydrated,
    "refined_query": refined.refined_query,
    "search_channels": {
        SearchChannel.VECTOR: {
            "kind": SearchKind.VECTOR,
            "query": {
                "query_text": refined.refined_query,    # 임베딩된 텍스트
                "parameters": {"top_k": TOP_K, "min_similarity": MIN_SIM},
            },
            "hits": _to_hits(vector_rows, score_field="similarity"),
        },
        SearchChannel.BM25: {
            "kind": SearchKind.BM25,
            "query": {
                "query_text": " ".join(bm25_tokens),
                "parameters": {"tokens": bm25_tokens, "top_k": TOP_K},
            },
            "hits": _to_hits(bm25_rows, score_field="bm25_score"),
        },
        SearchChannel.FINAL: {
            "kind": SearchKind.FINAL,
            "query": {
                "query_text": None,
                "parameters": {"source_channels": ["vector", "bm25"], "hydration_applied": True},
            },
            "hits": _to_hits(hydrated, score_field=None),
        },
    },
}
```

### 4-4. `vector_node` — Phase 2 (RRF — 별도 계획에서 반영)

```python
return {
    "vector_results": hydrated,
    "search_channels": {
        SearchChannel.VECTOR_A: {
            "kind": SearchKind.VECTOR,
            "query": {"query_text": refined.refined_query, "parameters": {...post_filter...}},
            "hits": _to_hits(a_rows, score_field="similarity"),
        },
        SearchChannel.VECTOR_B: {"kind": SearchKind.VECTOR, "query": {...}, "hits": _to_hits(b_rows, ...)},
        SearchChannel.VECTOR_C: {
            "kind": SearchKind.VECTOR,
            "query": {...},
            "hits": _to_hits(c_rows, score_field="similarity",
                             meta_fn=lambda r: {"intent_label": r["intent_label"]}),
        },
        SearchChannel.BM25: {"kind": SearchKind.BM25, "query": {...}, "hits": ...},
        SearchChannel.RRF: {
            "kind": SearchKind.RRF,
            "query": {
                "query_text": None,
                "parameters": {
                    "source_channels": ["vector_a", "vector_b", "vector_c", "bm25"],
                    "weights": weights,
                    "k_constant": 60,
                },
            },
            "hits": _to_hits_from_rrf(merged),
        },
        SearchChannel.FINAL: {
            "kind": SearchKind.FINAL,
            "query": {"query_text": None, "parameters": {"source_channel": "rrf", "hydration_applied": True}},
            "hits": _to_hits(hydrated, score_field=None),
        },
    },
}
```

> Phase 2 RRF 계획서는 위 흐름을 그대로 추가하는 한 줄 메모만으로 본 계획과 자연 호환된다.

### 4-5. `answer_node` (FALLBACK 케이스)

`final` 채널이 vector_node 에서 이미 채워졌다면 answer 단계에서는 손대지 않는다. **FALLBACK intent (검색 미실행)에서는 search_channels 를 비운 채로 둔다** — `search_persist_node` 가 0행 적재로 처리.

**Files:**
- Modify: `schemas/state.py` — `search_channels` reducer 선언
- Modify: `agents/sql_agent.py` — `ChannelData` 반환
- Modify: `agents/nodes.py::map_node` — 동일
- Modify: `agents/vector_agent.py` — Phase 1: vector/bm25/final 채널 모두
- Modify: `agents/nodes.py::retry_prep_node` — `search_channels` 리셋 추가
- Create: `agents/_search_channel_utils.py` — `_to_hits(rows, *, score_field, meta_fn=None) -> list[ChannelHit]` 헬퍼

- [x] **Step 1: `_to_hits` 헬퍼 + 단위 테스트**

```python
class TestToHits:
    def test_assigns_1_based_rank(self): ...
    def test_extracts_score_field(self): ...
    def test_score_none_when_field_missing(self): ...
    def test_meta_fn_called_per_row(self): ...
```

- [x] **Step 2: 각 노드 변경 + 회귀**

- [x] **Step 3: reducer + 리셋 동작 검증**

```python
class TestSearchChannelsReducer:
    async def test_channels_accumulate_across_nodes(self):
        """sql_node → vector_node 순으로 진행해도 sql 키가 보존된다."""

    async def test_retry_prep_resets_channels(self):
        """retry_prep_node 가 search_channels 를 {} 로 리셋한다 (재시도 UNIQUE 위반 방지)."""

    async def test_channel_data_pair_intact(self):
        """노드가 ChannelData를 통째로 넣으므로 query와 hits가 항상 짝을 이룬다."""
```

---

## Task 5: 그래프 end-to-end 통합 테스트

각 intent 시나리오별로 두 테이블에 어떤 행이 들어가는지 검증.

**Files:**
- Create: `tests/test_graph_search_persist.py`

```python
class TestSearchPersistByIntent:
    async def test_sql_intent_persists_sql_and_final(self, test_db):
        """SQL_SEARCH 시:
           - chat_search_queries: sql, final 2행
           - chat_search_results: sql N행 + final M행
           - kind 일관성: queries.kind == results.kind for 같은 channel
        """

    async def test_vector_intent_persists_phase1_channels(self, test_db):
        """VECTOR_SEARCH (Phase 1) 시 queries 3행 (vector/bm25/final) + 해당 results."""

    async def test_map_intent_persists_map(self, test_db):
        """MAP 시 queries 1행(map) + results 행들."""

    async def test_fallback_intent_persists_nothing(self, test_db):
        """FALLBACK 시 두 테이블 모두 0행."""

    async def test_zero_hit_query_still_recorded(self, test_db):
        """SQL 0건 결과여도 queries 에 sql 행은 기록 (results는 0행)."""

    async def test_self_correction_persists_only_last_attempt(self, test_db):
        """1회 재시도 시 retry_prep_node 가 search_channels 리셋 → 마지막 시도만 적재.
        UNIQUE 위반 없이 정확히 마지막 attempt 의 채널만 남는다."""

    async def test_query_text_examples(self, test_db):
        """채널별 query_text 형태가 명세대로 들어가는지:
           - sql: keyword 또는 None
           - vector*: refined_query 또는 hyde_document
           - bm25: 토큰 join
           - map: 'lat=...,lng=...,r=...m'
           - rrf/final: None
        """
```

---

## Task 6: 분석 쿼리 예시 + 운영 가이드

`docs/chat-search-persistence.md` 신설. 데이터 소비자(개발자/분석가)가 어떤 쿼리를 쓸지 가이드한다.

**예시 쿼리:**

```sql
-- ① 특정 메시지의 전체 검색 흐름: 무엇으로 검색해서 무엇이 나왔는지
SELECT q.kind, q.channel, q.query_text, q.parameters,
       r.rank, r.service_id, r.score, r.meta
FROM chat_search_queries q
LEFT JOIN chat_search_results r USING (message_id, channel)
WHERE q.message_id = $1
ORDER BY q.channel, r.rank;

-- ② kind별 평균 결과 수 (vector 계열 일괄 진단)
SELECT kind, channel, AVG(cnt) FROM (
    SELECT message_id, kind, channel, count(*) AS cnt
    FROM chat_search_results
    WHERE created_at >= NOW() - INTERVAL '7 days'
    GROUP BY message_id, kind, channel
) t GROUP BY kind, channel ORDER BY kind, channel;

-- ③ RRF가 끌어올린 시설 vs 단일 채널에서만 보이던 시설 비교 (Phase 2)
WITH r AS (SELECT message_id, service_id FROM chat_search_results WHERE channel='rrf' AND rank<=10),
     a AS (SELECT message_id, service_id FROM chat_search_results WHERE channel='vector_a' AND rank<=10)
SELECT count(*) FROM r LEFT JOIN a USING (message_id, service_id) WHERE a.service_id IS NULL;

-- ④ 자주 surface 되지만 final 에서 떨어지는 시설 (hydration miss / dedup 패배 진단)
SELECT service_id, count(*) AS surfaced_count
FROM chat_search_results
WHERE kind IN ('vector', 'rrf', 'bm25') AND rank<=10
  AND NOT EXISTS (
    SELECT 1 FROM chat_search_results f
    WHERE f.message_id = chat_search_results.message_id
      AND f.channel='final' AND f.service_id = chat_search_results.service_id
  )
GROUP BY service_id ORDER BY surfaced_count DESC LIMIT 20;

-- ⑤ 0건 결과 질의 분석 (recall 부족 채널 / stopword 과적용 진단)
SELECT q.kind, q.channel, q.query_text, q.parameters
FROM chat_search_queries q
LEFT JOIN chat_search_results r USING (message_id, channel)
WHERE r.id IS NULL
  AND q.kind IN ('sql','vector','bm25')
  AND q.created_at >= NOW() - INTERVAL '24 hours'
ORDER BY q.created_at DESC;

-- ⑥ 특정 임베딩 텍스트로 검색된 적이 있는지 (튜닝/디버깅)
SELECT message_id, channel, query_text
FROM chat_search_queries
WHERE kind='vector' AND query_text ILIKE '%수영장%'
ORDER BY created_at DESC LIMIT 50;
```

**보존 정책 (운영 가이드에 명시):**

- 적재량: 질의당 평균 ~30~50 rows (Phase 2 기준: 4 채널 × 10건 + RRF + final). queries 는 채널 수만큼이므로 7~10행/질의.
- 자정 시점 일일 회전 / 30일 보존 권장. 별도 partition 도입은 트래픽 증가 후 검토.
- PII 검토: `query_text` 에 사용자 원본 질의가 일부 포함될 수 있음 (refined_query). 운영 정책상 정제된 검색 텍스트라 PII 위험 낮으나, 본 가이드에 명시.

**Files:**
- Create: `docs/chat-search-persistence.md`

- [x] **Step 1: 가이드 작성** — 두 테이블 스키마 / kind+channel 의미 / 분석 쿼리 6종 / 보존 정책 / PII 메모

---

## Task 7: 관련 문서 일괄 갱신

### Step 1: `docs/ai-agent-design.md`

- [x] **1-1.** AgentState 표에 `search_channels: dict[str, ChannelData]` 행 추가 (kind/query/hits 한 묶음)
- [x] **1-2.** 2장 mermaid 다이어그램에 `search_persist_node` 추가 (answer → self_correction → search_persist → trace)
- [x] **1-3.** 8장 (오류 처리) 에 search_persist best-effort 정책 추가 (양쪽 테이블 동일 트랜잭션)
- [x] **1-4.** 9장 변경 이력에 본 작업(Phase 19 또는 별도 라벨) 추가 — "검색 source/results 적재. 두 테이블 + kind/channel 디스크리미네이터"

### Step 2: `2026-05-18-rrf-hybrid-search.md`

- [x] **2-1.** Task 6 (VectorAgent 재구성) 에 한 줄 추가:
  > "VectorAgent 는 4 채널(`vector_a/b/c`/`bm25`) + RRF 병합 결과를 `state.search_channels` 에 `ChannelData(kind, query, hits)` 형태로 각 채널 키별로 노출한다. `rrf` 채널의 query 는 source_channels + weights + k_constant 를 parameters 에 기록. `SearchChannel` / `SearchKind` 상수 사용."
- [x] **2-2.** File Map 에 `_search_channel_utils._to_hits` 헬퍼 사용 명시

### Step 3: `2026-05-18-triple-track-embedding-pipeline.md`

- [x] **3-1.** Task 6-2 (Phase 1 vector_search) 동작 메모에 한 줄 추가:
  > "Phase 1 vector_node 는 `search_channels` 에 `vector` (refined_query / 단일 경쟁 결과) + `bm25` (토큰 / 결과) + `final` (hydrated) 3개 채널을 `ChannelData` 형태로 채운다. Phase 2 RRF 도입 시 `vector` 단일이 `vector_a/b/c` + `rrf` 로 자연 대체된다 (스키마 변경 없음)."

### Step 4: 정합성 검증

- [x] **4-1.** grep으로 용어 일관성 확인

```bash
grep -rn "search_channels\|SearchChannel\.\|SearchKind\.\|ChannelData\|search_persist_node" \
  docs/ai-agent-design.md \
  docs/superpowers/plans/2026-05-1*.md \
  on-seoul-agent/docs/
```

---

## 완료 기준 체크리스트

- [x] `chat_search_queries` / `chat_search_results` 두 테이블이 모든 컬럼 + 인덱스 + 제약 + COMMENT를 갖는다
- [x] `kind` 컬럼은 CHECK 화이트리스트 (6종), `channel` 은 freeform 허용
- [x] 양쪽 테이블에서 동일 `(message_id, channel)` 의 `kind` 가 일치한다 (denormalize 일관성)
- [x] `AgentState.search_channels: dict[str, ChannelData]` 가 reducer 로 누적 병합된다
- [x] `kind_of(channel)` 헬퍼가 미등록 채널에 대해 `ValueError` 를 raise (typo 방지)
- [x] `sql_node` / `vector_node` (Phase 1) / `map_node` 가 각자 `ChannelData(kind, query, hits)` 를 채운다
- [x] `search_persist_node` 가 두 테이블을 동일 트랜잭션으로 INSERT 한다
- [x] hits 가 비어도 query 행은 기록된다 (0건 결과 분석)
- [x] best-effort: INSERT 실패 시 두 테이블 모두 rollback, `trace_node` 정상 호출
- [x] self-correction 재시도 시 `retry_prep_node` 가 `search_channels` 를 리셋한다 (UNIQUE 위반 방지)
- [x] FALLBACK intent 는 양쪽 테이블 모두 0행
- [x] freeform channel 명(예: 미등록 `future_channel`) 도 INSERT 통과 (channel CHECK 미적용)
- [x] `query_text` 가 `rrf` / `final` 채널에서 NULL 허용
- [x] 분석 쿼리 예시 6종 모두 실행 가능
- [x] 관련 문서 3개 모두 갱신

---

## 사전 확정 사항

1. **두 테이블 + 채널 키로 묶기**: `chat_search_queries` (input) + `chat_search_results` (output). `(message_id, channel)` 으로 join. 카디널리티가 다르므로 정규화.
2. **`kind` 컬럼은 CHECK 화이트리스트**: `sql`/`vector`/`bm25`/`rrf`/`map`/`final` 6종. kind 는 안정적이라 추가 빈도가 낮으므로 강 타입.
3. **`channel` 컬럼은 freeform**: 애플리케이션 측 `SearchChannel` 상수로 typo 방지. 채널 추가는 DB 마이그레이션 없이 가능 (Phase 2/3 자유 확장).
4. **`kind` 는 양쪽 테이블에 denormalize**: queries 에서 results 로 JOIN 없이도 분석 가능. 양쪽 일관성은 `search_persist_node` 가 보장 (kind_of(channel) 헬퍼로 단일 소스 결정).
5. **`ChannelData` 단위로 노드가 채움**: kind + query + hits 가 한 묶음. query 와 hits 의 짝을 잃지 않는다.
6. **`search_channels` 는 reducer 적용 필드**: 각 노드가 자기 채널만 담은 부분 dict 반환 → LangGraph가 `operator.or_` 로 누적 병합. 재시도 시 `retry_prep_node` 에서 명시 리셋.
7. **종단 노드 `search_persist_node` 는 `trace_node` 이전**: trace 가 final terminal이라는 기존 정책 유지.
8. **best-effort 트랜잭션 INSERT**: 두 테이블을 한 트랜잭션으로. 실패 시 둘 다 rollback + logger.warning + 다음 노드 진행.
9. **0건 결과여도 query 행은 기록**: "검색했는데 결과 없음" 도 recall 부족/stopword 과적용 진단의 신호.
10. **`final` 채널 = 사용자 노출 목록**: hydration·dedup·top_k 절단 후. 다른 채널과 의미가 명확히 분리됨.
11. **FALLBACK intent는 적재 안 함**: 검색이 일어나지 않았으므로 의미 없는 row.
12. **Phase 진화 매트릭스**: Phase 1 = sql/vector/bm25/map/final · Phase 2 = vector를 vector_a/b/c+rrf로 분해 · Phase 3 = hyde_vector 추가. **스키마는 고정** (kind 화이트리스트는 이미 모두 포함).

---

## 향후 단계 (별도 계획)

- **자동 회귀 분석 대시보드**: 채널별 일별 평균 결과 수, recall 부족 채널 알림.
- **Replay 도구**: 과거 message_id 의 final 결과를 재실행 후 비교 (가중치 변경 회귀 측정).
- **Partition by month** + 자동 purge: 30일 보존 정책 자동화.
- **PII 검토**: 현재는 service_id 만 저장하므로 PII 없음. 향후 사용자 메시지 일부를 meta에 저장할 계획이 생기면 별도 검토.
