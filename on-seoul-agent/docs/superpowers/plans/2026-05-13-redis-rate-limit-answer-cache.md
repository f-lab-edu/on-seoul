# Redis Concurrent Limit & Answer Caching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redis를 활용하여 `/chat/stream` 엔드포인트에 Concurrent Limit과 Answer Caching을 적용한다.

**Architecture:**
- **Rate Limiting (RPM 제어)** 은 API 서비스(Spring Boot)의 책임. AI 서비스는 인증된 사용자 정보가 없고 내부 호출 대상이므로 RPM 제어를 두지 않는다.
- **Concurrent Limit** 은 AI 서비스 책임. `room_id`당 LangGraph 동시 실행을 1개로 제한한다. RPM 방어가 목적이 아닌 LangGraph 실행 안정성(중복 실행 방지)이 목적이므로 AI 서비스에 적합하다. `SET NX EX` 패턴으로 lock을 획득하고, 실행 완료 시 해제한다.
- **Answer Cache** 는 `hash(message)` 키로 최종 answer payload를 캐싱하여 동일 질문의 LLM 실행을 건너뛴다.

**Tech Stack:** Python 3.13, FastAPI 0.135.x, redis.asyncio (이미 의존성 포함), pytest + AsyncMock

---

## 책임 경계

```
[Frontend] → [Spring Boot API] ──────────────────────→ [FastAPI AI]
                   │                                         │
           Rate Limiting (RPM)                    Concurrent Limit (SET NX EX)
           사용자 기준 분당 N회 제한               room_id당 동시 실행 1개 제한
           JWT 인증 컨텍스트 보유                  Answer Cache (hash(message))
           bucket4j 등 활용
```

---

## File Map

| 파일 | 역할 | 변경 |
|------|------|------|
| `core/cache.py` | Answer 캐시 read/write 함수 | 신규 생성 |
| `middleware/concurrent_limit.py` | Concurrent Limit (SET NX EX) | 신규 생성 |
| `routers/chat.py` | Concurrent Limit + Cache 적용 | 수정 |
| `core/config.py` | 설정값 추가 (concurrent limit TTL, cache TTL) | 수정 |
| `tests/test_concurrent_limit.py` | Concurrent Limit 단위 테스트 | 신규 생성 |
| `tests/test_answer_cache.py` | Answer Cache 단위 테스트 | 신규 생성 |
| `tests/test_chat_router.py` | Concurrent Limit / Cache 통합 시나리오 추가 | 수정 |

---

## Task 1: 설정값 추가

**Files:**
- Modify: `core/config.py`

- [ ] **Step 1: `core/config.py`에 설정값 추가**

```python
# core/config.py — Settings 클래스에 아래 필드 추가

# Concurrent Limit — room_id당 LangGraph 동시 실행 1개 제한
concurrent_limit_enabled: bool = True  # False면 비활성화 (테스트·개발용)
concurrent_limit_ttl: int = 120        # lock TTL 초. LangGraph 최대 실행시간 상한 (기본 2분)

# Answer Cache
answer_cache_ttl: int = 3600           # 캐시 TTL 초 단위 (기본 1시간)
answer_cache_enabled: bool = True      # False면 캐싱 비활성화
```

- [ ] **Step 2: 설정값 확인**

```bash
cd /Users/vito/study/on-seoul-agent/on-seoul-agent
python -c "from core.config import settings; print(settings.concurrent_limit_ttl, settings.answer_cache_ttl)"
```
Expected: `120 3600`

- [ ] **Step 3: 커밋**

```bash
git add core/config.py
git commit -m "feat: Concurrent Limit·Answer Cache 설정값 추가"
```

---

## Task 2: Concurrent Limit 구현

**Files:**
- Create: `middleware/concurrent_limit.py`
- Create: `tests/test_concurrent_limit.py`

### 동작 설계

```
key: concurrent:room:{room_id}
SET key 1 NX EX {concurrent_limit_ttl}
  → True(획득 성공): 실행 진행
  → False(이미 실행 중): 409 Conflict 응답
실행 완료 / 예외 발생 시: DEL key (lock 해제)
```

