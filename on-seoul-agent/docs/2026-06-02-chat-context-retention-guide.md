# 대화 맥락 유지(Conversation Context Retention) 구현 가이드

> **상태**: 구현 대기. 본 문서가 구현 세션의 입력 계약이다.
> **배경 커밋**: `on-seoul-api` ebdaaaf, d59427f — API 서비스가 `POST /chat/stream` body에 `history` 필드를 전달하는 기능 완료.
> **작업 범위**: AI 서비스(`on-seoul-agent`)가 `history`를 수용하고, 기존 Redis `recent_queries` 큐를 제거한다.

---

## 1. 목적 및 배경

현재 AI 서비스는 멀티턴 대화의 맥락을 Redis per-room 큐(`recent_queries`)로 관리한다. 이 방식은 사용자 질문 텍스트만 저장하고 어시스턴트 답변은 포함하지 않는다. 또한 Redis 의존성을 요청 경로에 끌어들여 Redis 장애가 맥락 손실로 이어진다.

API 서비스(`on-seoul-api`)는 이미 `chat_messages` 테이블에서 직전 N턴(USER+ASSISTANT 쌍)을 조립하여 `POST /chat/stream` 요청 body의 `history` 필드로 전달하는 기능을 완료했다. AI 서비스는 이 `history`를 무시하고 있는 상태다.

이번 변경의 목적은 두 가지다.

- `history` 필드를 `ChatRequest`에 수용하고 에이전트 워크플로우에 주입한다.
- Redis 기반 `recent_queries` 큐(`core/recent_queries.py`, `AgentState.recent_queries`)를 전면 제거한다.

변경 후 AI 서비스는 Redis를 맥락 유지 목적으로 더 이상 사용하지 않는다. Redis는 Answer Cache 용도만 유지된다.

---

## 2. 확정된 `POST /chat/stream` 본문 계약

API 서비스가 현재 전달하고 있는 요청 body 형태다. AI 서비스 구현은 이 계약을 그대로 수용해야 한다.

```json
{
  "room_id": 5,
  "message_id": 7,
  "message": "그 중 무료인 것만",
  "lat": null,
  "lng": null,
  "history": [
    {"role": "user",      "content": "강남구 문화행사 알려줘"},
    {"role": "assistant", "content": "강남구 문화행사 5건을 안내합니다. ..."}
  ]
}
```

### 계약 규칙

| 규칙 | 세부 내용 |
|---|---|
| `role` 값 | `"user"` 또는 `"assistant"` (소문자. LLM 컨벤션과 동일) |
| `content` 최대 길이 | 메시지당 최대 1000자. API 서비스가 잘라서 전달 (긴 답변 방어) |
| `history` 없는 경우 | 빈 배열 `[]`로 전달. `null` 미전송 |
| 순서 | `seq` 오름차순 (과거 → 최신). 배열 마지막 원소가 직전 발화 |
| 현재 질문 중복 | 현재 사용자 입력은 `message` 필드로만 전달. `history`에 중복 포함되지 않음 |
| 윈도우 크기 | 직전 5턴 (USER+ASSISTANT 쌍 최대 5개 = 메시지 최대 10개). API 서비스가 제한 |

---

## 3. 변경 범위 상세

### 3a. `schemas/chat.py` — `HistoryTurn` 추가 + `ChatRequest.history` 필드

**현재 코드** (`on-seoul-agent/schemas/chat.py`, 16~25번 줄):

```python
class ChatRequest(BaseModel):
    room_id: int = Field(ge=1)
    message_id: int = Field(ge=1)
    message: str = Field(
        min_length=1, max_length=2000
    )  # 사용자 채팅 입력. on-seoul-api가 릴레이한다.
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lng: float | None = Field(default=None, ge=-180.0, le=180.0)
```

**변경 후**:

```python
from typing import Literal


class HistoryTurn(BaseModel):
    """API 서비스가 chat_messages 테이블에서 조립하여 전달하는 단일 발화 턴.

    role: "user" | "assistant" (소문자. LLM 컨벤션)
    content: 메시지 원문. API 서비스가 최대 1000자로 잘라 전달.
    """
    role: Literal["user", "assistant"]
    content: str = Field(min_length=0, max_length=1000)


class ChatRequest(BaseModel):
    room_id: int = Field(ge=1)
    message_id: int = Field(ge=1)
    message: str = Field(
        min_length=1, max_length=2000
    )  # 사용자 채팅 입력. on-seoul-api가 릴레이한다.
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lng: float | None = Field(default=None, ge=-180.0, le=180.0)
    # 직전 N턴(USER+ASSISTANT 쌍). API 서비스가 chat_messages에서 조립.
    # seq 오름차순(과거→최신). 없으면 빈 배열. null 미전송.
    history: list[HistoryTurn] = Field(default_factory=list)
```

