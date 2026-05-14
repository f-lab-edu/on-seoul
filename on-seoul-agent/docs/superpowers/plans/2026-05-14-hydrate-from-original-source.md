# 원본 데이터 Hydration — Answer 컨텍스트 정확도 개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `service_embeddings`를 검색 인덱스로만 사용하고, 답변 생성 컨텍스트는 `public_service_reservations` 원본에서 직접 조회하도록 변경하여 `service_status`·`receipt_*_dt` 등 자주 바뀌는 필드를 임베딩 재생성 없이 최신값으로 반영한다.

**Architecture:** RRF 결합 후 추출한 `service_id` 리스트로 `public_service_reservations`를 다시 조회하는 hydration 도구(`tools/hydrate_services.py`)를 신설한다. `service_embeddings`(on_ai DB)와 `public_service_reservations`(on_data DB)는 서로 다른 DB이므로 cross-DB JOIN이 불가능하다. `VectorAgent.search()` 내부에서 RRF 결과 → service_id 추출 → data_session으로 hydrate 단계를 추가하고, hydration 누락분(원본 soft-delete 등)은 결과에서 제외한다. `AnswerAgent`는 평탄한 원본 컬럼만 받게 되어 metadata 언팩 분기를 제거한다.

**Tech Stack:** Python 3.13+, FastAPI, SQLAlchemy async (asyncpg), PostgreSQL 18 (on_data / on_ai), pytest + pytest-asyncio.

---

## 영향 받는 파일

- **신규**: `on-seoul-agent/tools/hydrate_services.py` — `service_id` 리스트 → 원본 행 조회 도구
- **신규**: `on-seoul-agent/tests/test_hydrate_services.py` — 신규 도구 단위 테스트
- **수정**: `on-seoul-agent/agents/vector_agent.py` — `search()` 시그니처에 `data_session` 추가, hydration 단계 삽입
- **수정**: `on-seoul-agent/agents/nodes.py` — `vector_node()`에서 `data_session` 전달
- **수정**: `on-seoul-agent/agents/answer_agent.py` — `_normalize()`의 metadata 언팩 분기 제거
- **수정**: `on-seoul-agent/tests/test_vector_agent.py` — hydration 단계 통합 테스트
- **수정**: `on-seoul-agent/tests/test_graph.py` — `vector_node` 시그니처 변경 대응
- **수정**: `on-seoul-agent/tests/test_answer_agent.py` — vector_results 형태 변경 반영
- **수정**: `on-seoul-agent/docs/hybrid-search-strategy.md` — "원본 hydration" 섹션 + 임베딩 대상 vs 표시 컬럼 명문화
- **수정**: `on-seoul-agent/docs/ai-agent-design.md` — VectorAgent 흐름과 DB 세션 라우팅 표 갱신
- **신규**: `on-seoul-agent/docs/tools/hydrate_services.md` — 도구 문서

---

## 사전 컨텍스트 (필독)

엔지니어가 이 계획만 보고 작업해야 하므로 다음을 미리 알아둔다.

### 현재 데이터 흐름

```
사용자 질문
  └─ VectorAgent.search(state, ai_session)
       1) LLM 질의 정제 → refined_query + filter 추출
       2) Gemini 임베딩
       3) vector_search() — ai_session, service_embeddings 조회
          반환: [{service_id, service_name, metadata, similarity}, ...]
       4) bm25_search() — ai_session, service_embeddings 조회
          반환: [{service_id, service_name, bm25_score}, ...]
       5) _rrf_merge() — service_id 기준 RRF 결합
          반환: [{service_id, service_name, metadata, rrf_score}, ...]
       6) state["vector_results"] = 결합 결과
  └─ AnswerAgent.answer(state)
       _collect_results() → _normalize() — metadata 언팩하여 LLM에 전달
```

### 문제

`service_embeddings.metadata`는 임베딩 시점 스냅샷이므로 `service_status`·`receipt_start_dt`·`receipt_end_dt`가 stale. 사용자가 "지금 접수 중인 시설"을 물으면 잘못된 정보가 답변에 들어간다.

### 해결 후 흐름

```
       5) _rrf_merge() — service_id 기준 RRF 결합
       6) [NEW] service_id 리스트 추출
       7) [NEW] hydrate_services(data_session, service_ids)
           → public_service_reservations에서 원본 행 조회
           → 입력 순서(RRF 순위) 유지하여 반환
           → soft-delete 또는 미존재 service_id는 누락
       8) [NEW] rrf_score를 hydrated 행에 병합
       9) state["vector_results"] = hydrated 결과 (sql_results와 동일 스키마)
```

### DB 세션 구분 (중요)

- `ai_session` — on_ai DB. `service_embeddings`, `chat_agent_traces`만 접근.
- `data_session` — on_data DB. `public_service_reservations`만 접근. `on_data_reader` 계정(SELECT 전용)으로 연결됨.

`vector_search`와 `bm25_search`는 ai_session, `sql_search`와 `map_search`는 data_session을 쓴다. **hydration은 data_session**을 사용해야 한다.

### 컬럼 매핑

`sql_search`의 반환 컬럼(`tools/sql_search.py:35-41`의 `_RESULT_COLUMNS`)을 그대로 따른다. hydration의 반환 컬럼도 정확히 동일해야 `vector_results`와 `sql_results`가 같은 스키마가 된다:

```
service_id, service_name, max_class_name, min_class_name,
area_name, place_name, service_status, payment_type,
service_url, receipt_start_dt, receipt_end_dt,
service_open_start_dt, service_open_end_dt,
coord_x, coord_y, target_info
```

이 외에 hydration에서만 추가되는 필드: `rrf_score`(검색 순위 보존용).

---

## Task 1: hydrate_services 도구 신설 (TDD)

**Files:**
- Create: `on-seoul-agent/tools/hydrate_services.py`
- Test: `on-seoul-agent/tests/test_hydrate_services.py`

### Step 1.1: 빈 입력 테스트 작성

- [ ] **Step 1.1.1: 실패 테스트 작성**

Create `on-seoul-agent/tests/test_hydrate_services.py`:

```python
"""tools/hydrate_services.py 단위 테스트.

Mock AsyncSession으로 입력 검증, bind 파라미터, 반환 순서를 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tools.hydrate_services import hydrate_services


def _make_session(rows: list[dict]) -> MagicMock:
    """fake AsyncSession — execute 호출 시 rows를 반환한다."""
    mock_result = MagicMock()
    if rows:
        mock_result.keys.return_value = list(rows[0].keys())
        mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    else:
        mock_result.keys.return_value = []
        mock_result.fetchall.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    return session


class TestHydrateEmptyInput:
    async def test_empty_service_ids_returns_empty_list(self):
        """service_id 리스트가 비어 있으면 DB 호출 없이 빈 리스트 반환."""
        session = _make_session([])
        result = await hydrate_services(session, [])
        assert result == []
        session.execute.assert_not_called()
```