**TTL 이중 보호:** lock을 명시적으로 해제하지 못한 경우(프로세스 크래시 등)에도 TTL이 만료되면 자동 해제된다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_concurrent_limit.py` 파일을 아래 내용으로 생성한다.

```python
"""middleware/concurrent_limit.py 단위 테스트."""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture
def mock_redis():
    client = AsyncMock()
    return client


class TestAcquireLock:
    async def test_acquire_sets_nx_key(self, mock_redis):
        """lock 획득 시 SET NX EX로 키를 설정한다."""
        mock_redis.set.return_value = True  # NX 획득 성공

        from middleware.concurrent_limit import acquire_lock

        result = await acquire_lock(room_id=1, redis=mock_redis)

        assert result is True
        mock_redis.set.assert_called_once()
        _, kwargs = mock_redis.set.call_args
        assert kwargs.get("nx") is True
        assert kwargs.get("ex") is not None

    async def test_acquire_fails_when_key_exists(self, mock_redis):
        """이미 실행 중이면 False를 반환한다."""
        mock_redis.set.return_value = None  # NX 획득 실패 (키 존재)

        from middleware.concurrent_limit import acquire_lock

        result = await acquire_lock(room_id=1, redis=mock_redis)

        assert result is False

    async def test_redis_error_returns_true(self, mock_redis):
        """Redis 장애 시 True를 반환한다 (fail-open — 실행 차단하지 않음)."""
        mock_redis.set.side_effect = Exception("redis down")

        from middleware.concurrent_limit import acquire_lock

        result = await acquire_lock(room_id=1, redis=mock_redis)

        assert result is True


class TestReleaseLock:
    async def test_release_deletes_key(self, mock_redis):
        """lock 해제 시 키를 삭제한다."""
        from middleware.concurrent_limit import release_lock

        await release_lock(room_id=1, redis=mock_redis)

        mock_redis.delete.assert_called_once()

    async def test_release_redis_error_does_not_raise(self, mock_redis):
        """Redis 장애 시 예외를 발생시키지 않는다."""
        mock_redis.delete.side_effect = Exception("redis down")

        from middleware.concurrent_limit import release_lock

        await release_lock(room_id=1, redis=mock_redis)  # 예외 없음


class TestCheckConcurrentLimit:
    async def test_already_running_raises_409(self, mock_redis):
        """이미 실행 중이면 HTTPException 409를 발생시킨다."""
        mock_redis.set.return_value = None  # 획득 실패

        from middleware.concurrent_limit import check_concurrent_limit
        from fastapi import HTTPException
        from core.config import settings

        with patch.object(settings, "concurrent_limit_enabled", True):
            with pytest.raises(HTTPException) as exc_info:
                await check_concurrent_limit(room_id=1, redis=mock_redis)

        assert exc_info.value.status_code == 409

    async def test_disabled_skips_check(self, mock_redis):
        """concurrent_limit_enabled=False면 Redis를 호출하지 않는다."""
        from middleware.concurrent_limit import check_concurrent_limit
        from core.config import settings

        with patch.object(settings, "concurrent_limit_enabled", False):
            await check_concurrent_limit(room_id=1, redis=mock_redis)

        mock_redis.set.assert_not_called()
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
cd /Users/vito/study/on-seoul-agent/on-seoul-agent
uv run pytest tests/test_concurrent_limit.py -v
```
Expected: `ImportError` (아직 구현 없음)

- [ ] **Step 3: `middleware/concurrent_limit.py` 구현**

```python
"""room_id 기준 LangGraph 동시 실행 제한.

SET NX EX 패턴으로 room_id당 동시 실행을 1개로 제한한다.
목적: LangGraph 중복 실행 방지 (비용 보호가 아닌 실행 안정성).

- key: concurrent:room:{room_id}
- lock TTL: settings.concurrent_limit_ttl (기본 120초)
  프로세스 크래시 시 TTL 만료로 자동 해제됨 (이중 보호)
- Redis 장애 시 fail-open (실행 차단하지 않음)
"""

import logging

import redis.asyncio as aioredis
from fastapi import HTTPException

from core.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "concurrent:room:"


