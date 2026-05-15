# Answer Cache & Conversation Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SQL_SEARCH / VECTOR_SEARCH intent의 답변을 전역 캐싱하고, 사용자별(room 단위) 최근 질의 컨텍스트를 Router Agent에 주입하여 후속 질의("성동구는?")를 가공한다.

**Architecture:**
- **Answer Cache (전역)**: `sha256(refined_query)` 키로 final payload + 검색 결과 일부(`vector_results`/`sql_results`/`refined_query`)를 함께 저장. 정확 매칭만 지원(시맨틱 캐시는 추후 단계).
- **Recent Queries Cache (per-room)**: Redis LIST(LPUSH + LTRIM)로 최근 N개 질의(답변 제외)만 보관. Router Agent가 컨텍스트로 받아 follow-up 질의 가공.
- **Topology**: Router 다음에 `cache_check_node`를 두고 hit이면 END로 점프, miss면 정상 흐름. Answer 이후 `cache_write_node`가 SQL_SEARCH / VECTOR_SEARCH 결과만 캐싱. MAP/FALLBACK은 캐시 미적용.
- **무효화**: 수집 스케줄러 완료 후 Spring Boot가 AI 서비스의 `POST /admin/cache/flush`를 호출 → `answer_cache:*` 키 전체 제거. 짧은 TTL(15분)로 추가 보호.
- **장애 처리**: 모든 Redis 연동은 fail-open. 캐시/recent_queries 장애 시 정상 흐름 유지.

**Tech Stack:** Python 3.13, FastAPI 0.135.x, redis.asyncio, LangGraph, pytest + AsyncMock

**Redis 클라이언트 사용 규칙:**
`core/redis.py::get_redis()`는 **동기 팩토리**(`aioredis.from_url()` 즉시 반환)이며, 반환된 client만 비동기다.
따라서 호출 패턴은 `redis = get_redis()` (await 없음) → `await redis.get/set/...` → `finally: await redis.aclose()`.
새로 도입되는 모든 호출처(`routers/chat.py`, `routers/admin.py`, 노드 의존성 주입)에서 동일하게 적용한다.

**관련 문서 supersede:**
- `docs/superpowers/plans/2026-05-13-redis-rate-limit-answer-cache.md`의 **Task 3 (Answer Cache)** 부분은 본 계획이 대체한다. 해당 문서의 Concurrent Limit(Task 1·2·4)는 그대로 유효.

---

## 변경 노트 — Cache 키 합성 확장 (2026-05-15)

**대상:** `core/cache.py`, `agents/nodes.py`(CacheCheckNode·CacheWriteNode)

**변경:** Cache 키를 `sha256(refined_query)` 단독에서 합성 키로 확장한다.

- 합성 입력: `refined_query | max={max_class_name} | area={area_name} | status={service_status}`
- `None`과 `""`는 동등하게 정규화(빈 문자열로 직렬화)
- `get_cached_answer` / `set_cached_answer` / `_cache_key` 모두 keyword-only 인자(`*` 구분)로 3개 필드 추가 — 기본값 None으로 기존 호출 호환
- envelope `state` snapshot에 `max_class_name` / `area_name` / `service_status` 3개 필드를 함께 저장하고, cache hit 시 state로 복원한다 (post-filter는 답변 컨텍스트의 일부)

**이유:**
Router LLM이 prompt를 어기고 `refined_query="테니스장"` + `area_name="강남구"`처럼 메타데이터를 분리 산출하는 경우, 기존 키 설계는 동일 refined_query를 가진 타 지역(예: 성동구) 질의에 잘못된 hit을 반환할 수 있었다(QA 발견 LOW 위험). 합성 키로 cross-user 오염을 차단한다.

**회귀 테스트:**
- `tests/test_answer_cache.py::TestCacheKey`
  - `test_different_area_produces_different_key`
  - `test_different_max_class_produces_different_key`
  - `test_different_service_status_produces_different_key`
  - `test_none_metadata_consistent_with_empty_string`
  - `test_no_metadata_equals_all_none`
- `tests/test_graph_cache.py::TestCacheCheckNode::test_same_query_different_area_produces_cache_miss` — 기존 사용자가 강남구로 캐싱했을 때 동일 refined_query / 다른 area_name의 신규 사용자는 cache miss

---

## Conversation Context 흐름 예시

```
[room_id=1] 사용자: "테니스장 보여줘"
  recent_queries(room=1) = []
  router → intent=VECTOR_SEARCH, refined_query="서울 테니스장"
  cache miss → 정상 응답
  → recent_queries(room=1) push "테니스장 보여줘"

[room_id=1] 사용자: "성동구는?"
  recent_queries(room=1) = ["테니스장 보여줘"]
  router 프롬프트에 컨텍스트 주입
  → intent=VECTOR_SEARCH, refined_query="성동구 테니스장"
  cache miss → 정상 응답
  → recent_queries(room=1) push "성동구는?"
```

다른 사용자(room_id=2)가 직접 "성동구 테니스장"을 물어보면 **전역 Answer Cache에서 hit** — 가공된 refined_query 기준으로 캐시 키를 만들기 때문.

---

## File Map