- [ ] **Step 1.1.2: 테스트 실행으로 실패 확인**

Run: `cd on-seoul-agent && uv run pytest tests/test_hydrate_services.py -v`
Expected: ImportError — `tools.hydrate_services` 모듈 없음

- [ ] **Step 1.1.3: 최소 구현**

Create `on-seoul-agent/tools/hydrate_services.py`:

```python
"""Hydrate Services Tool — service_id 리스트로 public_service_reservations 원본 조회.

service_embeddings(on_ai DB)는 검색 인덱스로만 쓰고, 답변 컨텍스트는
public_service_reservations(on_data DB)의 최신 원본에서 직접 조회한다.
임베딩 시점의 stale metadata(service_status·receipt_*_dt 등)를 우회하기 위함.

SQL Injection 방지:
    service_id 값은 단일 ARRAY bind 파라미터로 전달한다.
    SQL 템플릿에 service_id 값을 직접 삽입하지 않는다.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# sql_search._RESULT_COLUMNS와 동일하게 유지하여
# vector_results와 sql_results의 스키마를 일치시킨다.
_RESULT_COLUMNS = """
    service_id, service_name, max_class_name, min_class_name,
    area_name, place_name, service_status, payment_type,
    service_url, receipt_start_dt, receipt_end_dt,
    service_open_start_dt, service_open_end_dt,
    coord_x, coord_y, target_info
"""


async def hydrate_services(
    session: AsyncSession,
    service_ids: list[str],
) -> list[dict]:
    """service_id 리스트로 public_service_reservations 원본 행을 조회한다.

    입력 순서(검색 순위)를 그대로 유지하여 반환한다.
    원본에 없거나 soft-delete된 service_id는 결과에서 제외한다.

    Parameters
    ----------
    session:
        on_data_reader 계정 AsyncSession (SELECT 전용).
    service_ids:
        조회 대상 service_id 리스트. 빈 리스트면 DB 호출 없이 빈 리스트 반환.

    Returns
    -------
    list[dict]
        _RESULT_COLUMNS 컬럼을 가진 딕셔너리 리스트.
        입력 순서를 보존하며, 원본 누락분은 제외된다.
    """
    if not service_ids:
        return []

    sql = text(f"""
        SELECT {_RESULT_COLUMNS}
        FROM public_service_reservations
        WHERE service_id = ANY(:service_ids)
          AND deleted_at IS NULL
    """)

    result = await session.execute(sql, {"service_ids": service_ids})
    keys = result.keys()
    rows = [dict(zip(keys, row)) for row in result.fetchall()]

    # 입력 순서를 보존: dict 인덱싱 후 service_ids 순서대로 재정렬.
    # 원본에 없는 service_id는 자동 제외된다.
    by_id = {r["service_id"]: r for r in rows}
    return [by_id[sid] for sid in service_ids if sid in by_id]
```

- [ ] **Step 1.1.4: 테스트 통과 확인**

Run: `uv run pytest tests/test_hydrate_services.py::TestHydrateEmptyInput -v`
Expected: PASS

- [ ] **Step 1.1.5: 커밋**

```bash
git add on-seoul-agent/tools/hydrate_services.py on-seoul-agent/tests/test_hydrate_services.py
git commit -m "feat(hydrate): service_id 리스트로 public_service_reservations 조회하는 hydrate_services 도구 신설"
```

### Step 1.2: 순서 보존 + 누락 처리 테스트

- [ ] **Step 1.2.1: 실패 테스트 추가**

Append to `tests/test_hydrate_services.py`:

```python
class TestHydrateOrderPreservation:
    async def test_returns_in_input_order(self):
        """결과는 service_ids 입력 순서(=검색 순위)를 유지한다."""
        # DB는 임의 순서로 반환 — 정렬은 도구 책임
        db_rows = [
            {"service_id": "S002", "service_name": "수영장", "max_class_name": "체육시설",
             "min_class_name": None, "area_name": "강남구", "place_name": None,
             "service_status": "접수중", "payment_type": None, "service_url": None,
             "receipt_start_dt": None, "receipt_end_dt": None,
             "service_open_start_dt": None, "service_open_end_dt": None,
             "coord_x": None, "coord_y": None, "target_info": None},
            {"service_id": "S001", "service_name": "테니스장", "max_class_name": "체육시설",
             "min_class_name": None, "area_name": "마포구", "place_name": None,
             "service_status": "접수중", "payment_type": None, "service_url": None,
             "receipt_start_dt": None, "receipt_end_dt": None,
             "service_open_start_dt": None, "service_open_end_dt": None,
             "coord_x": None, "coord_y": None, "target_info": None},
        ]
        session = _make_session(db_rows)
        result = await hydrate_services(session, ["S001", "S002"])
        assert [r["service_id"] for r in result] == ["S001", "S002"]


class TestHydrateMissingRows:
    async def test_missing_service_ids_excluded(self):
        """원본 테이블에 없거나 soft-delete된 service_id는 결과에서 제외된다."""
        db_rows = [
            {"service_id": "S001", "service_name": "테니스장", "max_class_name": "체육시설",
             "min_class_name": None, "area_name": "마포구", "place_name": None,
             "service_status": "접수중", "payment_type": None, "service_url": None,
             "receipt_start_dt": None, "receipt_end_dt": None,
             "service_open_start_dt": None, "service_open_end_dt": None,
             "coord_x": None, "coord_y": None, "target_info": None},
        ]
        session = _make_session(db_rows)
        # S002는 임베딩엔 있지만 원본엔 없는 케이스
        result = await hydrate_services(session, ["S001", "S002", "S003"])
        assert len(result) == 1
        assert result[0]["service_id"] == "S001"
```

- [ ] **Step 1.2.2: 실행으로 통과 확인 (이미 Step 1.1.3 구현이 이를 만족)**

Run: `uv run pytest tests/test_hydrate_services.py -v`
Expected: 모든 테스트 PASS

- [ ] **Step 1.2.3: 커밋**

```bash
git add on-seoul-agent/tests/test_hydrate_services.py
git commit -m "test(hydrate): 입력 순서 보존 및 누락 행 제외 검증"
```

### Step 1.3: SQL Injection 안전성 테스트

- [ ] **Step 1.3.1: 실패 테스트 추가**

Append to `tests/test_hydrate_services.py`:

```python
class TestHydrateSqlSafety:
    async def test_service_ids_passed_as_bind_param(self):
        """service_id 값은 bind 파라미터로 전달되고 SQL 템플릿에 직접 삽입되지 않는다."""
        malicious = "'; DROP TABLE public_service_reservations; --"
        session = _make_session([])
        await hydrate_services(session, [malicious])

        stmt, params = session.execute.call_args[0][0], session.execute.call_args[0][1]
        # bind 파라미터로 전달됨
        assert params["service_ids"] == [malicious]
        # SQL 템플릿 문자열에는 삽입되지 않음
        assert malicious not in str(stmt)

    async def test_deleted_at_filter_in_sql(self):
        """soft-delete 필터(deleted_at IS NULL)가 SQL에 포함된다."""
        session = _make_session([])
        await hydrate_services(session, ["S001"])
        stmt = session.execute.call_args[0][0]
        assert "deleted_at IS NULL" in str(stmt)
```