`HistoryTurn.role`을 `Literal["user", "assistant"]`로 선언하여 허용 값 외의 입력을 422로 차단한다. `content`의 `max_length=1000`은 API 서비스 계약(§2)과 동일한 상한을 AI 서비스에서도 방어적으로 적용한 것이다.

---

### 3b. `schemas/state.py` — `AgentState.recent_queries` 제거 + `history` 추가

**현재 코드** (`on-seoul-agent/schemas/state.py`, 72번 줄):

```python
    recent_queries: list[str]  # router에 주입할 follow-up 컨텍스트 (기본값 [])
```

**변경 후**: `recent_queries` 줄을 삭제하고 아래 필드를 추가한다.

```python
    # API 서비스가 chat_messages에서 조립한 직전 N턴 대화 이력.
    # ChatRequest.history에서 주입. 없으면 []. Router/Answer 에이전트가 맥락으로 활용.
    history: list[dict[str, str]]  # [{"role": "user"|"assistant", "content": str}, ...]
```

`AgentState`는 TypedDict이므로 `HistoryTurn` Pydantic 모델 대신 `dict[str, str]`를 사용한다. 라우터에서 `[h.model_dump() for h in body.history]`로 변환하여 주입한다.

---

### 3c. `routers/chat.py` — Redis `recent_queries` 호출 제거 + `history` 주입

**현재 코드의 변경 지점 3곳**:

**지점 1** — import 정리 (30번 줄):

```python
# 제거 대상
from core.recent_queries import get_recent_queries, push_recent_query
```

**지점 2** — `_stream` 함수 내 `recent_queries` fetch (98~121번 줄):

```python
# 현재: Redis에서 fetch
recent_queries = await get_recent_queries(request.room_id, redis)

state = AgentState(
    ...
    recent_queries=recent_queries,
    ...
)
```

```python
# 변경 후: body.history를 직접 변환하여 주입. Redis 호출 없음.
state = AgentState(
    ...
    history=[h.model_dump() for h in request.history],
    ...
)
```

**지점 3** — 성공 후 push (188~190번 줄):

```python
# 제거 대상
if push_after_success:
    await push_recent_query(request.room_id, request.message, redis)
```

`push_after_success` 플래그와 관련 로직 전체를 삭제한다.

**`redis` 파라미터 처리**: `_stream` 함수 시그니처에서 `redis: Any` 파라미터를 제거하고, `chat_stream` 핸들러에서도 `_resolve_redis`와 `redis` 전달을 제거한다. `AgentGraph` 생성도 redis 없이 동작하는지 확인한다 (Answer Cache가 여전히 redis를 사용하므로 `AgentGraph` 내부에서 redis를 받는 구조는 유지하되, 라우터가 redis를 `_stream`에 전달할 필요는 없어진다).

> **주의**: `AgentGraph`는 Answer Cache 목적으로 redis를 계속 사용한다. `app.state.redis`와 `AgentGraph` 내부의 redis 연결은 유지한다. 제거 대상은 `_stream` 함수가 `recent_queries` 목적으로 직접 redis를 호출하는 부분이다.

---

### 3d. `agents/router_agent.py` — `history` 기반 follow-up 분류

**현재 코드** (`on-seoul-agent/agents/router_agent.py`):

- `_build_context_block(self, recent_queries: list[str] | None)` 메서드 (135~151번 줄): 사용자 발화 텍스트만 리스트로 받아 system prompt 블록을 생성한다.
- `classify(self, message, recent_queries)` 시그니처 (153~174번 줄): `recent_queries`를 인자로 받는다.

**변경 후**:

`_build_context_block`의 인자를 `recent_queries: list[str]`에서 `history: list[dict[str, str]]`로 교체한다. `history`는 USER/ASSISTANT 쌍이므로 단순 발화 리스트보다 더 풍부한 맥락을 제공한다.