| 파일 | 역할 | 변경 |
|------|------|------|
| `core/config.py` | 캐시 / recent_queries 설정값 | 수정 |
| `core/cache.py` | Answer Cache read/write (state 일부 포함) | 신규 |
| `core/recent_queries.py` | per-room 최근 질의 큐 | 신규 |
| `schemas/state.py` | `AgentState`에 `recent_queries`, `cache_hit` 필드 추가 | 수정 |
| `agents/router_agent.py` | recent_queries를 LLM 컨텍스트로 주입, refined_query 산출 | 수정 |
| `agents/nodes.py` | `cache_check_node`, `cache_write_node` 추가 | 수정 |
| `agents/graph.py` | conditional edge 재배선 (router → cache_check, answer → cache_write) | 수정 |
| `routers/chat.py` | recent_queries fetch / push, SSE `cache_hit` 이벤트 | 수정 |
| `routers/admin.py` | `POST /admin/cache/flush` 엔드포인트 | 신규 |
| `main.py` | admin 라우터 등록 | 수정 |
| `tests/test_answer_cache.py` | Answer Cache 단위 테스트 | 신규 |
| `tests/test_recent_queries.py` | recent_queries 단위 테스트 | 신규 |
| `tests/test_graph_cache.py` | cache_check / cache_write 노드 통합 | 신규 |
| `tests/test_router_agent_context.py` | recent_queries 컨텍스트 분기 | 신규 |
| `tests/test_chat_router.py` | recent_queries push, cache_hit SSE 시나리오 | 수정 |
| `tests/test_admin_cache.py` | flush 엔드포인트 테스트 | 신규 |
| `agents/workflow.py` | 레거시 LCEL 워크플로우 (graph.py로 일원화) | **삭제** |
| `tests/test_workflow.py` | 레거시 워크플로우 테스트 | **삭제** |
| `tests/test_integration_workflow.py` | 레거시 워크플로우 통합 테스트 | **삭제** |

---

## Task 1: 설정값 추가

**Files:**
- Modify: `core/config.py`

- [ ] **Step 1: `core/config.py`에 설정 필드 추가**

```python
# Answer Cache
answer_cache_enabled: bool = True
answer_cache_ttl: int = 900            # 15분 — 수집 스케줄러 주기보다 짧게
answer_cache_empty_ttl: int = 300      # 빈 결과 캐시 5분
answer_cache_eligible_intents: tuple[str, ...] = ("SQL_SEARCH", "VECTOR_SEARCH")

# Recent Queries (per-room)
recent_queries_enabled: bool = True
recent_queries_max: int = 5            # 보관 개수
recent_queries_ttl: int = 1800         # 30분 슬라이딩 — push 마다 갱신

# Admin
admin_internal_token: str = ""         # /admin/* 보호용 공유 토큰
```

- [ ] **Step 2: 환경변수 로딩 확인**

```bash
cd /Users/vito/study/on-seoul-agent/on-seoul-agent
uv run python -c "from core.config import settings; print(settings.answer_cache_ttl, settings.recent_queries_max)"
```
Expected: `900 5`

- [ ] **Step 3: 린트**

```bash
uv run ruff check core/config.py
```

---

## Task 2: `core/cache.py` — Answer Cache 구현

### 캐시 키·값 설계

```
key:    answer_cache:{sha256(refined_query.strip().lower())[:16]}
value:  JSON {
  "payload": {message_id, answer, intent, title},
  "state":   {refined_query, vector_results, sql_results}
}
TTL:    answer_cache_ttl (정상) / answer_cache_empty_ttl (빈 결과)
```

> `message_id` / `title`은 cache hit 시 호출 측에서 요청값으로 덮어쓴다.
> error / workflow_error는 캐싱하지 않는다.
> MAP / FALLBACK intent는 캐싱하지 않는다 (호출 측에서 가드).

**Files:**
- Create: `core/cache.py`
- Create: `tests/test_answer_cache.py`

- [ ] **Step 1: 테스트 작성** — `tests/test_answer_cache.py`

```python
"""core/cache.py — Answer Cache 단위 테스트."""

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_redis():
    return AsyncMock()


@pytest.fixture
def sample_payload():
    return {"message_id": 3, "answer": "테니스장 안내입니다.", "intent": "VECTOR_SEARCH", "title": None}


@pytest.fixture
def sample_state():
    return {"refined_query": "서울 테니스장", "vector_results": [{"service_id": "S1"}], "sql_results": None}


class TestCacheKey:
    def test_key_strips_and_lowercases(self):
        from core.cache import _cache_key
        assert _cache_key("  서울 테니스장 ") == _cache_key("서울 테니스장")
        assert _cache_key("서울 테니스장") == _cache_key("서울 테니스장")


class TestGetCachedAnswer:
    async def test_miss_returns_none(self, mock_redis):
        mock_redis.get.return_value = None
        from core.cache import get_cached_answer
        assert await get_cached_answer("서울 테니스장", mock_redis) is None

    async def test_hit_returns_envelope(self, mock_redis, sample_payload, sample_state):
        envelope = {"payload": sample_payload, "state": sample_state}
        mock_redis.get.return_value = json.dumps(envelope)
        from core.cache import get_cached_answer
        result = await get_cached_answer("서울 테니스장", mock_redis)
        assert result == envelope

    async def test_disabled_skips_redis(self, mock_redis):
        from core.cache import get_cached_answer
        from core.config import settings
        with patch.object(settings, "answer_cache_enabled", False):
            assert await get_cached_answer("q", mock_redis) is None
        mock_redis.get.assert_not_called()

    async def test_redis_error_returns_none(self, mock_redis):
        mock_redis.get.side_effect = RuntimeError("redis down")
        from core.cache import get_cached_answer
        assert await get_cached_answer("q", mock_redis) is None


class TestSetCachedAnswer:
    async def test_set_stores_envelope_with_ttl(self, mock_redis, sample_payload, sample_state):
        from core.cache import set_cached_answer
        from core.config import settings
        await set_cached_answer("서울 테니스장", sample_payload, sample_state, mock_redis)
        mock_redis.set.assert_called_once()
        kwargs = mock_redis.set.call_args.kwargs
        assert kwargs["ex"] == settings.answer_cache_ttl
        body = json.loads(mock_redis.set.call_args.args[1])
        assert body["payload"] == sample_payload
        assert body["state"] == sample_state

    async def test_empty_results_uses_short_ttl(self, mock_redis, sample_payload):
        empty_state = {"refined_query": "x", "vector_results": [], "sql_results": []}
        from core.cache import set_cached_answer
        from core.config import settings
        await set_cached_answer("x", sample_payload, empty_state, mock_redis)
        assert mock_redis.set.call_args.kwargs["ex"] == settings.answer_cache_empty_ttl

    async def test_disabled_skips_set(self, mock_redis, sample_payload, sample_state):
        from core.cache import set_cached_answer
        from core.config import settings
        with patch.object(settings, "answer_cache_enabled", False):
            await set_cached_answer("q", sample_payload, sample_state, mock_redis)
        mock_redis.set.assert_not_called()

    async def test_redis_error_does_not_raise(self, mock_redis, sample_payload, sample_state):
        mock_redis.set.side_effect = RuntimeError("redis down")
        from core.cache import set_cached_answer
        await set_cached_answer("q", sample_payload, sample_state, mock_redis)  # no raise


class TestFlush:
    async def test_flush_scans_and_deletes(self, mock_redis):
        async def _scan_iter(match):
            for k in [b"answer_cache:aaa", b"answer_cache:bbb"]:
                yield k
        mock_redis.scan_iter = _scan_iter
        from core.cache import flush_answer_cache
        deleted = await flush_answer_cache(mock_redis)
        assert deleted == 2
        mock_redis.delete.assert_called()
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/test_answer_cache.py -v
```
Expected: `ImportError`