- [ ] **Step 1.3.2: 실행으로 통과 확인**

Run: `uv run pytest tests/test_hydrate_services.py -v`
Expected: 모든 테스트 PASS

- [ ] **Step 1.3.3: 커밋**

```bash
git add on-seoul-agent/tests/test_hydrate_services.py
git commit -m "test(hydrate): SQL injection 방지 및 soft-delete 필터 검증"
```

---

## Task 2: VectorAgent에 hydration 단계 통합

**Files:**
- Modify: `on-seoul-agent/agents/vector_agent.py`
- Modify: `on-seoul-agent/tests/test_vector_agent.py`

### Step 2.1: search() 시그니처 변경 — data_session 추가

- [ ] **Step 2.1.1: 회귀 테스트 작성 — data_session으로 hydrate 호출됨**

Append to `on-seoul-agent/tests/test_vector_agent.py` (적절한 import 구문은 파일 상단의 기존 import 패턴을 따른다):

```python
class TestVectorAgentHydration:
    """RRF 결과의 service_id로 public_service_reservations를 hydrate하는 흐름."""

    async def test_hydrate_called_with_rrf_service_ids(self):
        """RRF 결합 결과의 service_id 리스트가 hydrate_services에 전달된다."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agents.vector_agent import VectorAgent
        from schemas.state import AgentState

        # 정제 LLM 모킹
        agent = VectorAgent.__new__(VectorAgent)
        agent._refine_chain = MagicMock()
        refined = MagicMock()
        refined.refined_query = "강남 수영장"
        refined.max_class_name = None
        refined.area_name = None
        refined.service_status = None
        agent._refine_chain.ainvoke = AsyncMock(return_value=refined)

        # 임베딩 모킹
        agent._embeddings = MagicMock()
        agent._embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)

        ai_session = MagicMock()
        data_session = MagicMock()

        # vector_search, bm25_search, hydrate_services 모킹
        with (
            patch("agents.vector_agent.vector_search",
                  AsyncMock(return_value=[{"service_id": "S001", "service_name": "n", "metadata": {}, "similarity": 0.9}])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services",
                  AsyncMock(return_value=[{"service_id": "S001", "service_name": "수영장", "service_status": "접수중"}])) as mock_hydrate,
        ):
            state: AgentState = {
                "room_id": 1, "message_id": 1, "message": "강남 수영장",
                "title_needed": False, "intent": None, "lat": None, "lng": None,
                "refined_query": None, "sql_results": None, "vector_results": None,
                "map_results": None, "answer": None, "title": None, "trace": None,
                "error": None, "retry_count": 0,
            }
            result = await agent.search(state, ai_session, data_session)

            # hydrate_services가 data_session과 RRF 결과 service_id로 호출됨
            mock_hydrate.assert_awaited_once()
            call_args = mock_hydrate.await_args
            assert call_args[0][0] is data_session
            assert call_args[0][1] == ["S001"]

            # vector_results는 hydrated 원본 행
            assert result["vector_results"][0]["service_status"] == "접수중"
```

- [ ] **Step 2.1.2: 테스트 실행으로 실패 확인**

Run: `uv run pytest tests/test_vector_agent.py::TestVectorAgentHydration -v`
Expected: FAIL — `search()` 시그니처에 data_session 없음 / `hydrate_services` import 없음

- [ ] **Step 2.1.3: vector_agent.py 수정**

Modify `on-seoul-agent/agents/vector_agent.py`:

1. import 구문에 hydrate_services 추가 (기존 `from tools.bm25_search import bm25_search` 라인 인근):

```python
from tools.bm25_search import bm25_search
from tools.hydrate_services import hydrate_services
from tools.tokenizer import tokenize_query
from tools.vector_search import vector_search
```

2. `search()` 메서드 시그니처와 본문을 다음과 같이 변경 (기존 구현을 통째로 교체):

```python
    async def search(
        self,
        state: AgentState,
        ai_session: AsyncSession,
        data_session: AsyncSession,
    ) -> AgentState:
        """질의 정제 → 임베딩 → 하이브리드 검색 → RRF 결합 → 원본 hydration.

        ai_session   : service_embeddings(on_ai)에 대한 의미 검색·BM25 용도
        data_session : public_service_reservations(on_data) hydration 용도

        vector_results에는 hydration된 원본 행이 채워진다.
        스키마는 sql_results와 동일하며 추가로 rrf_score를 포함한다.
        임베딩 metadata는 stale일 수 있으므로 답변 컨텍스트로 사용하지 않는다.
        """
        refined: _RefinedQuery = await self._refine_chain.ainvoke(
            {"message": state["message"]}
        )

        query_vector = await self._embeddings.aembed_query(refined.refined_query)
        tokens = tokenize_query(refined.refined_query)

        try:
            vector_rows: list[dict] = await vector_search(
                ai_session,
                query_vector,
                max_class_name=refined.max_class_name,
                area_name=refined.area_name,
                service_status=refined.service_status,
            )
        except Exception:
            logger.warning("vector_search 실패, 빈 결과로 대체", exc_info=True)
            vector_rows = []

        bm25_tokens = [t for t in tokens if t not in _BM25_STOPWORDS]
        bm25_rows: list[dict] = []
        if bm25_tokens:
            try:
                bm25_rows = await bm25_search(bm25_tokens, ai_session)
            except Exception:
                logger.warning("bm25_search 실패, 빈 결과로 대체", exc_info=True)
        else:
            logger.debug("유효 BM25 토큰 없음 — 벡터 단독 검색으로 진행")

        merged = _rrf_merge(vector_rows, bm25_rows, top_k=_TOP_K)

        # RRF 결과의 service_id로 원본 hydration.
        # rrf_score를 보존하기 위해 service_id → rrf_score 매핑을 먼저 만든다.
        rrf_score_by_id: dict[str, float] = {
            r["service_id"]: r.get("rrf_score", 0.0) for r in merged
        }
        service_ids = list(rrf_score_by_id.keys())

        try:
            hydrated: list[dict] = await hydrate_services(data_session, service_ids)
        except Exception:
            logger.warning("hydrate_services 실패, RRF 결과를 그대로 사용", exc_info=True)
            hydrated = []

        # 원본 행에 rrf_score 병합. hydration 누락분은 자연스럽게 제외된다.
        for row in hydrated:
            row["rrf_score"] = rrf_score_by_id.get(row["service_id"], 0.0)

        return {
            **state,
            "refined_query": refined.refined_query,
            "vector_results": hydrated,
        }
```

- [ ] **Step 2.1.4: 테스트 통과 확인**