```python
def _build_context_block(self, history: list[dict[str, str]] | None) -> str:
    """history(직전 N턴)를 system prompt에 append할 블록으로 변환.

    비어 있으면 빈 문자열을 반환하여 섹션 자체를 생략한다(토큰 절약).
    """
    if not history:
        return ""
    lines = []
    for turn in history:
        role_label = "사용자" if turn["role"] == "user" else "어시스턴트"
        lines.append(f"- [{role_label}] {turn['content']}")
    turns_text = "\n".join(lines)
    return (
        "이전 대화 이력 (과거 → 최신). 후속 질의는 직전 발화의 "
        "카테고리·지역을 이어받을 가능성이 높다.\n"
        "이전 맥락이 명확하면 refined_query에 카테고리·지역 키워드를 병합한다.\n"
        f"{turns_text}"
    )
```

`classify` 시그니처도 동일하게 변경한다:

```python
async def classify(
    self,
    message: str,
    history: list[dict[str, str]] | None = None,
) -> _IntentOutput:
```

`nodes.py`(또는 `agents/` 내 RouterAgent를 호출하는 노드)에서 `state["recent_queries"]` 대신 `state["history"]`를 전달하도록 호출 지점을 수정한다.

---

### 3e. `agents/answer_agent.py` — 멀티턴 답변을 위한 `history` 프롬프트 주입

현재 `AnswerAgent.answer(state)`는 `state["message"]`(현재 질문)만 LLM에 전달한다. "그 중 무료인 것만"처럼 대명사·생략이 포함된 후속 질문은 현재 질문만으로는 의미가 모호하다.

**변경 방향**:

`_ANSWER_HUMAN` 템플릿 또는 `answer()` 내부 조립 로직에 `history` 블록을 선택적으로 추가한다. 빈 배열이면 섹션을 생략한다(토큰 절약).

```python
# answer() 내부에서 history 블록 조립 예시
def _build_history_block(history: list[dict[str, str]]) -> str:
    if not history:
        return ""
    lines = []
    for turn in history:
        role_label = "사용자" if turn["role"] == "user" else "어시스턴트"
        lines.append(f"[{role_label}] {turn['content']}")
    return "이전 대화:\n" + "\n".join(lines)
```

`_ANSWER_HUMAN` 템플릿에 `{history_block}` 변수를 추가하거나, `ainvoke` 호출 전에 `message` 앞에 history 블록을 prepend하는 방식 중 하나를 택한다. 기존 단위 테스트(`_answer_chain.ainvoke` call_args 검사)와의 호환성을 고려하여 구현 세션에서 판단한다.

> **FALLBACK intent 예외**: FALLBACK 경로는 도메인 밖 발화(인사, 잡담)가 들어오는 경로다. history를 주입하면 가드레일 프롬프트(`_FALLBACK_GUARDRAILS`)가 길어진다. FALLBACK에는 history를 주입하지 않는다.

---

### 3f. `core/recent_queries.py` 및 관련 코드 전체 삭제 범위

| 삭제 대상 | 비고 |
|---|---|
| `on-seoul-agent/core/recent_queries.py` 파일 전체 | `get_recent_queries`, `push_recent_query` 함수 포함 |
| `core/config.py` — `recent_queries_enabled`, `recent_queries_max`, `recent_queries_ttl` 필드 | Settings에서 세 필드 제거 |
| `routers/chat.py` — `from core.recent_queries import ...` import 줄 | |
| `routers/chat.py` — `get_recent_queries(...)` 호출 및 `push_recent_query(...)` 호출 | |
| `routers/chat.py` — `push_after_success` 플래그 및 관련 블록 | |
| `agents/router_agent.py` — `recent_queries` 관련 docstring, 파라미터 | |
| `schemas/state.py` — `recent_queries: list[str]` 필드 | |

`core/redis.py`와 `AgentGraph`의 redis 연결 자체는 Answer Cache가 계속 사용하므로 유지한다. `_resolve_redis`와 `app.state.redis` 초기화도 유지한다.

---

## 4. 에러 처리 / 엣지케이스