- [ ] **Step 3: `core/cache.py` 구현**

```python
"""Answer Cache — refined_query 기반 전역 캐싱.

키:   answer_cache:{sha256(refined_query)[:16]}
값:   JSON {payload, state}
TTL:  정상 결과는 settings.answer_cache_ttl,
      빈 검색 결과(vector/sql 모두 empty)는 settings.answer_cache_empty_ttl

Redis 장애 시 fail-open. MAP/FALLBACK 및 error는 호출 측에서 가드한다.
"""

import hashlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "answer_cache:"


def _cache_key(query: str) -> str:
    normalized = query.strip().lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{_KEY_PREFIX}{digest}"


def _is_empty_state(state: dict[str, Any]) -> bool:
    return not state.get("vector_results") and not state.get("sql_results")


async def get_cached_answer(query: str, redis: aioredis.Redis) -> dict | None:
    """캐시된 envelope({payload, state})를 반환. miss/장애 시 None."""
    if not settings.answer_cache_enabled:
        return None
    try:
        raw = await redis.get(_cache_key(query))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        logger.warning("answer cache GET 오류 — miss 처리", exc_info=True)
        return None


async def set_cached_answer(
    query: str,
    payload: dict[str, Any],
    state: dict[str, Any],
    redis: aioredis.Redis,
) -> None:
    """payload + state 일부를 envelope로 저장. 장애 시 무시."""
    if not settings.answer_cache_enabled:
        return
    ttl = settings.answer_cache_empty_ttl if _is_empty_state(state) else settings.answer_cache_ttl
    envelope = {"payload": payload, "state": state}
    try:
        await redis.set(
            _cache_key(query),
            json.dumps(envelope, ensure_ascii=False, default=str),
            ex=ttl,
        )
    except Exception:
        logger.warning("answer cache SET 오류 — 캐싱 건너뜀", exc_info=True)


async def flush_answer_cache(redis: aioredis.Redis) -> int:
    """`answer_cache:*` 키 전체 삭제. 삭제된 키 수 반환. 장애 시 0."""
    deleted = 0
    try:
        batch: list = []
        async for key in redis.scan_iter(match=f"{_KEY_PREFIX}*"):
            batch.append(key)
            if len(batch) >= 500:
                deleted += await redis.delete(*batch)
                batch = []
        if batch:
            deleted += await redis.delete(*batch)
    except Exception:
        logger.warning("answer cache flush 오류", exc_info=True)
    return deleted
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/test_answer_cache.py -v
```

---

## Task 3: `core/recent_queries.py` — per-room 최근 질의 저장소

### 저장 형식

```
key:   recent_queries:room:{room_id}
type:  LIST (LPUSH + LTRIM, 최신이 인덱스 0)
TTL:   settings.recent_queries_ttl (push 마다 EXPIRE로 슬라이딩)
값:    answer 미포함. 사용자 원본 message 텍스트만.
```

**Files:**
- Create: `core/recent_queries.py`
- Create: `tests/test_recent_queries.py`

- [ ] **Step 1: 테스트 작성** — `tests/test_recent_queries.py`

```python
"""core/recent_queries.py 단위 테스트."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_redis():
    return AsyncMock()


class TestGetRecentQueries:
    async def test_returns_latest_first(self, mock_redis):
        mock_redis.lrange.return_value = [b"\xec\x84\xb1\xeb\x8f\x99\xea\xb5\xac\xeb\x8a\x94?", b"\xed\x85\x8c\xeb\x8b\x88\xec\x8a\xa4\xec\x9e\xa5 \xeb\xb3\xb4\xec\x97\xac\xec\xa4\x98"]
        from core.recent_queries import get_recent_queries
        result = await get_recent_queries(room_id=1, redis=mock_redis)
        assert result == ["성동구는?", "테니스장 보여줘"]

    async def test_empty_returns_empty_list(self, mock_redis):
        mock_redis.lrange.return_value = []
        from core.recent_queries import get_recent_queries
        assert await get_recent_queries(room_id=1, redis=mock_redis) == []

    async def test_disabled_skips_redis(self, mock_redis):
        from core.recent_queries import get_recent_queries
        from core.config import settings
        with patch.object(settings, "recent_queries_enabled", False):
            assert await get_recent_queries(room_id=1, redis=mock_redis) == []
        mock_redis.lrange.assert_not_called()

    async def test_redis_error_returns_empty(self, mock_redis):
        mock_redis.lrange.side_effect = RuntimeError("down")
        from core.recent_queries import get_recent_queries
        assert await get_recent_queries(room_id=1, redis=mock_redis) == []


class TestPushRecentQuery:
    async def test_push_then_trim_then_expire(self, mock_redis):
        from core.recent_queries import push_recent_query
        from core.config import settings
        await push_recent_query(room_id=1, message="테니스장", redis=mock_redis)
        mock_redis.lpush.assert_called_once()
        mock_redis.ltrim.assert_called_once()
        trim_args = mock_redis.ltrim.call_args.args
        assert trim_args[1] == 0
        assert trim_args[2] == settings.recent_queries_max - 1
        mock_redis.expire.assert_called_once()

    async def test_disabled_skips(self, mock_redis):
        from core.recent_queries import push_recent_query
        from core.config import settings
        with patch.object(settings, "recent_queries_enabled", False):
            await push_recent_query(room_id=1, message="x", redis=mock_redis)
        mock_redis.lpush.assert_not_called()

    async def test_redis_error_does_not_raise(self, mock_redis):
        mock_redis.lpush.side_effect = RuntimeError("down")
        from core.recent_queries import push_recent_query
        await push_recent_query(room_id=1, message="x", redis=mock_redis)
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/test_recent_queries.py -v
```