Run: `uv run pytest tests/test_vector_agent.py -v`
Expected: 새 테스트 PASS. 기존 테스트 중 `ai_session` 단일 인자를 넘기던 케이스는 다음 단계에서 수정한다 — 일단 새 테스트만 통과하면 OK.

- [ ] **Step 2.1.5: 기존 테스트 시그니처 마이그레이션**

`tests/test_vector_agent.py`의 기존 `agent.search(state, session)` 호출을 모두 `agent.search(state, ai_session, data_session)`으로 변경한다. 검색 도구를 patch하는 테스트는 `hydrate_services`도 함께 patch해야 한다.

기존 호출을 찾는다:

```bash
grep -n "\.search(.*session" tests/test_vector_agent.py
```

각 호출 사이트에서:
1. 함수 시작부에 `data_session = MagicMock()` (또는 기존 `session`을 `ai_session`으로 rename) 추가
2. `await agent.search(state, session)` → `await agent.search(state, ai_session, data_session)`
3. 검색 결과가 있는 케이스는 `patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=...))`로 hydration을 모킹

대표 패턴:

```python
# Before
ai_session = MagicMock()
with patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)):
    result = await agent.search(state, ai_session)

# After
ai_session = MagicMock()
data_session = MagicMock()
hydrated_rows = [
    {"service_id": r["service_id"], "service_name": "...", "service_status": "접수중"}
    for r in vector_rows
]
with (
    patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
    patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=hydrated_rows)),
):
    result = await agent.search(state, ai_session, data_session)
```

- [ ] **Step 2.1.6: 전체 vector_agent 테스트 통과 확인**

Run: `uv run pytest tests/test_vector_agent.py -v`
Expected: 모든 테스트 PASS

- [ ] **Step 2.1.7: 커밋**

```bash
git add on-seoul-agent/agents/vector_agent.py on-seoul-agent/tests/test_vector_agent.py
git commit -m "feat(vector_agent): RRF 결과를 public_service_reservations에서 hydrate

service_embeddings의 stale metadata 대신 원본 행을 답변 컨텍스트로 사용.
search() 시그니처에 data_session 추가, 기존 ai_session은 검색용으로 유지."
```

---

## Task 3: vector_node에 data_session 전달

**Files:**
- Modify: `on-seoul-agent/agents/nodes.py`
- Modify: `on-seoul-agent/tests/test_graph.py`

### Step 3.1: vector_node 수정

- [ ] **Step 3.1.1: nodes.py 수정**

`on-seoul-agent/agents/nodes.py`의 `vector_node` 메서드를 다음으로 교체 (현재 line 122-135 인근):

```python
    async def vector_node(self, state: AgentState) -> dict[str, Any]:
        """VectorAgent.search() 호출 — vector_results, refined_query 설정.

        VectorAgent는 임베딩 검색(ai_session)과 원본 hydration(data_session)을
        모두 수행하므로 두 세션을 모두 전달한다.
        """
        assert self.ai_session is not None
        assert self.data_session is not None
        try:
            new_state = await self._vector.search(
                state, self.ai_session, self.data_session
            )
            self.node_path.append("vector_node")
            return {
                "vector_results": new_state.get("vector_results"),
                "refined_query": new_state.get("refined_query"),
            }
        except Exception as exc:
            logger.exception("vector_node 실행 오류")
            self.node_path.append("vector_error")
            return {"error": str(exc)}
```

- [ ] **Step 3.1.2: 그래프 테스트 실행으로 회귀 확인**

Run: `uv run pytest tests/test_graph.py -v`
Expected: vector 관련 테스트가 실패할 수 있다. 다음 단계에서 수정.

- [ ] **Step 3.1.3: test_graph.py — VectorAgent 모킹 수정**

`tests/test_graph.py`에서 `VectorAgent` 모킹을 사용하는 테스트는 `search()`가 이제 3-인자(`state, ai_session, data_session`)를 받는다는 점을 반영한다. AsyncMock을 사용한 경우 인자 개수만 늘어나므로 대부분 자동으로 통과하지만, side_effect으로 함수를 지정한 경우는 시그니처를 맞춰야 한다.

`grep -n "_vector\|VectorAgent\|vector_agent" tests/test_graph.py`로 호출 사이트를 찾고, side_effect 함수가 있으면 다음과 같이 인자를 추가한다:

```python
# Before
async def _fake_search(state, session):
    return {**state, "vector_results": [...]}

# After
async def _fake_search(state, ai_session, data_session):
    return {**state, "vector_results": [...]}
```

- [ ] **Step 3.1.4: 그래프 전체 테스트 통과 확인**

Run: `uv run pytest tests/test_graph.py -v`
Expected: 모든 테스트 PASS

- [ ] **Step 3.1.5: 커밋**

```bash
git add on-seoul-agent/agents/nodes.py on-seoul-agent/tests/test_graph.py
git commit -m "feat(nodes): vector_node에서 data_session도 함께 전달

VectorAgent가 RRF 후 public_service_reservations를 hydrate하므로
data_session 주입이 필요하다."
```

---

## Task 4: AnswerAgent._normalize 단순화

**Files:**
- Modify: `on-seoul-agent/agents/answer_agent.py`
- Modify: `on-seoul-agent/tests/test_answer_agent.py`

### 배경

이전엔 `vector_results`의 각 행이 `{service_id, service_name, metadata: {...}, rrf_score}` 형태였고 `_normalize`가 `metadata` JSON을 언팩해야 했다. 이제 `vector_results`는 `sql_results`와 동일한 평탄한 스키마이므로 metadata 언팩 분기는 죽은 코드가 된다.

`map_results`는 여전히 GeoJSON에서 `properties`를 언팩하지만, 일반 dict 키로 접근 가능하므로 metadata 언팩 분기 자체는 제거 가능하다.

### Step 4.1: 테스트로 새 동작 명시

- [ ] **Step 4.1.1: 회귀 테스트 추가**

Append to `tests/test_answer_agent.py`:

```python
class TestAnswerAgentVectorResultsFlatSchema:
    """vector_results가 sql_results와 동일한 평탄 스키마인 경우 _normalize 동작."""

    def test_flat_vector_row_normalized_without_metadata_unpack(self):
        """metadata 키가 없는 평탄 행에서도 모든 필드가 추출된다."""
        from agents.answer_agent import AnswerAgent

        agent = AnswerAgent.__new__(AnswerAgent)
        flat_row = {
            "service_id": "S001",
            "service_name": "마포 수영장",
            "area_name": "마포구",
            "place_name": "마포 스포츠센터",
            "service_status": "접수중",
            "receipt_start_dt": "2026-05-01",
            "receipt_end_dt": "2026-05-31",
            "service_url": "https://example.com/s001",
            "rrf_score": 0.123,
        }
        normalized = AnswerAgent._normalize(flat_row)
        assert normalized["service_id"] == "S001"
        assert normalized["service_name"] == "마포 수영장"
        assert normalized["area_name"] == "마포구"
        assert normalized["service_status"] == "접수중"
        assert normalized["service_url"] == "https://example.com/s001"

    def test_missing_service_url_uses_fallback(self):
        """service_url이 없으면 yeyak fallback 링크가 사용된다."""
        from agents.answer_agent import AnswerAgent, _FALLBACK_URL

        normalized = AnswerAgent._normalize({"service_id": "S002"})
        assert normalized["service_url"] == _FALLBACK_URL
```