async def acquire_lock(room_id: int, redis: aioredis.Redis) -> bool:
    """lock 획득을 시도한다.

    Returns:
        True: 획득 성공 또는 Redis 장애(fail-open)
        False: 이미 실행 중
    """
    key = f"{_KEY_PREFIX}{room_id}"
    try:
        result = await redis.set(key, "1", nx=True, ex=settings.concurrent_limit_ttl)
        return result is True
    except Exception:
        logger.warning("concurrent limit Redis 오류 — fail-open으로 실행 허용", exc_info=True)
        return True


async def release_lock(room_id: int, redis: aioredis.Redis) -> None:
    """lock을 해제한다. 장애 시 무시 (TTL로 자동 만료됨)."""
    key = f"{_KEY_PREFIX}{room_id}"
    try:
        await redis.delete(key)
    except Exception:
        logger.warning("concurrent limit lock 해제 오류 — TTL 만료로 자동 해제 예정", exc_info=True)


async def check_concurrent_limit(room_id: int, redis: aioredis.Redis) -> None:
    """동시 실행 중이면 HTTPException(409)을 발생시킨다.

    호출 측에서 실행 완료 후 반드시 release_lock()을 호출해야 한다.
    """
    if not settings.concurrent_limit_enabled:
        return

    acquired = await acquire_lock(room_id=room_id, redis=redis)
    if not acquired:
        raise HTTPException(
            status_code=409,
            detail="이미 처리 중인 요청이 있습니다. 잠시 후 다시 시도해 주세요.",
        )
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/test_concurrent_limit.py -v
```
Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
git add middleware/concurrent_limit.py tests/test_concurrent_limit.py
git commit -m "feat: room_id 기준 Concurrent Limit 구현 (SET NX EX, fail-open)"
```

---

## Task 3: Answer Cache 구현

**Files:**
- Create: `core/cache.py`
- Create: `tests/test_answer_cache.py`

### 캐시 키 설계

```
key: answer_cache:{sha256(message.strip().lower())[:16]}
value: JSON — {message_id, answer, intent, title}
TTL: settings.answer_cache_ttl (기본 3600초)
```

> `message_id`는 캐시 hit 시 요청의 `message_id`로 덮어쓴다.
> `title`은 첫 메시지(message_id==1)에서만 생성되므로 캐시 hit 시에도 그대로 전달한다.
> error / workflow_error 응답은 캐싱하지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_answer_cache.py` 파일을 아래 내용으로 생성한다.

```python
"""core/cache.py — Answer Cache 단위 테스트."""

import json
import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture
def mock_redis():
    client = AsyncMock()
    return client


@pytest.fixture
def sample_payload():
    return {
        "message_id": 3,
        "answer": "따릉이는 서울시 공공자전거입니다.",
        "intent": "VECTOR_SEARCH",
        "title": None,
    }


class TestGetCachedAnswer:
    async def test_cache_miss_returns_none(self, mock_redis):
        """캐시에 값이 없으면 None을 반환한다."""
        mock_redis.get.return_value = None

        from core.cache import get_cached_answer

        result = await get_cached_answer(message="따릉이 뭐야?", redis=mock_redis)
        assert result is None

    async def test_cache_hit_returns_payload(self, mock_redis, sample_payload):
        """캐시에 값이 있으면 dict를 반환한다."""
        mock_redis.get.return_value = json.dumps(sample_payload)

        from core.cache import get_cached_answer

        result = await get_cached_answer(message="따릉이 뭐야?", redis=mock_redis)
        assert result == sample_payload

    async def test_cache_key_normalizes_whitespace(self, mock_redis):
        """앞뒤 공백이 달라도 동일한 캐시 키를 사용한다."""
        from core.cache import _cache_key

        assert _cache_key("따릉이 뭐야?") == _cache_key("  따릉이 뭐야?  ")

    async def test_disabled_returns_none_without_redis_call(self, mock_redis):
        """answer_cache_enabled=False면 Redis를 호출하지 않고 None을 반환한다."""
        from core.cache import get_cached_answer
        from core.config import settings

        with patch.object(settings, "answer_cache_enabled", False):
            result = await get_cached_answer(message="따릉이 뭐야?", redis=mock_redis)

        assert result is None
        mock_redis.get.assert_not_called()

    async def test_redis_error_returns_none(self, mock_redis):
        """Redis 장애 시 None을 반환한다 (fail-open)."""
        mock_redis.get.side_effect = Exception("redis down")

        from core.cache import get_cached_answer
        from core.config import settings

        with patch.object(settings, "answer_cache_enabled", True):
            result = await get_cached_answer(message="따릉이 뭐야?", redis=mock_redis)

        assert result is None