- [ ] **Step 3: `core/recent_queries.py` 구현**

```python
"""per-room 최근 질의 큐 — Router Agent의 follow-up 컨텍스트.

저장값은 사용자 원본 message만 포함 (answer/intent 제외).
LIST 최신 인덱스 0. LPUSH + LTRIM + EXPIRE.
"""

import logging

import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)


def _key(room_id: int) -> str:
    return f"recent_queries:room:{room_id}"


async def get_recent_queries(room_id: int, redis: aioredis.Redis) -> list[str]:
    """최신 순으로 최근 질의를 반환. 비활성/장애 시 빈 리스트."""
    if not settings.recent_queries_enabled:
        return []
    try:
        items = await redis.lrange(_key(room_id), 0, settings.recent_queries_max - 1)
        return [item.decode("utf-8") if isinstance(item, bytes) else item for item in items]
    except Exception:
        logger.warning("recent_queries GET 오류 — 빈 컨텍스트", exc_info=True)
        return []


async def push_recent_query(room_id: int, message: str, redis: aioredis.Redis) -> None:
    """원본 message를 큐에 push. 장애 시 무시."""
    if not settings.recent_queries_enabled:
        return
    if not message or not message.strip():
        return
    key = _key(room_id)
    try:
        await redis.lpush(key, message.strip())
        await redis.ltrim(key, 0, settings.recent_queries_max - 1)
        await redis.expire(key, settings.recent_queries_ttl)
    except Exception:
        logger.warning("recent_queries PUSH 오류 — 컨텍스트 누락", exc_info=True)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/test_recent_queries.py -v
```

---

## Task 4: `AgentState`에 컨텍스트 / 캐시 필드 추가

**Files:**
- Modify: `schemas/state.py`

- [ ] **Step 1: AgentState 확장**

`AgentState` TypedDict에 아래 필드를 추가한다:

```python
# Router 컨텍스트 / 캐시 흐름
recent_queries: list[str]   # router에 주입할 follow-up 컨텍스트
cache_hit: bool             # cache_check_node 결과 (관측·라우팅용)
```

router/sql/vector/answer 노드 호출 측에서 이미 dict 리터럴로 초기화되는 곳들을 모두 수정 (`routers/chat.py`, `agents/workflow.py`, 테스트 fixture 포함).

- [ ] **Step 2: 회귀 테스트 확인**

```bash
uv run pytest tests/ -v -k "state or agent or graph or chat"
```

기존 dict 리터럴이 누락되면 KeyError가 나므로 모두 잡아낸다. 누락 발견 시 `recent_queries=[]`, `cache_hit=False` 기본값을 채운다.

---

## Task 5: Router Agent에 recent_queries 컨텍스트 주입

**Files:**
- Modify: `agents/router_agent.py`
- Create: `tests/test_router_agent_context.py`

### 동작

- LLM 시스템 프롬프트에 "이전 사용자 발화" 섹션을 추가한다.
- recent_queries가 비어 있으면 섹션을 출력하지 않는다(불필요한 토큰 절약).
- Router는 기존처럼 `intent` + `refined_query`를 반환. follow-up이 감지되면 refined_query에 이전 맥락(예: 카테고리·키워드)을 병합한다.

- [ ] **Step 1: 테스트 작성** — `tests/test_router_agent_context.py`

```python
"""Router Agent — recent_queries 주입 분기 검증.

LLM 호출은 mock하고, 프롬프트에 컨텍스트가 들어갔는지 검증.
"""

from unittest.mock import AsyncMock, patch


class TestRouterContextInjection:
    async def test_recent_queries_in_prompt(self):
        from agents.router_agent import RouterAgent
        agent = RouterAgent()

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = AsyncMock(content='{"intent": "VECTOR_SEARCH", "refined_query": "성동구 테니스장"}')

        with patch.object(agent, "_llm", mock_llm):
            await agent.classify(message="성동구는?", recent_queries=["테니스장 보여줘"])

        messages = mock_llm.ainvoke.call_args.args[0]
        prompt_text = "\n".join(m.content if hasattr(m, "content") else str(m) for m in messages)
        assert "테니스장 보여줘" in prompt_text

    async def test_empty_recent_queries_omits_section(self):
        from agents.router_agent import RouterAgent
        agent = RouterAgent()

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = AsyncMock(content='{"intent": "FALLBACK", "refined_query": null}')

        with patch.object(agent, "_llm", mock_llm):
            await agent.classify(message="안녕", recent_queries=[])

        messages = mock_llm.ainvoke.call_args.args[0]
        prompt_text = "\n".join(m.content if hasattr(m, "content") else str(m) for m in messages)
        assert "이전 사용자 발화" not in prompt_text
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/test_router_agent_context.py -v
```

- [ ] **Step 3: `RouterAgent.classify` 시그니처 / 프롬프트 수정**

요지:

```python
async def classify(self, message: str, recent_queries: list[str] | None = None) -> IntentResult:
    """기존 동작 + recent_queries가 있으면 follow-up 맥락 병합.

    Note: recent_queries 인자는 기본값 None으로 호환성 유지.
    기존 호출처(있다면)는 변경 없이 동작하며, router_node만 명시적으로 전달한다.
    """
    context_block = self._build_context_block(recent_queries)
    system_prompt = _ROUTER_SYSTEM + (f"\n\n{context_block}" if context_block else "")
    ...

def _build_context_block(self, recent_queries: list[str] | None) -> str:
    if not recent_queries:
        return ""
    lines = "\n".join(f"- {q}" for q in recent_queries[:5])
    return (
        "이전 사용자 발화 (최신 순). 후속 질의는 직전 발화의 카테고리·지역을 이어받을 가능성이 높다.\n"
        "이전 맥락이 명확하면 refined_query에 카테고리·지역 키워드를 병합한다.\n"
        f"{lines}"
    )
```