- [ ] **Step 4.1.2: 테스트 실행으로 통과 확인 (현재 구현에서도 통과해야 함)**

Run: `uv run pytest tests/test_answer_agent.py::TestAnswerAgentVectorResultsFlatSchema -v`
Expected: PASS — 현재 `_normalize`는 평탄 키도 지원하므로 통과.

- [ ] **Step 4.1.3: _normalize 단순화**

`on-seoul-agent/agents/answer_agent.py`의 `_normalize` 메서드를 다음으로 교체:

```python
    @staticmethod
    def _normalize(row: dict) -> dict:
        """카드 렌더링에 필요한 필드만 추출하고 fallback URL을 보정한다.

        sql_results와 vector_results는 모두 public_service_reservations 원본 컬럼을
        평탄 dict로 가지므로 metadata 언팩 분기는 더 이상 필요하지 않다.
        map_results는 GeoJSON Feature의 properties dict를 그대로 받는다.
        """
        service_url = row.get("service_url") or _FALLBACK_URL

        return {
            "service_id": row.get("service_id"),
            "service_name": row.get("service_name"),
            "area_name": row.get("area_name"),
            "place_name": row.get("place_name"),
            "service_status": row.get("service_status"),
            "receipt_start_dt": row.get("receipt_start_dt"),
            "receipt_end_dt": row.get("receipt_end_dt"),
            "service_url": service_url,
        }
```

또한 `_normalize` 위쪽의 `import json` 사용처가 더 이상 필요 없을 수 있다 — `answer()` 메서드의 `json.dumps(results)`는 유지하므로 import는 남겨둔다.

- [ ] **Step 4.1.4: 전체 answer_agent 테스트 통과 확인**

Run: `uv run pytest tests/test_answer_agent.py -v`
Expected: 모든 테스트 PASS

기존에 metadata 언팩 경로를 검증하던 테스트가 있다면 이제 답변 컨텍스트는 평탄 행만 들어오므로 해당 테스트는 새 케이스(`test_flat_vector_row_normalized_without_metadata_unpack`)로 대체되었다. 만약 기존 테스트가 깨지면 그 테스트는 stale 동작을 검증하던 것이므로 제거한다.

- [ ] **Step 4.1.5: 커밋**

```bash
git add on-seoul-agent/agents/answer_agent.py on-seoul-agent/tests/test_answer_agent.py
git commit -m "refactor(answer_agent): _normalize의 metadata 언팩 분기 제거

vector_results가 hydration 후 sql_results와 동일한 평탄 스키마이므로
metadata JSON 언팩 코드는 dead code가 되어 제거."
```

---

## Task 5: 회귀 테스트 — 정확도 보장 시나리오

**Files:**
- Modify: `on-seoul-agent/tests/test_vector_agent.py`

### Step 5.1: 임베딩 metadata와 원본 데이터가 다른 경우

- [ ] **Step 5.1.1: stale metadata 우회 시나리오 테스트**

Append to `tests/test_vector_agent.py`:

```python
class TestHydrationDataFreshness:
    """임베딩 시점의 stale metadata 대신 원본 최신 값이 답변 컨텍스트로 들어가는지 검증."""

    async def test_hydrated_status_overrides_stale_embedding_metadata(self):
        """임베딩 metadata는 '접수마감', 원본은 '접수중'이면 hydrated 값('접수중')이 반환된다."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agents.vector_agent import VectorAgent
        from schemas.state import AgentState

        agent = VectorAgent.__new__(VectorAgent)
        refined = MagicMock()
        refined.refined_query = "수영장"
        refined.max_class_name = None
        refined.area_name = None
        refined.service_status = None
        agent._refine_chain = MagicMock()
        agent._refine_chain.ainvoke = AsyncMock(return_value=refined)
        agent._embeddings = MagicMock()
        agent._embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)

        # 임베딩 metadata는 stale: '예약마감'
        stale_vector_rows = [{
            "service_id": "S001",
            "service_name": "마포 수영장",
            "metadata": {"service_status": "예약마감"},
            "similarity": 0.9,
        }]
        # 원본은 최신: '접수중'
        fresh_hydrated = [{
            "service_id": "S001",
            "service_name": "마포 수영장",
            "service_status": "접수중",
        }]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=stale_vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=fresh_hydrated)),
        ):
            state: AgentState = {
                "room_id": 1, "message_id": 1, "message": "수영장",
                "title_needed": False, "intent": None, "lat": None, "lng": None,
                "refined_query": None, "sql_results": None, "vector_results": None,
                "map_results": None, "answer": None, "title": None, "trace": None,
                "error": None, "retry_count": 0,
            }
            result = await agent.search(state, MagicMock(), MagicMock())

            # 답변 컨텍스트에는 최신 '접수중'만 들어간다
            assert result["vector_results"][0]["service_status"] == "접수중"
            # stale metadata는 노출되지 않는다
            assert "metadata" not in result["vector_results"][0] or \
                   result["vector_results"][0].get("metadata") is None
```

- [ ] **Step 5.1.2: 테스트 통과 확인**

Run: `uv run pytest tests/test_vector_agent.py::TestHydrationDataFreshness -v`
Expected: PASS

- [ ] **Step 5.1.3: 커밋**

```bash
git add on-seoul-agent/tests/test_vector_agent.py
git commit -m "test(vector_agent): hydration이 stale 임베딩 metadata를 최신 원본 값으로 대체함을 검증"
```

### Step 5.2: 원본 누락 시나리오 — soft-delete 또는 미존재

- [ ] **Step 5.2.1: 누락 행 제외 테스트**

Append to `tests/test_vector_agent.py`:

```python
    async def test_hydration_drops_rows_missing_in_source(self):
        """임베딩에는 있지만 원본 테이블에 없는 service_id는 vector_results에서 제외된다."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agents.vector_agent import VectorAgent
        from schemas.state import AgentState

        agent = VectorAgent.__new__(VectorAgent)
        refined = MagicMock()
        refined.refined_query = "수영장"
        refined.max_class_name = None
        refined.area_name = None
        refined.service_status = None
        agent._refine_chain = MagicMock()
        agent._refine_chain.ainvoke = AsyncMock(return_value=refined)
        agent._embeddings = MagicMock()
        agent._embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)

        # 검색 결과: S001과 S002 두 건
        vector_rows = [
            {"service_id": "S001", "service_name": "n1", "metadata": {}, "similarity": 0.9},
            {"service_id": "S002", "service_name": "n2", "metadata": {}, "similarity": 0.8},
        ]
        # 원본에는 S001만 존재 (S002는 soft-delete됨)
        hydrated = [
            {"service_id": "S001", "service_name": "마포 수영장", "service_status": "접수중"},
        ]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=hydrated)),
        ):
            state: AgentState = {
                "room_id": 1, "message_id": 1, "message": "수영장",
                "title_needed": False, "intent": None, "lat": None, "lng": None,
                "refined_query": None, "sql_results": None, "vector_results": None,
                "map_results": None, "answer": None, "title": None, "trace": None,
                "error": None, "retry_count": 0,
            }
            result = await agent.search(state, MagicMock(), MagicMock())

            ids = [r["service_id"] for r in result["vector_results"]]
            assert ids == ["S001"]
```