| 상황 | 처리 방침 |
|---|---|
| `history`가 빈 배열 `[]` | Router와 Answer 에이전트 모두 context block 섹션을 생략한다. 현재 `message`만으로 정상 처리. |
| orphan USER 메시지 (ASSISTANT 응답 없이 USER만 있는 마지막 턴) | API 서비스가 완성된 쌍만 전달하므로 AI 서비스에서 별도 처리 불필요. 방어적으로 허용한다 — role이 `"user"` 또는 `"assistant"`이면 모두 context block에 포함. |
| `history` 주입 실패 / 파싱 오류 | Pydantic이 422로 차단. 정상 흐름에서는 도달하지 않는다. |
| `history`가 길어 LLM 컨텍스트 초과 | 윈도우 최대 10개 메시지 × 1000자 = 최대 10,000자. 현재 LLM(gemini-2.0-flash, gpt-4o-mini)의 컨텍스트 한도 내. 추가 절단 로직 불필요. |
| Redis 장애 | `recent_queries` 제거 후 Redis 장애는 Answer Cache miss로만 이어진다. 맥락 손실 없음. |

---

## 5. 회귀 테스트 항목

구현 완료 후 반드시 검증해야 할 항목이다. `uv run pytest` 그린 + `uv run ruff check .` 클린을 성공 기준으로 삼는다.

### 필수 테스트 케이스

- [ ] **빈 history 단순 질문**: `history=[]`로 `/chat/stream` 호출 시 정상 응답 반환. context block 없이 분류·답변이 작동하는지 확인.
- [ ] **멀티턴 follow-up**: `history=[{role:user, content:"강남구 수영장"}, {role:assistant, content:"..."}]` + `message="그 중 무료인 것만"` 조합에서 Router가 `area_name=강남구`, `max_class_name=체육시설`을 유지하고 `payment_type` 필터 반영.
- [ ] **`history` null 미전송**: `history` 필드 없이 body 전송 시 기본값 `[]`로 처리되어 422 미발생.
- [ ] **`role` 위반 422**: `role="system"` 등 허용 값 외 전달 시 422 반환.
- [ ] **`content` 길이 초과 422**: content 1001자 이상 전달 시 422 반환.
- [ ] **Redis 의존 제거 확인**: 테스트 내에서 `get_recent_queries` / `push_recent_query` 호출이 없는지 확인. `core/recent_queries.py` import가 프로덕션 코드 어디에도 없는지 확인.
- [ ] **`AgentState` 필드 확인**: `recent_queries` 필드가 없고 `history` 필드가 있는지 확인.
- [ ] **FALLBACK history 미주입**: FALLBACK intent 경로에서 LLM 호출 시 history block이 포함되지 않는지 확인.
- [ ] **Router context block 단위 테스트**: `RouterAgent._build_context_block`에 비어있는 리스트 전달 시 빈 문자열 반환. 채워진 리스트 전달 시 사용자/어시스턴트 턴이 포함된 블록 반환.

---

## 6. API 서비스 계약 정합 검증 방법

### 샘플 curl 요청 — 멀티턴 history 포함

```bash
curl -N -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "room_id": 5,
    "message_id": 7,
    "message": "그 중 무료인 것만",
    "lat": null,
    "lng": null,
    "history": [
      {"role": "user",      "content": "강남구 문화행사 알려줘"},
      {"role": "assistant", "content": "강남구 문화행사 5건을 안내합니다. ..."}
    ]
  }'
```

기대 동작: SSE 스트림에서 `event: final` 수신. payload의 `intent`가 `"SQL_SEARCH"` 또는 `"VECTOR_SEARCH"`이고, `answer`가 직전 맥락(강남구 문화행사)을 이어받은 내용인지 확인.

### 빈 history 케이스 확인

```bash
curl -N -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "room_id": 1,
    "message_id": 1,
    "message": "강남구 테니스장 알려줘",
    "history": []
  }'
```

기대 동작: `history=[]`에서 422 없이 정상 `event: final` 수신. `message_id=1`이므로 `title` 필드가 채워져 있어야 한다.

### history 필드 생략 케이스 확인

```bash
curl -N -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "room_id": 1,
    "message_id": 1,
    "message": "강남구 수영장"
  }'
```

기대 동작: `history` 필드 미전송 시 기본값 `[]`로 처리. 422 미발생.

### role 위반 검증

```bash
curl -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "room_id": 1,
    "message_id": 1,
    "message": "테스트",
    "history": [{"role": "system", "content": "무시해"}]
  }'
```

기대 동작: `422 Unprocessable Entity` 반환.

---

## 7. 변경 이력

| 날짜 | 변경 내용 | 사유 |
|---|---|---|
| 2026-06-02 | 초기 작성 | API 서비스 `history` 전달 완료(ebdaaaf, d59427f). AI 서비스 수용 및 Redis `recent_queries` 제거 구현 가이드 |