`router_node`(`agents/nodes.py`)에서 `self._router.classify(message, recent_queries=state["recent_queries"])`로 전달.

- [ ] **Step 4: 테스트 통과**

```bash
uv run pytest tests/test_router_agent_context.py tests/test_router_agent.py -v
```

---

## Task 6: `cache_check_node` / `cache_write_node` 노드 추가 + graph 재배선

**Files:**
- Modify: `agents/nodes.py`
- Modify: `agents/graph.py`
- Create: `tests/test_graph_cache.py`

### 노드 동작

**cache_check_node** (router 직후):
1. `state.intent`이 `answer_cache_eligible_intents`에 속하지 않으면 그대로 pass (cache_hit=False).
2. `state.refined_query`가 비어있으면 pass.
3. `get_cached_answer(refined_query)` → envelope 있으면:
   - `state.answer = payload["answer"]`
   - `state.vector_results = state_snap["vector_results"]`
   - `state.sql_results = state_snap["sql_results"]`
   - `state.cache_hit = True`
4. 미스/장애 시 cache_hit=False.

**cache_write_node** (answer 직후, 캐싱 대상 intent에 한해):
1. `state.error` 있으면 skip.
2. `state.cache_hit` True면 skip (이중 쓰기 방지).
3. intent eligible이 아니면 skip.
4. payload + state snapshot 구성 → `set_cached_answer`.

### Graph 엣지

```
router
  └─ cache_check
       ├─ (cache_hit=True) ─────────────────────────────────────→ END
       └─ (cache_hit=False) → [sql | vector | map | fallback]
                                          └─→ answer
                                                └─ cache_write → END
```

- [ ] **Step 1: 테스트 작성** — `tests/test_graph_cache.py`

```python
"""cache_check / cache_write 노드 및 graph 라우팅."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def base_state():
    return {
        "room_id": 1,
        "message_id": 1,
        "message": "테니스장",
        "title_needed": True,
        "intent": None,
        "refined_query": "서울 테니스장",
        "recent_queries": [],
        "cache_hit": False,
        "sql_results": None,
        "vector_results": None,
        "map_results": None,
        "answer": None,
        "title": None,
        "trace": None,
        "error": None,
        "retry_count": 0,
        "lat": None,
        "lng": None,
    }


class TestCacheCheckNode:
    async def test_eligible_hit_populates_state(self, base_state):
        from agents.nodes import CacheCheckNode
        from schemas.state import IntentType
        base_state["intent"] = IntentType.VECTOR_SEARCH
        envelope = {
            "payload": {"answer": "캐시 답변", "intent": "VECTOR_SEARCH", "title": None, "message_id": 1},
            "state": {"refined_query": "서울 테니스장", "vector_results": [{"service_id": "S1"}], "sql_results": None},
        }
        with patch("agents.nodes.get_cached_answer", AsyncMock(return_value=envelope)):
            node = CacheCheckNode(redis=AsyncMock())
            new_state = await node(base_state)
        assert new_state["cache_hit"] is True
        assert new_state["answer"] == "캐시 답변"
        assert new_state["vector_results"] == [{"service_id": "S1"}]

    async def test_eligible_miss_passes_through(self, base_state):
        from agents.nodes import CacheCheckNode
        from schemas.state import IntentType
        base_state["intent"] = IntentType.VECTOR_SEARCH
        with patch("agents.nodes.get_cached_answer", AsyncMock(return_value=None)):
            node = CacheCheckNode(redis=AsyncMock())
            new_state = await node(base_state)
        assert new_state["cache_hit"] is False
        assert new_state["answer"] is None

    async def test_non_eligible_intent_skips_lookup(self, base_state):
        from agents.nodes import CacheCheckNode
        from schemas.state import IntentType
        base_state["intent"] = IntentType.MAP
        with patch("agents.nodes.get_cached_answer", AsyncMock()) as mock_get:
            node = CacheCheckNode(redis=AsyncMock())
            await node(base_state)
        mock_get.assert_not_called()


class TestCacheWriteNode:
    async def test_writes_on_success(self, base_state):
        from agents.nodes import CacheWriteNode
        from schemas.state import IntentType
        base_state["intent"] = IntentType.VECTOR_SEARCH
        base_state["answer"] = "신규 답변"
        base_state["vector_results"] = [{"service_id": "S1"}]
        with patch("agents.nodes.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)
        mock_set.assert_called_once()

    async def test_skips_on_error(self, base_state):
        from agents.nodes import CacheWriteNode
        from schemas.state import IntentType
        base_state["intent"] = IntentType.VECTOR_SEARCH
        base_state["answer"] = "x"
        base_state["error"] = "boom"
        with patch("agents.nodes.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)
        mock_set.assert_not_called()

    async def test_skips_on_cache_hit(self, base_state):
        from agents.nodes import CacheWriteNode
        from schemas.state import IntentType
        base_state["intent"] = IntentType.VECTOR_SEARCH
        base_state["cache_hit"] = True
        base_state["answer"] = "x"
        with patch("agents.nodes.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)
        mock_set.assert_not_called()

    async def test_skips_non_eligible_intent(self, base_state):
        from agents.nodes import CacheWriteNode
        from schemas.state import IntentType
        base_state["intent"] = IntentType.MAP
        base_state["answer"] = "x"
        with patch("agents.nodes.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)
        mock_set.assert_not_called()


class TestGraphRouting:
    async def test_cache_hit_routes_directly_to_end(self):
        """cache_hit=True면 sql/vector/answer 노드가 호출되지 않는다."""
        # 통합 graph build → cache_check가 hit 반환하도록 mock → events 수집
        # 자세한 mock 셋업은 기존 tests/test_graph.py 패턴 따름
        pass  # 구현 시 실제 graph build + mocked nodes로 채움
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/test_graph_cache.py -v
```

- [ ] **Step 3: `agents/nodes.py`에 노드 추가**