- [ ] **Step 5.2.2: 테스트 통과 확인**

Run: `uv run pytest tests/test_vector_agent.py::TestHydrationDataFreshness -v`
Expected: PASS

- [ ] **Step 5.2.3: 커밋**

```bash
git add on-seoul-agent/tests/test_vector_agent.py
git commit -m "test(vector_agent): 원본에 없는 service_id는 vector_results에서 제외됨을 검증"
```

### Step 5.3: hydrate 실패 시 graceful degradation

- [ ] **Step 5.3.1: hydrate_services 예외 시 빈 결과 테스트**

Append to `tests/test_vector_agent.py`:

```python
    async def test_hydrate_failure_falls_back_to_empty_results(self):
        """hydrate_services가 예외를 던지면 vector_results가 빈 리스트가 된다.

        검색 자체는 성공했으나 hydration이 실패한 경우, stale metadata로 답변하는 것보다
        결과 없음을 안내하는 편이 안전하다. Answer Agent의 _self_correction_edge가
        '결과 없음'을 빈 답변으로 변환하여 재시도 로직이 발동할 수 있다.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from agents.vector_agent import VectorAgent
        from schemas.state import AgentState

        agent = VectorAgent.__new__(VectorAgent)
        refined = MagicMock()
        refined.refined_query = "수영장"
        refined.max_class_name = None
        refined.area_name = None
        refined.service_status = None
        agent._refine_chain = MagicMock()
        agent._refine_chain.ainvoke = AsyncMock(return_value=refined)
        agent._embeddings = MagicMock()
        agent._embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)

        vector_rows = [{"service_id": "S001", "service_name": "n", "metadata": {}, "similarity": 0.9}]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services",
                  AsyncMock(side_effect=RuntimeError("DB down"))),
        ):
            state: AgentState = {
                "room_id": 1, "message_id": 1, "message": "수영장",
                "title_needed": False, "intent": None, "lat": None, "lng": None,
                "refined_query": None, "sql_results": None, "vector_results": None,
                "map_results": None, "answer": None, "title": None, "trace": None,
                "error": None, "retry_count": 0,
            }
            result = await agent.search(state, MagicMock(), MagicMock())

            assert result["vector_results"] == []
```

- [ ] **Step 5.3.2: 테스트 통과 확인**

Run: `uv run pytest tests/test_vector_agent.py::TestHydrationDataFreshness -v`
Expected: PASS — Task 2.1.3 구현에서 hydrate 예외는 빈 리스트로 처리하도록 작성했음.

- [ ] **Step 5.3.3: 커밋**

```bash
git add on-seoul-agent/tests/test_vector_agent.py
git commit -m "test(vector_agent): hydrate 실패 시 빈 결과로 fallback함을 검증"
```

---

## Task 6: 문서 업데이트

**Files:**
- Modify: `on-seoul-agent/docs/hybrid-search-strategy.md`
- Modify: `on-seoul-agent/docs/ai-agent-design.md`
- Create: `on-seoul-agent/docs/tools/hydrate_services.md`

### Step 6.1: hybrid-search-strategy.md — 원본 hydration 섹션 추가

- [ ] **Step 6.1.1: 섹션 추가**

`on-seoul-agent/docs/hybrid-search-strategy.md`의 "## 검색 쿼리 구조" 섹션 직전(또는 RRF 섹션 직후 적절한 위치)에 다음 섹션을 추가한다:

````markdown
## 원본 데이터 Hydration

`service_embeddings`는 의미 검색의 인덱스로만 사용한다. 답변 생성에 필요한 컨텍스트는 RRF 결합 후 `service_id`를 키로 `public_service_reservations` 원본 테이블에서 다시 조회하여 채운다. 임베딩 시점에 스냅샷된 `service_status`·`receipt_start_dt`·`receipt_end_dt` 등이 stale 상태로 답변에 들어가는 것을 막기 위함이다.

### 컬럼 책임 분리

| 용도 | 출처 | 컬럼 예시 |
|---|---|---|
| 의미 검색 (임베딩 입력) | `service_embeddings.embedding` | `service_name`, `area_name`, `max_class_name`, `target_info` (가공된 텍스트) |
| 키워드 검색 (BM25) | `service_embeddings.service_name`, `service_embeddings.metadata` | `service_name`, `metadata` JSONB |
| 답변 표시 (Hydration) | `public_service_reservations` | 모든 표시 컬럼 — 특히 자주 바뀌는 `service_status`, `receipt_*_dt`, `service_url` |

**원칙**: 임베딩 metadata는 검색 후처리(post-filter 등)에만 쓰고, 사용자에게 노출되는 표시 값은 항상 원본 테이블에서 가져온다.

### Hydration 흐름

```
VectorAgent.search()
  1. 질의 정제 + 임베딩
  2. vector_search (ai_session)
  3. bm25_search    (ai_session)
  4. _rrf_merge → service_id 리스트
  5. hydrate_services(data_session, service_ids)
     - public_service_reservations에서 WHERE service_id = ANY(:service_ids) AND deleted_at IS NULL
     - 입력 순서(RRF 순위) 유지
     - 원본 누락분 자동 제외
  6. rrf_score 병합 후 vector_results에 할당
```

### 임베딩 ↔ 원본 동기화 정책

수집 스케줄러는 매일 1회 `service_change_log`에 변경분을 기록한다. 임베딩 재생성 트리거는 다음과 같다:

| 변경된 필드 | 임베딩 재생성 | 사유 |
|---|---|---|
| `service_name`, `area_name`, `max_class_name`, `target_info` | **필요** | 의미 공간 자체가 달라짐 |
| `service_status`, `receipt_*_dt`, `service_open_*_dt`, `service_url`, `payment_type` | 불필요 | Hydration이 매 답변마다 최신 값을 끌어오므로 임베딩 갱신 불필요 |

`scripts/embed_metadata.py`는 `--incremental` 모드에서 `service_change_log`를 읽어 의미 컬럼이 변경된 service_id만 재임베딩한다. 이로 인해 임베딩 비용을 최소화하면서도 답변 정확도는 유지된다.

### 누락·실패 처리

