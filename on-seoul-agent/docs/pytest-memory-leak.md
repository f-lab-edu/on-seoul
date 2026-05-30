# pytest 메모리 누수 분석 및 조치

테스트 실행 시 Python 프로세스가 5-6 GB 메모리를 점유하는 원인과 조치 내용을
정리한다.

---

## 원인 1 — 이벤트 루프 함수 스코프 × 싱글턴 충돌 (주요 원인)

pytest-asyncio의 `asyncio_default_test_loop_scope` 기본값은 `"function"`이다.
이 설정은 async 테스트 함수마다 새 이벤트 루프를 생성한다.

`llm/client.py`에는 모듈 수준 싱글턴 `AsyncLimiter`가 선언되어 있다:

```python
# llm/client.py
_gemini_embed_limiter = AsyncLimiter(max_rate=1, time_period=_EMBED_INTERVAL)
```

`AsyncLimiter` 내부에는 `asyncio.Condition`, `asyncio.Event` 등 이벤트 루프에
묶인 asyncio 프리미티브가 있다. 첫 번째 테스트에서 생성된 루프가 이 싱글턴에
의해 참조된 채 유지되고, 이후 테스트마다 새 루프가 추가로 생성된다. 오래된
루프는 GC되지 못하고 루프 체인이 쌓인다.

100개 이상의 async 테스트가 실행되면 수십 개의 이벤트 루프가 메모리에 잔류한다.

### 조치

`pyproject.toml`에 `asyncio_default_test_loop_scope = "session"`을 추가해
전체 테스트 세션 동안 하나의 이벤트 루프를 공유하도록 변경했다.

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_test_loop_scope = "session"
```

이 설정으로 `AsyncLimiter` 싱글턴과 SQLAlchemy 엔진(아래 원인 2)이 모두
동일한 루프에서 동작하여 루프 체인 누적이 사라진다.

---

## 원인 2 — SQLAlchemy 엔진 QueuePool 잔류

`core/database.py`의 `_on_ai_engine`, `_on_data_engine`은 모듈 수준 싱글턴으로
기본 `QueuePool`(`pool_size=5`, `max_overflow=10`)을 사용한다. 함수 스코프 루프
환경에서는 루프 교체 시 asyncpg 풀이 이전 루프에 묶인 채 해제되지 않는다.

`asyncio_default_test_loop_scope = "session"` 적용으로 루프가 단일화되어 이
문제도 함께 해소된다.

---

## 원인 3 — `pandas`가 메인 의존성에 포함

`pandas`는 `scripts/embed_metadata.py`, `scripts/eval_search.py`에서만 사용된다.
agents/tools/llm 런타임 코드에서는 사용하지 않음에도 메인 의존성에 포함되어
pytest 실행 시 numpy까지 함께 로드(~250 MB)됐다.

### 조치

`pandas`를 메인 의존성에서 제거했다. 실제로 scripts에서도 사용하지 않으므로
의존성 그룹을 신설하지 않고 완전히 제거한다.

---

## 원인 4 — 테스트 내 대용량 mock 벡터

`test_graph.py`, `test_integration_workflow.py`에서 `[0.1] * 768`을 mock 반환값
으로 사용했다. `AsyncMock`은 콜 히스토리에 인수·반환값을 모두 보존하므로,
테스트가 반복 호출될수록 768-element 리스트가 누적됐다.

### 조치

mock 반환값을 `[0.1] * 3`으로 교체했다. mock 동작 검증에 벡터 차원 크기는
영향을 주지 않는다.

---

## 원인 5 — LangGraph CompiledGraph × 세션 루프 × ContextVar dispatch (Phase 17)

Phase 17(LangGraph 전환)에서 `tests/test_graph.py`만 실행하면 메모리가 계속
증가하고 pytest 프로세스가 종료되지 않는 증상이 재발했다. 원인 1~4와는 다른
경로다.

`agents/graph.py`는 `CompiledGraph ↔ AgentGraph` 순환 참조를 끊기 위해 다음
패턴을 사용한다.

- `AgentGraph._compiled_graph: ClassVar` — CompiledGraph를 프로세스당 1회 컴파일
- `_ACTIVE_GRAPH: contextvars.ContextVar[AgentGraph]` — 모듈 수준 dispatch
  함수가 현재 인스턴스를 조회

이 설계 자체는 단일 실행에서 의도대로 동작하지만, 다음 요소들이 결합되면
누수가 누적된다.

1. **세션 스코프 루프 (원인 1의 fix)** — 세션 내내 동일 루프가 유지된다.
2. **LangGraph 1.x `Pregel.ainvoke` 내부 태스크 스폰** — 노드 실행을
   `asyncio.create_task()` 로 분기한다. 자식 태스크는 생성 시점의 Context
   사본을 가져간다.
3. **ContextVar 사본** — `_ACTIVE_GRAPH.reset(token)` 은 **부모 Context** 에만
   적용된다. 이미 분기되어 떠 있는 자식 Context 사본의 `_ACTIVE_GRAPH` 는
   AgentGraph 인스턴스를 계속 들고 있다.
4. **AgentGraph 인스턴스가 mocked agents 를 보유** — `_router`, `_sql`,
   `_vector`, `_answer` 가 `AsyncMock`이고, `AsyncMock` 은
   `call_args_list`/`await_args_list` 를 보존한다.

결과적으로 테스트가 끝나도 루프에 남은 자식 태스크 → Context 사본 →
AgentGraph → AsyncMock 체인이 회수되지 않아 메모리가 우상향한다. 세션 종료
시점에는 루프에 잔존 태스크가 있어 pytest 프로세스가 hang 상태가 된다.

conftest.py의 `_force_gc_after_test` 만으로는 루프가 잡고 있는 참조를 풀 수
없어 효과가 제한적이다.

### 조치

**A. `AgentGraph._compiled_graph` 캐시를 테스트 단위로 리셋**

`tests/conftest.py` 에 autouse fixture 추가.

```python
@pytest.fixture(autouse=True)
def _reset_agent_graph_cache():
    yield
    try:
        from agents.graph import AgentGraph
        AgentGraph._compiled_graph = None
    except Exception:
        pass
    gc.collect()