```python
from core.cache import get_cached_answer, set_cached_answer
from core.config import settings
from schemas.state import IntentType


class CacheCheckNode:
    """router 직후, intent가 캐싱 대상이면 cache 조회."""

    def __init__(self, redis):
        self._redis = redis

    async def __call__(self, state: dict) -> dict:
        intent = state.get("intent")
        refined = state.get("refined_query")
        if intent is None or refined is None:
            state["cache_hit"] = False
            return state
        if intent.value not in settings.answer_cache_eligible_intents:
            state["cache_hit"] = False
            return state

        envelope = await get_cached_answer(refined, self._redis)
        if envelope is None:
            state["cache_hit"] = False
            return state

        payload = envelope.get("payload", {})
        snap = envelope.get("state", {})
        state["answer"] = payload.get("answer")
        state["title"] = payload.get("title")
        state["vector_results"] = snap.get("vector_results")
        state["sql_results"] = snap.get("sql_results")
        state["cache_hit"] = True
        logger.info("answer cache HIT — intent=%s", intent.value)
        return state


class CacheWriteNode:
    """answer 직후, 정상 결과만 캐싱."""

    def __init__(self, redis):
        self._redis = redis

    async def __call__(self, state: dict) -> dict:
        if state.get("error"):
            return state
        if state.get("cache_hit"):
            return state
        intent = state.get("intent")
        if intent is None or intent.value not in settings.answer_cache_eligible_intents:
            return state
        refined = state.get("refined_query")
        answer = state.get("answer")
        if not refined or not answer:
            return state

        payload = {
            "message_id": state.get("message_id"),
            "answer": answer,
            "intent": intent.value,
            "title": state.get("title"),
        }
        snap = {
            "refined_query": refined,
            "vector_results": state.get("vector_results"),
            "sql_results": state.get("sql_results"),
        }
        await set_cached_answer(refined, payload, snap, self._redis)
        logger.info("answer cache WRITE — intent=%s", intent.value)
        return state
```

- [ ] **Step 4: `agents/graph.py` 엣지 재배선**

핵심 변경:

```python
# 기존: workflow.add_edge("router", routing_decision)
# 변경:
workflow.add_node("cache_check", CacheCheckNode(redis=self._redis))
workflow.add_node("cache_write", CacheWriteNode(redis=self._redis))

workflow.add_edge("router", "cache_check")

def _post_cache_check(state: dict) -> str:
    if state.get("cache_hit"):
        return END
    intent = state.get("intent")
    if intent == IntentType.SQL_SEARCH:
        return "sql"
    if intent == IntentType.VECTOR_SEARCH:
        return "vector"
    if intent == IntentType.MAP:
        return "map"
    return "fallback"

workflow.add_conditional_edges("cache_check", _post_cache_check, {
    END: END,
    "sql": "sql",
    "vector": "vector",
    "map": "map",
    "fallback": "fallback",
})

# answer 다음을 cache_write로 라우팅 — 단 eligible intent만
workflow.add_edge("answer", "cache_write")
workflow.add_edge("cache_write", END)
```

**레거시 `agents/workflow.py` 제거** (별도 step):
검토 결과 활성 코드 어디에서도 import되지 않고 테스트(`tests/test_workflow.py`, `tests/test_integration_workflow.py`)만 의존한다. 본 task에서 함께 제거하여 graph.py로 일원화한다.

- [ ] **Step 4a: 레거시 workflow 제거**

```bash
rm agents/workflow.py tests/test_workflow.py tests/test_integration_workflow.py
uv run pytest -v   # 회귀 확인 (제거 후에도 그린)
uv run ruff check .
```

만약 회귀 발생 시(예상치 못한 import) 해당 import를 graph.py 기반으로 교체한 뒤 다시 실행.

- [ ] **Step 5: 테스트 통과 + 회귀**

```bash
uv run pytest tests/test_graph_cache.py tests/test_graph.py -v
```

---

## Task 7: `routers/chat.py` 통합

### 흐름

```
POST /chat/stream
  ├─ recent = get_recent_queries(room_id)
  ├─ AgentState에 recent_queries 주입
  ├─ graph.stream(...)
  │    ├─ router → cache_check
  │    │   └─ hit이면 progress("cache_hit") + 즉시 final 흐름
  │    └─ ...
  ├─ 정상 종료 후 push_recent_query(room_id, message)
  └─ finally: redis.aclose()
```

> recent_queries push는 error / workflow_error가 아닌 정상 완료 시에만.
> cache_hit 정보는 graph 내부에서 state["cache_hit"]로 흘러오므로, chat.py는 result 이벤트 처리 시 `cache_hit` 플래그를 payload에 포함하여 SSE에 노출(`payload["cache_hit"] = True`).

**Files:**
- Modify: `routers/chat.py`
- Modify: `tests/test_chat_router.py`

- [ ] **Step 1: `routers/chat.py` 변경**

핵심 발췌:

```python
from core.recent_queries import get_recent_queries, push_recent_query

async def _stream(request: ChatRequest) -> AsyncGenerator[bytes, None]:
    redis = get_redis()
    try:
        recent = await get_recent_queries(room_id=request.room_id, redis=redis)

        state = AgentState(
            room_id=request.room_id,
            message_id=request.message_id,
            message=request.message,
            title_needed=(request.message_id == 1),
            intent=None,
            refined_query=None,
            recent_queries=recent,
            cache_hit=False,
            lat=request.lat,
            lng=request.lng,
            sql_results=None,
            vector_results=None,
            map_results=None,
            answer=None,
            title=None,
            trace=None,
            error=None,
            retry_count=0,
        )

        async with data_session_ctx() as data_session, ai_session_ctx() as ai_session:
            async for event_type, data in _get_graph().stream(
                state, data_session=data_session, ai_session=ai_session,
            ):
                if event_type == "progress":
                    yield sse_frame("progress", data)
                elif event_type == "result":
                    result = data
                    intent = result.get("intent")
                    payload = {
                        "message_id": result["message_id"],
                        "answer": result.get("answer") or "",
                        "intent": intent.value if intent is not None else None,
                        "title": result.get("title"),
                        "cache_hit": bool(result.get("cache_hit")),
                    }
                    if result.get("error"):
                        logger.error("workflow error: %s", result["error"])
                        payload["error"] = "서비스 처리 중 오류가 발생했습니다."
                        yield sse_frame("workflow_error", payload)
                    else:
                        yield sse_frame("final", payload)
                        # 정상 종료 시에만 recent_queries push
                        await push_recent_query(
                            room_id=request.room_id,
                            message=request.message,
                            redis=redis,
                        )
    except Exception:
        logger.exception("워크플로우 실행 중 오류")
        yield sse_frame("error", {"message": "서비스 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."})
    finally:
        await redis.aclose()
```