| 상황 | 처리 |
|---|---|
| 임베딩엔 있지만 원본 테이블에 service_id 미존재 | 결과에서 제외 (검색 결과 누락으로 인지) |
| 원본 행이 soft-delete (`deleted_at IS NOT NULL`) | 결과에서 제외 |
| `hydrate_services` 자체가 예외 (DB 다운 등) | `vector_results = []`로 fallback. stale metadata로 답변하지 않는다. Self-correction이 빈 답변을 재시도로 전환할 수 있다. |
````

- [ ] **Step 6.1.2: 커밋**

```bash
git add on-seoul-agent/docs/hybrid-search-strategy.md
git commit -m "docs(hybrid-search): 원본 hydration 섹션 추가 — 임베딩 vs 원본 책임 분리 명문화"
```

### Step 6.2: ai-agent-design.md — VectorAgent 흐름 갱신

- [ ] **Step 6.2.1: §3-3 Vector Agent 섹션 수정**

`docs/ai-agent-design.md`의 §3-3 "Vector Agent — 의미 기반 검색 (BM25 + vector 하이브리드)" 본문의 단계 목록을 다음으로 교체:

```markdown
1. **질의 정제** — LLM으로 사용자 질의를 벡터 검색용 문장으로 정제하고, post-filter용 파라미터(`max_class_name`, `area_name`, `service_status`)를 함께 추출한다.
2. **이중 경로 실행 (ai_session)**
   - **BM25 경로**: `llm/tokenizer.py` (Lindera KoDic + `DOMAIN_TOKENS`)로 토큰화 → `tools/bm25_search` 호출 → `(service_id, bm25_score)` 목록
   - **Vector 경로**: Gemini 임베딩 → `tools/vector_search` 호출 (post-filter 적용)
3. **RRF 결합** — 두 결과의 순위를 Reciprocal Rank Fusion으로 결합한다.
4. **원본 Hydration (data_session)** — RRF 결과의 `service_id`로 `tools/hydrate_services`를 호출하여 `public_service_reservations` 최신 원본 행을 가져온다. 임베딩 metadata의 stale 필드(`service_status`·`receipt_*_dt` 등) 우회 목적. 원본 누락 또는 hydration 실패 시 해당 행은 결과에서 제외된다.
5. `vector_results`에 hydrated 결과 + `rrf_score`를 저장한다. 스키마는 `sql_results`와 동일.
```

또한 §3-3의 입출력 표 옆에 다음 메모를 추가:

```markdown
> Phase 18부터 VectorAgent.search()는 `ai_session`(검색) + `data_session`(hydration) 두 세션을 모두 받는다.
```

- [ ] **Step 6.2.2: §6 DB 세션 라우팅 표 갱신**

기존 vector_node 행 1개를 다음 2개 행으로 교체:

```markdown
| vector_node → `vector_search` / `bm25_search` | `ai_session` | `on_ai` | `service_embeddings` |
| vector_node → `hydrate_services` | `data_session` | `on_data` | `public_service_reservations` |
```

- [ ] **Step 6.2.3: §4 Tools 섹션에 hydrate_services 추가**

§4-3 `bm25_search`와 §4-4 `map_search` 사이에 다음 절을 삽입:

```markdown
### 4-3.5. `hydrate_services` — 검색 결과 원본 hydration (Phase 18 신설)

RRF 결합 후 추출한 `service_id` 리스트로 `public_service_reservations`에서 최신 원본 행을 조회한다. 임베딩 metadata의 stale 필드를 우회하여 답변 정확도를 보장한다.

| 파라미터 | 설명 |
|---|---|
| `session` | `on_data_reader` 계정 AsyncSession (SELECT 전용) |
| `service_ids` | 검색 순위 순서대로 정렬된 `service_id` 리스트 |

반환: 입력 순서를 유지한 원본 행 리스트. 원본에 없거나 soft-delete된 service_id는 자동 제외. 스키마는 `sql_search`와 동일.
```

- [ ] **Step 6.2.4: §9 변경 이력 표에 행 추가**

```markdown
| Phase 18 | 원본 hydration 도입 | `tools/hydrate_services` 신설, VectorAgent에서 RRF 후 `public_service_reservations` 조회. 답변 컨텍스트가 항상 최신 원본 값을 사용하도록 변경. `AnswerAgent._normalize`의 metadata 언팩 분기 제거. |
```

- [ ] **Step 6.2.5: 커밋**

```bash
git add on-seoul-agent/docs/ai-agent-design.md
git commit -m "docs(ai-agent-design): VectorAgent hydration 단계 및 hydrate_services 도구 반영"
```

### Step 6.3: hydrate_services 도구 문서 신설

- [ ] **Step 6.3.1: 도구 문서 작성**

Create `on-seoul-agent/docs/tools/hydrate_services.md`:

```markdown
# hydrate_services

`public_service_reservations` 테이블에서 `service_id` 리스트에 해당하는 원본 행을 조회합니다. 검색 결과의 순위를 유지하고, 원본 누락분(soft-delete 또는 미존재)은 자동으로 제외합니다.

## 시그니처

```python
async def hydrate_services(
    session: AsyncSession,
    service_ids: list[str],
) -> list[dict]:
```

## 파라미터

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `session` | `AsyncSession` | `on_data_reader` 계정 세션 (SELECT 전용) |
| `service_ids` | `list[str]` | 검색 순위 순서로 정렬된 service_id 리스트. 빈 리스트면 DB 호출 없이 빈 리스트 반환 |

## 반환값

`list[dict]` — `sql_search`와 동일한 컬럼 셋:

`service_id`, `service_name`, `max_class_name`, `min_class_name`,
`area_name`, `place_name`, `service_status`, `payment_type`,
`service_url`, `receipt_start_dt`, `receipt_end_dt`,
`service_open_start_dt`, `service_open_end_dt`, `coord_x`, `coord_y`,
`target_info`

입력 `service_ids` 순서를 보존한다. 원본에 없는 service_id는 결과에서 제외된다.

## 안전성

- service_id 값은 단일 `ARRAY` bind 파라미터(`:service_ids`)로 전달된다 (SQL Injection 방지).
- `deleted_at IS NULL` 필터로 soft-delete된 행은 제외된다.

## 사용 예

```python
from tools.hydrate_services import hydrate_services

ranked_ids = ["S004", "S001", "S009"]  # RRF 순위
hydrated = await hydrate_services(data_session, ranked_ids)
# hydrated[0]["service_id"] == "S004", hydrated[0]["service_status"] = 최신 원본 값
```
```

- [ ] **Step 6.3.2: 커밋**

```bash
git add on-seoul-agent/docs/tools/hydrate_services.md
git commit -m "docs(tools): hydrate_services 도구 문서 신설"
```

---

## Task 7: 통합 회귀 — 전체 그래프 흐름 검증

**Files:**
- Modify: `on-seoul-agent/tests/test_graph.py` (또는 통합 테스트가 있다면 그쪽)

### Step 7.1: vector intent 경로의 end-to-end 검증

- [ ] **Step 7.1.1: 통합 테스트 추가**