class TestSetCachedAnswer:
    async def test_set_stores_json_with_ttl(self, mock_redis, sample_payload):
        """payload를 JSON으로 직렬화하여 TTL과 함께 저장한다."""
        from core.cache import set_cached_answer
        from core.config import settings

        with patch.object(settings, "answer_cache_enabled", True):
            await set_cached_answer(
                message="따릉이 뭐야?",
                payload=sample_payload,
                redis=mock_redis,
            )

        mock_redis.set.assert_called_once()
        call_kwargs = mock_redis.set.call_args[1]
        assert call_kwargs.get("ex") == settings.answer_cache_ttl

    async def test_disabled_skips_set(self, mock_redis, sample_payload):
        """answer_cache_enabled=False면 Redis set을 호출하지 않는다."""
        from core.cache import set_cached_answer
        from core.config import settings

        with patch.object(settings, "answer_cache_enabled", False):
            await set_cached_answer(
                message="따릉이 뭐야?",
                payload=sample_payload,
                redis=mock_redis,
            )

        mock_redis.set.assert_not_called()

    async def test_redis_error_does_not_raise(self, mock_redis, sample_payload):
        """Redis 장애 시 예외를 발생시키지 않는다 (fail-open)."""
        mock_redis.set.side_effect = Exception("redis down")

        from core.cache import set_cached_answer
        from core.config import settings

        with patch.object(settings, "answer_cache_enabled", True):
            await set_cached_answer(
                message="따릉이 뭐야?",
                payload=sample_payload,
                redis=mock_redis,
            )  # 예외 없음
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/test_answer_cache.py -v
```
Expected: `ImportError`

- [ ] **Step 3: `core/cache.py` 구현**

```python
"""Answer Cache — Redis 기반 최종 응답 캐싱.

캐시 키: answer_cache:{sha256(normalized_message)[:16]}
값: JSON 직렬화된 answer payload
TTL: settings.answer_cache_ttl

Redis 장애 시 항상 fail-open (캐시 없는 것처럼 동작).
error / workflow_error 응답은 호출 측에서 캐싱하지 않는다.
"""

import hashlib
import json
import logging

import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "answer_cache:"


def _cache_key(message: str) -> str:
    """메시지를 정규화하여 캐시 키를 생성한다."""
    normalized = message.strip().lower()
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"{_KEY_PREFIX}{digest}"


async def get_cached_answer(
    message: str,
    redis: aioredis.Redis,
) -> dict | None:
    """캐시된 answer payload를 반환한다. 없거나 장애 시 None."""
    if not settings.answer_cache_enabled:
        return None

    key = _cache_key(message)
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        logger.warning("answer cache GET 오류 — cache miss로 처리", exc_info=True)
        return None


async def set_cached_answer(
    message: str,
    payload: dict,
    redis: aioredis.Redis,
) -> None:
    """answer payload를 캐시에 저장한다. 장애 시 무시."""
    if not settings.answer_cache_enabled:
        return

    key = _cache_key(message)
    try:
        await redis.set(key, json.dumps(payload, ensure_ascii=False), ex=settings.answer_cache_ttl)
    except Exception:
        logger.warning("answer cache SET 오류 — 캐싱 건너뜀", exc_info=True)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/test_answer_cache.py -v
```
Expected: 9 passed

- [ ] **Step 5: 커밋**

```bash
git add core/cache.py tests/test_answer_cache.py
git commit -m "feat: Answer Cache 구현 (sha256 키, TTL, fail-open)"
```

---

## Task 4: `routers/chat.py` 통합

**Files:**
- Modify: `routers/chat.py`
- Modify: `tests/test_chat_router.py`

### 흐름 변경

```
POST /chat/stream
  ├─ 1. Answer Cache 확인 → hit: SSE final 즉시 반환 (lock 불필요)
  ├─ 2. check_concurrent_limit(room_id) → 409 or lock 획득
  ├─ 3. LangGraph 실행 → SSE 스트리밍 → set_cached_answer(payload)
  └─ 4. finally: release_lock(room_id)