- [ ] **Step 2: 통합 테스트 추가** — `tests/test_chat_router.py`

```python
class TestCacheAndContextIntegration:
    async def test_cache_hit_sse_payload_marks_cache_hit(self, async_client):
        """graph가 cache_hit=True로 result를 emit하면 SSE payload에 표시된다."""
        from schemas.state import IntentType

        async def _fake_stream(*args, **kwargs):
            yield ("result", {
                "message_id": 1,
                "answer": "캐시된 답변",
                "intent": IntentType.VECTOR_SEARCH,
                "title": None,
                "cache_hit": True,
                "error": None,
            })

        mock_graph = MagicMock()
        mock_graph.stream = _fake_stream

        with (
            patch("routers.chat._get_graph", return_value=mock_graph),
            patch("routers.chat.get_recent_queries", AsyncMock(return_value=[])),
            patch("routers.chat.push_recent_query", AsyncMock()) as mock_push,
            patch("routers.chat.get_redis", return_value=AsyncMock()),
            patch("routers.chat.data_session_ctx"),
            patch("routers.chat.ai_session_ctx"),
        ):
            resp = await async_client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테니스장"},
            )

        assert resp.status_code == 200
        assert b"cache_hit" in resp.content
        mock_push.assert_called_once()

    async def test_recent_queries_passed_into_state(self, async_client):
        """fetch한 recent_queries가 AgentState에 주입된다."""
        captured: dict = {}

        async def _fake_stream(state, **kwargs):
            captured["recent_queries"] = state["recent_queries"]
            yield ("result", {
                "message_id": 1, "answer": "x", "intent": None,
                "title": None, "cache_hit": False, "error": None,
            })

        mock_graph = MagicMock()
        mock_graph.stream = _fake_stream

        with (
            patch("routers.chat._get_graph", return_value=mock_graph),
            patch("routers.chat.get_recent_queries", AsyncMock(return_value=["이전질의"])),
            patch("routers.chat.push_recent_query", AsyncMock()),
            patch("routers.chat.get_redis", return_value=AsyncMock()),
            patch("routers.chat.data_session_ctx"),
            patch("routers.chat.ai_session_ctx"),
        ):
            await async_client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "성동구는?"},
            )

        assert captured["recent_queries"] == ["이전질의"]

    async def test_recent_queries_not_pushed_on_workflow_error(self, async_client):
        """workflow_error 응답은 recent_queries에 push되지 않는다."""
        from schemas.state import IntentType

        async def _fake_stream(*args, **kwargs):
            yield ("result", {
                "message_id": 1, "answer": "",
                "intent": IntentType.VECTOR_SEARCH, "title": None,
                "cache_hit": False, "error": "boom",
            })

        mock_graph = MagicMock()
        mock_graph.stream = _fake_stream

        with (
            patch("routers.chat._get_graph", return_value=mock_graph),
            patch("routers.chat.get_recent_queries", AsyncMock(return_value=[])),
            patch("routers.chat.push_recent_query", AsyncMock()) as mock_push,
            patch("routers.chat.get_redis", return_value=AsyncMock()),
            patch("routers.chat.data_session_ctx"),
            patch("routers.chat.ai_session_ctx"),
        ):
            await async_client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "x"},
            )

        mock_push.assert_not_called()
```

- [ ] **Step 3: 회귀 + 신규 통과**

```bash
uv run pytest tests/test_chat_router.py -v
```

---

## Task 8: `POST /admin/cache/flush` 엔드포인트

수집 스케줄러(Spring Boot)가 데이터 변경 후 호출하는 내부 엔드포인트. 공유 토큰으로 보호.

**Files:**
- Create: `routers/admin.py`
- Modify: `main.py`
- Create: `tests/test_admin_cache.py`

- [ ] **Step 1: 테스트 작성** — `tests/test_admin_cache.py`

```python
"""POST /admin/cache/flush — answer_cache:* 전체 삭제."""

from unittest.mock import AsyncMock, patch

import pytest


class TestFlushEndpoint:
    async def test_unauthorized_without_token(self, async_client):
        resp = await async_client.post("/admin/cache/flush")
        assert resp.status_code == 401

    async def test_wrong_token_rejected(self, async_client):
        from core.config import settings
        with patch.object(settings, "admin_internal_token", "secret"):
            resp = await async_client.post(
                "/admin/cache/flush", headers={"X-Internal-Token": "wrong"},
            )
        assert resp.status_code == 401

    async def test_authorized_flush_returns_count(self, async_client):
        from core.config import settings
        with (
            patch.object(settings, "admin_internal_token", "secret"),
            patch("routers.admin.flush_answer_cache", AsyncMock(return_value=42)),
            patch("routers.admin.get_redis", return_value=AsyncMock()),
        ):
            resp = await async_client.post(
                "/admin/cache/flush", headers={"X-Internal-Token": "secret"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"deleted": 42}

    async def test_empty_configured_token_rejects_all(self, async_client):
        """admin_internal_token이 빈 문자열이면 모든 요청 거부 (오설정 보호)."""
        from core.config import settings
        with patch.object(settings, "admin_internal_token", ""):
            resp = await async_client.post(
                "/admin/cache/flush", headers={"X-Internal-Token": ""},
            )
        assert resp.status_code == 401
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/test_admin_cache.py -v
```

- [ ] **Step 3: `routers/admin.py` 구현**