Append to `tests/test_graph.py`:

```python
class TestGraphVectorHydrationE2E:
    async def test_vector_intent_uses_hydrated_results(self):
        """VECTOR_SEARCH 인텐트가 hydrated 원본 값을 answer 입력으로 사용한다."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agents.graph import AgentGraph
        from schemas.state import IntentType

        # Router는 VECTOR_SEARCH 반환
        router = _router(IntentType.VECTOR_SEARCH)

        # Vector 검색 결과 — stale metadata
        vector_rows = [{
            "service_id": "S001",
            "service_name": "마포 수영장",
            "metadata": {"service_status": "예약마감"},
            "similarity": 0.9,
        }]
        # Hydration — 최신 '접수중'
        hydrated_rows = [{
            "service_id": "S001",
            "service_name": "마포 수영장",
            "area_name": "마포구",
            "service_status": "접수중",
            "service_url": "https://example.com",
        }]

        # Answer Agent는 입력 컨텍스트에 '접수중'이 들어있는지 검증
        captured: dict = {}

        async def _capture_answer(state):
            results = AnswerAgent.__new__(AnswerAgent)._collect_results.__func__(
                AnswerAgent.__new__(AnswerAgent), state
            )
            captured["results"] = results
            return {**state, "answer": "마포 수영장 접수 중입니다."}

        answer_agent = MagicMock()
        answer_agent.answer = AsyncMock(side_effect=_capture_answer)

        data_session = MagicMock()
        ai_session = _ai_session()

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=hydrated_rows)),
        ):
            graph = AgentGraph(router=router, answer_agent=answer_agent)
            result = await graph.run(
                _state(message="마포 수영장"),
                data_session=data_session,
                ai_session=ai_session,
            )

        # 답변 컨텍스트에 hydrated '접수중'이 들어가야 한다
        statuses = [r["service_status"] for r in captured["results"]]
        assert "접수중" in statuses
        assert "예약마감" not in statuses
```

> 참고: 이 테스트는 `_router`, `_state`, `_ai_session` 등 기존 헬퍼와 `AnswerAgent` import를 사용한다. 파일 상단 import에 `from agents.answer_agent import AnswerAgent`를 추가하라.

- [ ] **Step 7.1.2: 테스트 통과 확인**

Run: `uv run pytest tests/test_graph.py::TestGraphVectorHydrationE2E -v`
Expected: PASS

- [ ] **Step 7.1.3: 전체 회귀 실행**

Run: `cd on-seoul-agent && uv run pytest -q`
Expected: 모든 테스트 PASS. 실패가 있다면 해당 테스트의 vector 관련 모킹을 hydration 포함하도록 마이그레이션 (Task 2.1.5 패턴).

- [ ] **Step 7.1.4: 커밋**

```bash
git add on-seoul-agent/tests/test_graph.py
git commit -m "test(graph): VECTOR_SEARCH 경로가 hydrated 원본 값을 답변 컨텍스트로 사용함을 검증"
```

---

## Task 8: scripts/embed_metadata.py — 증분 재임베딩 가이드 코멘트

**Files:**
- Modify: `on-seoul-agent/scripts/embed_metadata.py`

이 작업은 **코드 변경 없이** 주석/문서화만 추가한다. 실제 `--incremental` 모드에서 어떤 컬럼 변경이 재임베딩을 트리거하는지 명문화하기 위함이다.

### Step 8.1: 임베딩 대상 컬럼 명시 주석

- [ ] **Step 8.1.1: 파일 상단 docstring에 다음 블록 추가**

`scripts/embed_metadata.py`의 모듈 docstring 마지막 부분에 다음을 추가한다:

```python
"""
...

임베딩 대상 vs 표시 컬럼 (Phase 18):
    임베딩 입력에 포함되는 의미 컬럼이 변경된 경우에만 재임베딩한다.
    표시 전용 컬럼은 매 답변마다 tools/hydrate_services가 원본에서 가져오므로
    임베딩을 갱신할 필요가 없다.

    의미 컬럼 (변경 시 재임베딩):
        service_name, area_name, max_class_name, min_class_name, target_info

    표시 전용 컬럼 (변경해도 재임베딩 불필요):
        service_status, receipt_start_dt, receipt_end_dt,
        service_open_start_dt, service_open_end_dt, service_url, payment_type,
        coord_x, coord_y, place_name
"""
```

기존 docstring을 그대로 두고 마지막에 위 블록을 append한다.

- [ ] **Step 8.1.2: 커밋**

```bash
git add on-seoul-agent/scripts/embed_metadata.py
git commit -m "docs(embed_metadata): 임베딩 대상 컬럼 vs 표시 전용 컬럼 명문화

hydration 도입으로 표시 전용 컬럼 변경은 임베딩 재생성을 트리거하지 않음을
docstring으로 명시."
```

---

## 자기 검토 결과 (Self-Review)

### 1. Spec 커버리지

| 원본 체크 | 대응 태스크 |
|---|---|
| Search Agent 흐름 수정 — service_id 추출 + JOIN | Task 1 (hydrate_services 도구), Task 2 (vector_agent 통합) |
| Answer Agent 컨텍스트 입력 교체 | Task 2 (vector_results = hydrated), Task 4 (_normalize 단순화) |
| 임베딩 ↔ 원본 동기화 정책 정리 | Task 6.1 (hybrid-search-strategy 정책 섹션), Task 8 (embed_metadata 주석) |
| 회귀 테스트 (벡터 매칭 실패 / 원본 누락) | Task 5.1 (stale 우회), 5.2 (누락 제외), 5.3 (hydrate 실패 fallback), Task 7 (E2E) |

### 2. Placeholder 스캔

`TBD`, `TODO`, `implement later`, `fill in details`, "appropriate error handling" 등의 표현 없음. 각 코드 step은 완전한 코드 블록을 포함한다.

### 3. 타입 일관성

- `service_ids: list[str]` — Task 1, 2, 6 모두 일치.
- `_RESULT_COLUMNS` 셋이 `sql_search`와 `hydrate_services`에서 동일하게 유지됨.
- `vector_results` 스키마: hydrated 평탄 dict + `rrf_score` — Task 2 (정의), Task 4 (소비), Task 5 (검증), Task 7 (E2E)에서 일관됨.
- `VectorAgent.search()` 시그니처 `(state, ai_session, data_session)` — Task 2 (정의), Task 3 (호출), Task 5/7 (테스트)에서 일관됨.

---

**계획 완료.** 저장 위치: `on-seoul-agent/docs/superpowers/plans/2026-05-14-hydrate-from-original-source.md`

## 두 가지 실행 옵션

**1. Subagent-Driven (권장)** — 태스크당 새 subagent가 작업하고, 태스크 사이에 리뷰 체크포인트를 둔다. 빠른 반복.

**2. Inline Execution** — 현재 세션에서 `executing-plans`로 일괄 실행하고 단계별 체크포인트만 둔다.

어느 방식으로 진행할까?