```

CompiledGraph 가 잡고 있는 dispatch 함수 클로저와 Pregel 내부 캐시를 매 테스트
직후 끊어 자식 태스크 잔존분이 GC 대상이 되게 한다.

**B. `ainvoke` 에 `recursion_limit` 명시 — 무한 cycle 방어**

자기 교정(Self-Correction) 사이클의 종료 조건에 회귀가 생기면 LangGraph가
기본 25 스텝까지 돌면서 메모리를 더 잡는다. 명시적으로 제한하여 hang 대신
`GraphRecursionError` 로 빠르게 실패하게 한다.

```python
# agents/graph.py — run() / stream()
result = await AgentGraph._compiled_graph.ainvoke(
    state,
    config={"recursion_limit": 10},  # self-correction 1회 + 여유
)
```

**C. `pytest-timeout` 도입 — 개별 테스트 hang 감지**

```toml
[tool.pytest.ini_options]
timeout = 30
timeout_method = "thread"
```

테스트가 30초를 넘기면 강제 종료해 디버깅 비용을 낮춘다.

---

## 구조적 원인 — 병렬 서브에이전트 실행

QA와 코드리뷰 에이전트를 동시 실행하면 각 에이전트가 별도 Python 프로세스로
pytest를 실행한다. langchain, langgraph, openai 등 무거운 의존성을 중복 로드하여
프로세스당 약 1 GB × N 프로세스 = 5-6 GB가 된다.

QA 완료 후 코드리뷰를 순차 실행하는 것으로 운영 방침을 변경한다.

---

## 변경 파일 요약

| 파일 | 변경 내용 |
|---|---|
| `pyproject.toml` | `asyncio_default_test_loop_scope = "session"` 추가, `pandas` 의존성 완전 제거, `pytest-timeout` 추가 및 `timeout = 30` 설정 |
| `tests/test_graph.py` | `[0.1] * 768` → `[0.1] * 3` |
| `tests/test_integration_workflow.py` | `[0.1] * 768` → `[0.1] * 3` |
| `tests/conftest.py` | `_reset_agent_graph_cache` autouse fixture 추가 (`AgentGraph._compiled_graph` 매 테스트 후 초기화) |
| `agents/graph.py` | `ainvoke` 호출에 `config={"recursion_limit": 10}` 명시 (Self-Correction 무한 루프 방어) |