```

> Cache hit 시에는 LangGraph를 실행하지 않으므로 lock을 획득할 필요가 없다.
> error / workflow_error 결과는 캐싱하지 않는다.

- [ ] **Step 1: `routers/chat.py` 수정**

현재 파일의 import 블록에 추가:

```python
from fastapi import APIRouter, HTTPException

from core.cache import get_cached_answer, set_cached_answer
from core.redis import get_redis
from middleware.concurrent_limit import check_concurrent_limit, release_lock
```

`_stream` 함수를 아래와 같이 교체한다:

```python
async def _stream(request: ChatRequest) -> AsyncGenerator[bytes, None]:
    """워크플로우를 실행하고 SSE 프레임을 yield한다."""
    redis = get_redis()
    lock_acquired = False
    try:
        # 1. Answer Cache 확인 (lock 획득 전 — cache hit이면 LangGraph 불필요)
        cached = await get_cached_answer(message=request.message, redis=redis)
        if cached is not None:
            cached["message_id"] = request.message_id  # message_id는 요청값으로 덮어씀
            yield sse_frame("final", cached)
            return

        # 2. Concurrent Limit — room_id당 동시 실행 1개 제한
        await check_concurrent_limit(room_id=request.room_id, redis=redis)
        lock_acquired = True

        # 3. LangGraph 실행
        state = AgentState(
            room_id=request.room_id,
            message_id=request.message_id,
            message=request.message,
            title_needed=(request.message_id == 1),
            intent=None,
            lat=request.lat,
            lng=request.lng,
            refined_query=None,
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
                state,
                data_session=data_session,
                ai_session=ai_session,
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
                    }
                    if result.get("error"):
                        logger.error("workflow error: %s", result["error"])
                        payload["error"] = "서비스 처리 중 오류가 발생했습니다."
                        yield sse_frame("workflow_error", payload)
                    else:
                        # 정상 응답만 캐싱
                        await set_cached_answer(
                            message=request.message,
                            payload=payload,
                            redis=redis,
                        )
                        yield sse_frame("final", payload)

    except HTTPException as e:
        yield sse_frame("error", {"message": e.detail, "status_code": e.status_code})
    except Exception:
        logger.exception("워크플로우 실행 중 오류")
        yield sse_frame("error", {"message": "서비스 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."})
    finally:
        if lock_acquired:
            await release_lock(room_id=request.room_id, redis=redis)
        await redis.aclose()
```

- [ ] **Step 2: 기존 테스트 전체 통과 확인**

```bash
uv run pytest tests/test_chat_router.py -v
```
Expected: 모두 통과

- [ ] **Step 3: Concurrent Limit / Cache 시나리오 테스트 추가**

`tests/test_chat_router.py` 끝에 아래 클래스를 추가한다:

```python
class TestConcurrentLimitIntegration:
    """Concurrent Limit이 적용된 /chat/stream 통합 시나리오."""

    async def test_concurrent_limit_returns_error_sse(self, async_client):
        """동시 실행 중일 때 error SSE 이벤트를 반환한다."""
        from fastapi import HTTPException

        with (
            patch("routers.chat._get_graph"),
            patch("routers.chat.get_cached_answer", return_value=None),
            patch(
                "routers.chat.check_concurrent_limit",
                side_effect=HTTPException(status_code=409, detail="이미 처리 중인 요청이 있습니다."),
            ),
            patch("routers.chat.get_redis", return_value=AsyncMock()),
        ):
            resp = await async_client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        assert resp.status_code == 200  # SSE는 항상 200
        assert b"error" in resp.content
        assert "409" in resp.text or "처리 중" in resp.text