```python
"""내부 admin 엔드포인트 — Spring Boot 수집 스케줄러용.

보호: X-Internal-Token 헤더가 settings.admin_internal_token과 일치해야 한다.
빈 토큰 설정 시 모든 요청 거부 (오설정 시 노출 방지).
"""

from fastapi import APIRouter, Depends, Header, HTTPException, status

from core.cache import flush_answer_cache
from core.config import settings
from core.redis import get_redis

router = APIRouter(prefix="/admin", tags=["admin"])


def _verify_token(x_internal_token: str | None = Header(default=None)) -> None:
    expected = settings.admin_internal_token
    if not expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="admin disabled")
    if x_internal_token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


@router.post("/cache/flush", dependencies=[Depends(_verify_token)])
async def cache_flush() -> dict:
    redis = get_redis()
    try:
        deleted = await flush_answer_cache(redis)
        return {"deleted": deleted}
    finally:
        await redis.aclose()
```

- [ ] **Step 4: `main.py`에 라우터 등록**

```python
from routers import admin as admin_router
...
app.include_router(admin_router.router)
```

- [ ] **Step 5: 통과 확인**

```bash
uv run pytest tests/test_admin_cache.py -v
```

---

## Task 9: 메트릭 / 관측 로그

요구사항: 단순 메트릭 OK. Prometheus 미도입 (추후).

- 구조화 로그 키:
  - `cache.hit` — `{"intent": "...", "key_prefix": "...", "refined_query_len": N}`
  - `cache.miss` — 동일 필드
  - `cache.write` — `{"intent": "...", "empty_state": bool}`
  - `cache.flush` — `{"deleted": N}`
- SSE result payload의 `cache_hit` 플래그(이미 Task 7에서 추가)
- 응답 헤더는 SSE 특성상 의미가 약해 SSE payload 플래그로 대체

**Files:**
- Modify: `agents/nodes.py` (CacheCheckNode / CacheWriteNode 로그 강화)
- Modify: `core/cache.py` (flush 로그)

- [ ] **Step 1: 구조화 로그 보강**

```python
# CacheCheckNode hit/miss
logger.info("cache.hit intent=%s len=%d", intent.value, len(refined))
logger.info("cache.miss intent=%s len=%d", intent.value, len(refined))
# CacheWriteNode
logger.info("cache.write intent=%s empty=%s", intent.value, _is_empty)
# flush
logger.info("cache.flush deleted=%d", deleted)
```

- [ ] **Step 2: 간단한 카운트 fixture 테스트(선택)**

`caplog`로 hit/miss 로그가 한 번씩 기록되는지 검증한다.

```python
async def test_cache_check_logs_hit_and_miss(caplog):
    ...
    assert any("cache.hit" in r.message for r in caplog.records)
```

- [ ] **Step 3: 회귀**

```bash
uv run pytest -v
uv run ruff check .
```

---

## 완료 기준 체크리스트

- [ ] `uv run pytest -v` 전체 통과 / `uv run ruff check .` 통과
- [ ] `ANSWER_CACHE_ENABLED=false` 환경변수로 답변 캐시 완전 비활성화 가능
- [ ] `RECENT_QUERIES_ENABLED=false`로 컨텍스트 주입 비활성화 가능 (router는 message만으로 분류)
- [ ] Redis 장애 시 fail-open — cache/recent_queries 둘 다 정상 흐름 유지
- [ ] MAP / FALLBACK intent는 캐시 read/write 모두 호출되지 않음
- [ ] error / workflow_error는 캐시되지 않고, recent_queries에도 push되지 않음
- [ ] cache_hit 시 sql/vector/answer 노드가 호출되지 않음 (graph routing 검증)
- [ ] cache hit envelope 복원으로 `vector_results` / `sql_results`가 state에 채워짐 (카드 렌더링 가능)
- [ ] 빈 결과(`vector_results` / `sql_results` 둘 다 empty)는 짧은 TTL(`answer_cache_empty_ttl`)로 저장
- [ ] `POST /admin/cache/flush`는 `X-Internal-Token` 일치 시에만 동작, 미설정 시 항상 401
- [ ] Spring Boot 측에서 수집 스케줄러 완료 후 위 엔드포인트를 호출하도록 작업 위임 (별도 task)

---

## 향후 단계 (별도 계획)

- **Phase 2**: 시맨틱 캐시 — 질의 임베딩 기반 유사 매칭(threshold ≥ 0.9)으로 hit rate 추가 확보. pgvector 또는 Redis Vector 활용.
- **Phase 3**: `service_change_log` 기반 정밀 무효화 — 변경 service_id가 포함된 캐시 키만 선택적 제거. answer 캐시 값에 `referenced_service_ids` 인덱스(`reverse:service_id:{id} → cache_keys SET`) 도입 필요.
- **Phase 4**: cache hit/miss를 Prometheus / OTel로 수집, hit rate·절감 토큰량 대시보드화.

---

## 사전 확정 사항

1. **`RouterAgent.classify` 시그니처**: `recent_queries: list[str] | None = None` 기본값으로 호환성 유지. `router_node`만 명시적으로 전달, 다른 호출처는 변경 없음.
2. **레거시 `agents/workflow.py` 제거**: Task 6 Step 4a에서 `agents/workflow.py` + `tests/test_workflow.py` + `tests/test_integration_workflow.py` 일괄 삭제. 활성 코드 의존성 없음 확인 완료.
3. **Redis 클라이언트 호출 패턴**: `get_redis()`는 동기 팩토리 (`aioredis.from_url()` 즉시 반환). 모든 신규 호출처에서 `redis = get_redis()` (await 없음) → `await client.op(...)` → `finally: await redis.aclose()` 패턴 사용.

---

## API 서비스(on-seoul-api)에 위임할 작업

| 항목 | 내용 |
|------|------|
| 수집 스케줄러 종료 훅 | 일 1회 수집 완료 직후 `POST {AI_BASE_URL}/admin/cache/flush` 호출. 헤더 `X-Internal-Token`에 공유 토큰. |
| RPM Rate Limit | 기존 2026-05-13 계획서 참조. 본 문서 범위 외. |