class TestAnswerCacheIntegration:
    """Answer Cache가 적용된 /chat/stream 통합 시나리오."""

    async def test_cache_hit_returns_final_without_graph(self, async_client):
        """캐시 hit 시 graph를 호출하지 않고 final 이벤트를 반환한다."""
        cached_payload = {
            "message_id": 1,
            "answer": "캐시된 답변입니다.",
            "intent": "VECTOR_SEARCH",
            "title": None,
        }

        mock_graph = AsyncMock()

        with (
            patch("routers.chat._get_graph", return_value=mock_graph),
            patch("routers.chat.get_cached_answer", return_value=cached_payload),
            patch("routers.chat.get_redis", return_value=AsyncMock()),
        ):
            resp = await async_client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "따릉이 뭐야?"},
            )

        assert resp.status_code == 200
        assert b"final" in resp.content
        assert b"캐시된 답변" in resp.content
        mock_graph.stream.assert_not_called()

    async def test_cache_miss_calls_graph_and_caches_result(self, async_client):
        """캐시 miss 시 graph를 호출하고 결과를 캐싱한다."""
        from schemas.state import IntentType

        mock_stream_result = [
            ("result", {
                "message_id": 1,
                "answer": "새로운 답변입니다.",
                "intent": IntentType.VECTOR_SEARCH,
                "title": None,
                "error": None,
            })
        ]

        async def _mock_stream(*args, **kwargs):
            for item in mock_stream_result:
                yield item

        mock_graph = MagicMock()
        mock_graph.stream = _mock_stream
        mock_set_cache = AsyncMock()

        with (
            patch("routers.chat._get_graph", return_value=mock_graph),
            patch("routers.chat.get_cached_answer", return_value=None),
            patch("routers.chat.check_concurrent_limit", return_value=None),
            patch("routers.chat.release_lock", return_value=None),
            patch("routers.chat.set_cached_answer", mock_set_cache),
            patch("routers.chat.get_redis", return_value=AsyncMock()),
            patch("routers.chat.data_session_ctx"),
            patch("routers.chat.ai_session_ctx"),
        ):
            resp = await async_client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "따릉이 뭐야?"},
            )

        assert resp.status_code == 200
        assert b"final" in resp.content
        mock_set_cache.assert_called_once()

    async def test_lock_released_on_exception(self, async_client):
        """LangGraph 실행 중 예외 발생 시에도 lock이 해제된다."""
        mock_release = AsyncMock()

        with (
            patch("routers.chat.get_cached_answer", return_value=None),
            patch("routers.chat.check_concurrent_limit", return_value=None),
            patch("routers.chat.release_lock", mock_release),
            patch("routers.chat.get_redis", return_value=AsyncMock()),
            patch("routers.chat.data_session_ctx", side_effect=Exception("db error")),
            patch("routers.chat.ai_session_ctx"),
        ):
            resp = await async_client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        assert resp.status_code == 200
        assert b"error" in resp.content
        mock_release.assert_called_once()
```

- [ ] **Step 4: 전체 테스트 통과 확인**

```bash
uv run pytest tests/test_chat_router.py tests/test_concurrent_limit.py tests/test_answer_cache.py -v
```
Expected: 모두 통과

- [ ] **Step 5: 전체 테스트 회귀 확인**

```bash
uv run pytest -v
```
Expected: 기존 포함 전체 통과

- [ ] **Step 6: 커밋**

```bash
git add routers/chat.py tests/test_chat_router.py
git commit -m "feat: /chat/stream에 Concurrent Limit · Answer Cache 통합"
```

---

## 완료 기준 체크리스트

- [ ] `uv run pytest -v` 전체 통과
- [ ] `CONCURRENT_LIMIT_ENABLED=false` 환경변수로 Concurrent Limit 비활성화 가능
- [ ] `ANSWER_CACHE_ENABLED=false` 환경변수로 캐시 비활성화 가능
- [ ] Redis 장애 시 서비스 정상 동작 (fail-open)
- [ ] LangGraph 실행 중 예외 발생 시에도 lock 해제 보장 (finally 블록)
- [ ] error / workflow_error 응답은 캐싱하지 않음
- [ ] cache hit 시 lock을 획득하지 않음 (불필요한 Redis 호출 없음)
- [ ] `uv run ruff check .` 린트 통과

---

## 참고: API 서비스 Rate Limiting

RPM 기반 Rate Limiting은 **Spring Boot API 서비스**에서 구현한다.

- 위치: `on-seoul-api` — Spring Security 필터 체인 또는 인터셉터
- 기준: JWT에서 추출한 사용자 ID (room_id가 아닌 user 기준)
- 권장 라이브러리: `bucket4j-spring-boot-starter` (Redis 백엔드 지원)
- 알고리즘: Sliding Window Counter 또는 Token Bucket (API 서비스는 처리 시간이 일정하므로 적합)
