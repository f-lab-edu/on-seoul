# Chat Response Schema v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/chat/stream` SSE final 이벤트 payload를 "narrative `answer` + 구조화 `blocks`" 하이브리드 스키마로 진화시킨다. LLM은 짧은 요약·맥락만 생성하고, 시설 카드 데이터는 코드가 hydrated row에서 직접 매핑한다. 시설별 `service_url`·요금·접수상태·이용기간 등 풍부한 메타데이터를 프론트엔드에 정확하게 전달하여 카드 렌더링·액션(예약/즐겨찾기/공유)·미래 확장(지도·FAQ·후속 질문)을 모두 가능하게 한다.

**Scope:**
- 본 계획은 **응답 페이로드 스키마 진화**만 다룬다. 검색·랭킹·임베딩 변경 없음.
- LLM 비용 절감(`answer` 짧아짐) + 데이터 정확성(카드는 코드가 매핑) 두 가지 효과를 동시에 노린다.
- 하위 호환을 위해 Phase A → B → C 단계적 전환 — 기존 클라이언트가 갑자기 깨지지 않는다.

**문제 진단 (현황):**
- 현재 `ChatResponse`는 `{message_id, answer, intent, title}` 4필드 평탄 구조.
- LLM이 `answer` 텍스트에 시설명·URL을 직접 풀어 적어야 해서:
  - 시설별 `service_url`이 누락되거나 일괄 fallback(`https://yeyak.seoul.go.kr`)으로 안내됨 (2026-05-28 라이브 호출에서 확인)
  - `payment_type`, `target_info`, `min_class_name` 등 hydrated row가 가진 풍부한 정보가 답변에서 손실
  - 프론트엔드가 카드 UI를 그릴 수 없어 텍스트만 렌더 → "예약" 버튼, 상태 배지, 즐겨찾기 등 액션 추가 불가
- 2026-05-28 즉시 패치(`AnswerAgent._normalize()` 14필드 + 프롬프트 강화)는 **데이터 손실은 막았지만 LLM이 정확히 출력해주리라는 신뢰에 의존** — 환각 위험은 잔존. 본 계획은 코드가 카드를 매핑하여 이 위험을 원천 차단한다.

**Architecture:**
- **Discriminated union으로 컨텐츠 블록 분리**: `ServiceCard | MapBlock | FaqBlock | ...`. 각 블록은 `kind` 리터럴 필드로 구분되어 새 블록 타입을 추가만 해도 됨 (Open-Closed Principle).
- **LLM 책임 = narrative만**: `AnswerAgent`는 `answer`(요약·맥락) 만 생성. 시설 나열·URL·날짜는 절대 적지 않도록 프롬프트로 강제.
- **블록 매핑 = 코드**: `AnswerAgent._build_blocks()` 가 hydrated rows를 `ServiceCard` 리스트로 변환. 환각 불가 — DB 원본만 매핑.
- **`schema_version` 필드**: 단조 증가 정수. 클라이언트는 자신이 이해하는 최고 버전을 헤더로 표명하고, 서버는 클라이언트가 이해할 수 있는 형태로 직렬화.
- **하위 호환 단계적 전환**: Phase A(`blocks` 추가, `answer`는 기존 그대로) → Phase B(클라이언트 헤더 기반 라우팅) → Phase C(`answer` narrative-only로 통일).

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, LangChain (구조화 출력), pytest

**관련 문서:**
- 영향 받는 기존 문서:
  - `docs/agent-design.md` (Answer Agent 책임 변경)
  - `docs/architecture.md` (`/chat/stream` payload 구조)
  - `on-seoul-agent/README.md` (응답 예시)
  - **Spring Boot 측 `chat_messages.content` 스키마 협의 필요** — `answer` 텍스트만 그대로 저장할지, `blocks` JSON도 별도 컬럼에 보관할지 결정 (DB 마이그레이션 동반 가능)
- 봉인 평가셋 측정 결과(`scripts/eval/run_recall.py`)는 본 변경의 영향을 받지 않음 — recall 측정은 `result_ids` 기준이지 응답 텍스트 기준이 아니기 때문.

---

## File Map

| 파일 | 역할 | 변경 |
|------|------|------|
| `schemas/chat.py` | `ServiceCard` / `MapBlock` / `FaqBlock` / `ChatStreamFinalEvent v2` 정의 | 수정 |
| `schemas/state.py` | `AgentState`에 `blocks: list[dict]` 추가 (직렬화는 SSE 시점) | 수정 |
| `agents/answer_agent.py` | `_build_blocks()` 추가 + 프롬프트를 narrative-only로 (Phase C에서 전환) | 수정 |
| `routers/chat.py` | SSE final payload에 `blocks` / `schema_version` 추가. `X-Chat-Schema-Version` 헤더 라우팅 (Phase B) | 수정 |
| `tests/test_answer_agent.py` | `_build_blocks()` 단위 테스트 | 수정 |
| `tests/test_chat_router.py` | SSE final payload에 `blocks` 포함 검증 | 수정 |
| `tests/test_chat_schema_v2.py` | 신규 — 스키마 검증, discriminated union 동작, 빈 결과 처리 | 신규 |
| `docs/agent-design.md` | Answer Agent 책임 변경 명시 (LLM=narrative, 코드=blocks) | 수정 |
| `docs/architecture.md` | `/chat/stream` payload 구조 v2 명시 | 수정 |
| `on-seoul-agent/README.md` | 응답 예시 v2로 갱신 | 수정 |

---

## Task 1: `schemas/chat.py` — 응답 스키마 v2 정의

### 핵심 설계

- `kind` 리터럴로 discriminated union 구성 → Pydantic이 자동 분기
- 모든 카드 필드는 `optional` (intent별로 가지는 정보가 다름)
- `schema_version` 필드는 매 응답에 명시 — 클라이언트가 호환성 판단 가능
- `metadata` 자유 dict — A/B 실험·디버깅 슬롯

### 정의 예시

```python
# schemas/chat.py 추가

from typing import Annotated, Literal
from pydantic import BaseModel, Field


class ServiceCard(BaseModel):
    """SQL/VECTOR_SEARCH 결과 — 예약 가능 시설 카드."""
    kind: Literal["service"] = "service"
    service_id: str
    service_name: str
    area_name: str | None = None
    place_name: str | None = None
    category: str | None = None                    # "체육시설 > 테니스장"
    payment_type: str | None = None                # "무료" / "유료"
    target_info: str | None = None                 # "제한없음" / "초등학생"
    status: str | None = None                      # "접수중" / "마감"
    receipt_period: dict[str, str] | None = None   # {"start": "...", "end": "..."}
    operating_period: dict[str, str] | None = None
    summary: str | None = None                     # Track B 요약 (향후)
    coord: dict[str, float] | None = None          # {"x": ..., "y": ...}
    service_url: str
    relevance_score: float | None = None           # RRF score (디버깅/정렬용)


class MapBlock(BaseModel):
    """MAP intent — 지도 결과."""
    kind: Literal["map"] = "map"
    geojson: dict
    center: dict[str, float] | None = None
    radius_m: int | None = None


class FaqBlock(BaseModel):
    """향후 FAQ intent용 placeholder."""
    kind: Literal["faq"] = "faq"
    question: str
    answer: str
    source_url: str | None = None


# Pydantic Discriminated Union
ContentBlock = Annotated[
    ServiceCard | MapBlock | FaqBlock,
    Field(discriminator="kind"),
]


class ChatStreamFinalEvent(BaseModel):
    """SSE event=final payload v2."""
    schema_version: int = 2
    message_id: int

    # 챗봇 발화 — narrative only. 시설 나열·URL 금지 (Phase C 이후)
    answer: str

    # 구조화 데이터 — 프론트가 카드/지도/리스트로 렌더
    blocks: list[ContentBlock] = Field(default_factory=list)

    # 추천 후속 질문 (Phase 2 — 초기엔 빈 리스트)
    suggested_followups: list[str] = Field(default_factory=list)

    # 메타데이터
    intent: str | None = None
    sub_intent: str | None = None
    title: str | None = None
    cache_hit: bool = False

    # 확장 슬롯 — A/B 플래그, 디버깅 등
    metadata: dict | None = None
```

**Files:**
- Modify: `schemas/chat.py`

- [ ] **Step 1: 스키마 정의 + 단위 테스트**

```python
# tests/test_chat_schema_v2.py
class TestChatStreamFinalEventSchema:
    def test_service_card_discriminator(self):
        """kind='service' 면 ServiceCard 로 deserialize."""
        ...

    def test_map_block_discriminator(self):
        """kind='map' 이면 MapBlock 으로 deserialize."""
        ...

    def test_blocks_default_empty_list(self):
        """blocks 미전달 시 빈 리스트."""
        ...

    def test_invalid_kind_raises_validation(self):
        """알 수 없는 kind는 ValidationError."""
        ...

    def test_service_card_minimal_required_fields(self):
        """service_id / service_name / service_url 만으로도 유효."""
        ...
```

- [ ] **Step 2: 기존 `ChatResponse`는 deprecated 표시 (제거하지 않음 — Phase C까지 보존)**

```python
class ChatResponse(BaseModel):
    """[DEPRECATED v1] schema_version<2 클라이언트용. Phase C 완료 후 제거."""
    ...
```

---

## Task 2: `AnswerAgent._build_blocks()` — 코드 기반 카드 매핑

### 동작

- LLM 호출 없이 hydrated rows 만 사용
- `sql_results` / `vector_results` → `ServiceCard` 리스트
- `map_results` (GeoJSON) → `MapBlock`
- 입력 행 순서 유지 (검색 랭크 = 카드 노출 순서)
- `payment_type` 등 None인 필드는 모델에서 None으로 유지 (Pydantic exclude_none 옵션은 직렬화 시점)
- 카드 수 상한: `settings.chat_max_blocks` (기본 10) — 운영 비용/UX 고려

### 시그니처

```python
@staticmethod
def _build_blocks(state: AgentState) -> list[ContentBlock]:
    """검색 결과를 ContentBlock 리스트로 변환한다 (LLM 호출 없음)."""
```

### 책임 분리

| 책임 | 담당 |
|------|------|
| `answer` (narrative) 생성 | LLM (`_answer_chain`) |
| `blocks` (구조화 데이터) 매핑 | 코드 (`_build_blocks`) |
| `suggested_followups` 생성 | Phase 2 (선택, LLM) |

**Files:**
- Modify: `agents/answer_agent.py`
- Modify: `tests/test_answer_agent.py`

- [ ] **Step 1: `_build_blocks()` 구현 + 단위 테스트**

```python
class TestBuildBlocks:
    def test_sql_results_become_service_cards(self):
        """sql_results 의 각 row가 ServiceCard 로 매핑된다."""
        ...

    def test_vector_results_become_service_cards(self):
        """vector_results 도 동일 매핑."""
        ...

    def test_input_order_preserved(self):
        """검색 랭크 순서가 blocks 순서로 보존된다."""
        ...

    def test_map_results_become_map_block(self):
        """map_results GeoJSON 이 MapBlock 단일 항목으로 매핑."""
        ...

    def test_blocks_capped_at_max(self):
        """settings.chat_max_blocks 이상은 잘려나간다."""
        ...

    def test_missing_service_url_uses_fallback(self):
        """service_url None 일 때 _FALLBACK_URL 사용."""
        ...

    def test_category_assembled_from_max_min_class(self):
        """category 필드는 '{max} > {min}' 형태로 조립."""
        ...

    def test_receipt_period_dict_format(self):
        """receipt_start_dt / receipt_end_dt 가 dict으로 합쳐진다."""
        ...
```

- [ ] **Step 2: 프롬프트 narrative-only 전환 — Phase C에서만 활성화**

```python
_ANSWER_SYSTEM_V2 = """\
당신은 서울시 공공서비스 예약 안내 챗봇입니다.
사용자 질문에 1~3문장의 자연스러운 한국어로 답하세요.

규칙:
- 검색 결과가 없으면 정중히 안내하고, 조건 변경을 권유하세요.
- 결과가 있으면 전체 요약·맥락·특이점만 짧게 언급합니다.
  예: "강남구 체육시설 23건을 찾았어요. 대부분 무료시설입니다."
- **시설명·URL·날짜·요금 등 개별 시설 정보는 절대 직접 적지 마세요.**
  (그 정보는 별도 카드 컴포넌트가 렌더링합니다.)
- 마크다운 금지. 일반 문장만 사용하세요.
"""
```

기존 `_ANSWER_SYSTEM`은 Phase A·B 동안 그대로 유지.

---

## Task 3: `routers/chat.py` — SSE final payload v2 + 헤더 라우팅

### Phase A: `blocks` 추가, `answer` 기존 그대로

```python
elif event_type == "result":
    result = data
    intent = result.get("intent")

    # blocks 매핑 — 코드가 hydrated rows에서 직접 만든다
    blocks = AnswerAgent._build_blocks(result)

    payload = {
        "schema_version": 2,
        "message_id": result["message_id"],
        "answer": result.get("answer") or "",
        "blocks": [b.model_dump(exclude_none=True) for b in blocks],
        "suggested_followups": [],   # Phase 2
        "intent": intent.value if intent is not None else None,
        "sub_intent": result.get("vector_sub_intent"),
        "title": result.get("title"),
        "cache_hit": bool(result.get("cache_hit")),
    }
    ...
```

### Phase B: `X-Chat-Schema-Version` 헤더 기반 라우팅

```python
client_version = int(request.headers.get("x-chat-schema-version", "1"))

if client_version >= 2:
    # v2 narrative-only answer + blocks
    payload_answer = result.get("answer") or ""   # AnswerAgent가 narrative만 출력
else:
    # v1 — 기존 answer (시설 나열 포함). 카드 없이 텍스트만으로 표시되는 구식 클라이언트용.
    payload_answer = result.get("answer_v1") or result.get("answer") or ""
```

이를 위해 `AgentState` 에 `answer` / `answer_v1` 두 슬롯이 필요할 수도 있으나, 더 단순하게는 **Phase B 동안 AnswerAgent를 두 번 호출하지 않고** v2 narrative만 생성하되 클라이언트 헤더가 v1이면 서버가 narrative + blocks 마크다운 합성으로 v1 답변을 재구성하는 방식이 권장된다.

### Phase C: v1 응답 제거

- v1 클라이언트 전부 마이그레이션 완료 확인 후
- `_ANSWER_SYSTEM` → `_ANSWER_SYSTEM_V2` 영구 전환
- `ChatResponse` (v1) 삭제

**Files:**
- Modify: `routers/chat.py`
- Modify: `tests/test_chat_router.py`

- [ ] **Step 1 (Phase A): final payload에 `blocks` 추가 + 기존 `answer` 보존**

테스트 갱신:
```python
async def test_sse_final_includes_blocks(self):
    """final event payload 에 blocks 리스트가 포함된다."""
    ...

async def test_sse_final_schema_version_is_2(self):
    """payload['schema_version'] == 2."""
    ...
```

- [ ] **Step 2 (Phase B): `X-Chat-Schema-Version` 헤더 라우팅**

```python
async def test_v1_client_receives_legacy_answer(self):
    """헤더가 1이면 시설 정보 포함 markdown answer."""
    ...

async def test_v2_client_receives_narrative_answer_and_blocks(self):
    """헤더가 2면 narrative answer + blocks 분리."""
    ...

async def test_missing_header_defaults_to_v1(self):
    """헤더 미전송 시 v1 호환."""
    ...
```

- [ ] **Step 3 (Phase C): v1 제거 + narrative 프롬프트 영구 전환**

별도 PR. v1 클라이언트가 production에 없음을 확인한 후 진행.

---

## Task 4: `AgentState` 확장

### 변경

```python
class AgentState(TypedDict):
    ...
    blocks: list[dict] | None    # 신규 — _build_blocks() 결과의 직렬화 dict (state envelope에는 dict로 저장)
```

캐시 envelope에도 `blocks`를 포함시켜 cache hit 시 동일하게 복원되도록 한다 (Task 5).

**Files:**
- Modify: `schemas/state.py`
- Modify: `tests/conftest.py` (state fixture)

- [ ] **Step 1: `blocks` 키 추가 + state 초기화 회귀**

---

## Task 5: Answer Cache envelope 확장

### 변경 동작

- 현재 `core/cache.py` envelope은 `{answer, title, sql_results, vector_results, refined_query, ...}` 보관
- v2 에선 `blocks` 도 함께 보관해야 cache hit 시 SSE final payload를 정확히 재구성 가능
- `_build_blocks()` 은 cache miss 시점에만 실행 — cache hit 시는 envelope에서 그대로 복원

```python
envelope = {
    "answer": state["answer"],
    "title": state.get("title"),
    "blocks": state.get("blocks"),    # 신규
    ...
}
```

**Files:**
- Modify: `core/cache.py`
- Modify: `tests/test_answer_cache.py`

- [ ] **Step 1: envelope 직렬화·복원에 `blocks` 추가**

```python
async def test_cache_envelope_preserves_blocks(self):
    """cache hit 시 blocks가 동일하게 복원된다."""
    ...
```

- [ ] **Step 2: cache key는 v2 도입으로 변경되지 않음** (현 `sha256(refined_query|max|area|status)` 그대로)

블록 데이터는 envelope에 담길 뿐, 캐시 키 합성 입력에는 포함하지 않는다 (검색 입력 조건이 같으면 동일한 응답).

---

## Task 6: Spring Boot 측 협의 사항

### 결정 필요 항목 (`on-seoul-api` 팀과 협의)

1. **`chat_messages.content` 컬럼**:
   - 옵션 A — 기존 `content TEXT` 유지, `answer` 만 저장. `blocks`는 별도 응답 시점 캐시 사용.
   - 옵션 B — `content JSONB` 로 마이그레이션, payload 전체(`answer` + `blocks`) 저장. 대화 재현 시 카드까지 복원 가능.
   - **권장: 옵션 B** — 대화 이력에서 카드 렌더가 가능해야 UX 일관성 확보.

2. **SSE 릴레이**:
   - 기존 SseEmitter + WebClient 코드는 payload 형태에 의존하지 않으면 그대로 동작
   - 단, 헤더(`X-Chat-Schema-Version`) 전달 필요

3. **알림 메시지 생성(`POST /notification/template`)**:
   - 알림은 짧은 문장이라 `answer` 와 다른 별도 API. 본 계획 영향 없음.

**Files:**
- 협의 결과를 `docs/api-contract.md` (신규 또는 기존 문서)에 명시

- [ ] **Step 1: Spring Boot 팀 협의 + 의사결정 기록**
- [ ] **Step 2: `chat_messages.content` 컬럼 결정 시 마이그레이션 계획 별도 작성**

---

## Task 7: 문서 일괄 갱신

### 영향 받는 문서

- `/Users/vito/study/on-seoul-agent/docs/agent-design.md`
- `/Users/vito/study/on-seoul-agent/docs/architecture.md`
- `/Users/vito/study/on-seoul-agent/on-seoul-agent/README.md`

### Step 1: `docs/agent-design.md`

- [ ] **1-1.** Answer Agent 섹션에 "LLM=narrative, 코드=blocks" 책임 분리 명시
- [ ] **1-2.** AgentState 표에 `blocks` 행 추가
- [ ] **1-3.** Discriminated union(`ServiceCard | MapBlock | FaqBlock`) 도식 추가

### Step 2: `docs/architecture.md`

- [ ] **2-1.** `/chat/stream` SSE final payload v2 예시 추가
- [ ] **2-2.** 하이브리드(narrative + structured) 설계 근거 1문단

### Step 3: `on-seoul-agent/README.md`

- [ ] **3-1.** 응답 예시 v2로 갱신
- [ ] **3-2.** schema_version 협상 시퀀스 다이어그램 추가 (선택)

---

## Task 8: 단계적 활성화

### Phase A (본 계획 진행 시 즉시 적용)

- `schema_version=2` payload에 `blocks` 추가
- `answer`는 기존 v1 동일 — 시설 나열·URL 포함된 마크다운
- 신규 클라이언트는 `blocks` 만 렌더, 기존 클라이언트는 `answer` 만 렌더 — 양쪽 모두 동작

### Phase B (별도 PR — 프론트엔드 측 준비 완료 후)

- `X-Chat-Schema-Version` 헤더로 라우팅
- v2 클라이언트는 narrative-only `answer` + `blocks` 받음
- v1 클라이언트는 기존 그대로

### Phase C (별도 PR — v1 클라이언트 완전 사라진 후)

- `_ANSWER_SYSTEM` → `_ANSWER_SYSTEM_V2` 영구 전환
- `ChatResponse v1` deprecated → 제거
- 마이그레이션 완료

- [ ] **Step 1: Phase A 배포 후 메트릭 관찰** — `blocks` 직렬화 비용·payload 크기 증가량
- [ ] **Step 2: 프론트엔드 카드 렌더 구현 완료 확인** → Phase B 트리거
- [ ] **Step 3: v1 클라이언트 통계 확인** → Phase C 트리거

---

## 완료 기준 체크리스트

### Phase A (이번 PR 범위)

- [ ] `schemas/chat.py` 에 `ServiceCard` / `MapBlock` / `FaqBlock` / `ChatStreamFinalEvent v2` 정의
- [ ] `AnswerAgent._build_blocks()` 가 sql_results / vector_results / map_results 를 ContentBlock 리스트로 매핑
- [ ] SSE final payload 에 `schema_version=2` + `blocks` 포함
- [ ] 기존 `answer` 동작 변경 없음 (하위 호환)
- [ ] cache envelope 에 `blocks` 포함 + cache hit 시 동일 복원
- [ ] AgentState 에 `blocks` 슬롯 추가, 기존 테스트 회귀 통과
- [ ] 신규 단위 테스트 (`test_chat_schema_v2.py`, `_build_blocks` 케이스) 추가
- [ ] 라이브 호출 검증 — `curl /chat/stream` 결과에 `blocks` 배열 및 시설별 정확한 `service_url` 확인

### Phase B (별도 PR)

- [ ] `X-Chat-Schema-Version` 헤더 라우팅 구현
- [ ] v1/v2 클라이언트 분기 테스트 추가

### Phase C (별도 PR)

- [ ] `_ANSWER_SYSTEM_V2` 영구 전환
- [ ] `ChatResponse v1` 삭제
- [ ] v1 회귀 테스트 제거 (또는 historical 보존 디렉토리로 이동)

---

## 사전 확정 사항

1. **LLM은 narrative만, 카드는 코드가 매핑** — LLM 환각 위험 차단 + LLM 비용·지연 절감 두 가지 모두 노린다.
2. **Discriminated union으로 확장**: 새 컨텐츠 타입(map / faq / image / weather …) 추가 시 union에 한 줄 추가만으로 됨. 기존 코드 변경 X.
3. **하위 호환 단계적 전환**: Phase A → B → C. 어느 단계도 기존 클라이언트를 깨뜨리지 않는다.
4. **cache envelope에 `blocks` 포함**: cache hit 시에도 동일한 카드 데이터 복원. 캐시 키 합성 입력은 변경하지 않음.
5. **`schema_version` 필드 단조 증가**: 클라이언트는 자신이 이해하는 최고 버전을 헤더로 표명, 서버는 그에 맞춰 직렬화.
6. **`metadata` 자유 dict 슬롯**: A/B 실험·디버깅 데이터를 스키마 변경 없이 실어 보낼 수 있다.
7. **`suggested_followups` 는 Phase 2 이후**: 본 계획에서는 빈 리스트로 placeholder만 유지. 별도 LLM 호출 비용·UX 검증 후 채운다.
8. **Spring Boot `chat_messages.content` 컬럼 마이그레이션은 별도 결정**: JSONB로 가는 게 권장 방향이나, 본 계획 외부 의사결정이다.

---

## 부록 — 운영 중 발견된 미완 사항 (2026-05-28 라이브 검증)

`/chat/stream` 실제 호출 검증에서 드러난 답변 품질 이슈. v2 스키마 본격 도입 전이라도 가능한 부분은 v1 프롬프트 패치로 우선 처리하고, 구조적 개선이 필요한 부분은 v2 Phase A 작업 항목에 통합한다.

### A. v1 프롬프트로 즉시 해결 (적용 완료)

- [x] **이용 기간(service_open_*_dt) 출력 제외** — DB에 `2021-01-01 ~ 2031-12-30` 같은 비현실적 값이 다수. 사용자 혼란을 유발하므로 LLM 컨텍스트에서도 제거.
- [x] **친절한 도입문/마무리** — "조회된 데이터 [...]"식 무미건조한 출력 대신:
  - 도입: "관련 시설을 찾아봤어요" 류 1~2 문장
  - 마무리: 접수중 시설이 있으면 절차 안내(서울시 통합회원 가입·휴대폰 본인인증), 자치구/요금 조건 미명시 시 후속 질문 유도
- [x] **`}` 누수 버그** — 프롬프트 내 `{{service_name}}` placeholder 표기를 LLM이 그대로 출력. placeholder 문법 완전 제거 후 구체 예시 형태로 전환.

### B. v2 Phase A 에서 함께 처리할 항목

- [ ] **`vector_sub_intent` 분기 응답**
  - `detail` 의도(절차/요금/규정 문의)일 때 카드 나열보다 안내문 위주의 narrative 강화
  - `identification` 의도(고유명사 식별)일 때 가장 가까운 후보 1~3개 위주 출력
  - `semantic` 의도일 때 다양성 우선 (지역/카테고리 분포 노출)
  - 구현 위치: `AnswerAgent.answer()` 에서 `state["vector_sub_intent"]` 분기 → 프롬프트 또는 narrative 템플릿 선택

- [ ] **자격 제한 강조** — `target_info` 가 "어르신", "65세이상", "회원전용" 등이면 카드에 ⚠️ 또는 별도 라벨 노출. 일반 사용자가 신청 불가능한 시설을 상위로 안내할 때 혼란 최소화.

- [ ] **데이터 신뢰성 필터** — `service_open_*_dt` 비정상 값(10년 이상 운영 기간) 검출 시 메타데이터 별도 표기 또는 카드 노출 우선순위 하향.

- [ ] **후속 질문 유도(suggested_followups)** — narrative 마무리에서 자치구·요금·시간대 미명시 시 구체적 follow-up 질문 1~2개 제시.
  - 예: "어느 자치구를 선호하시나요?", "무료 시설만 보시겠어요, 유료 포함 안내드릴까요?"
  - v2 의 `suggested_followups: list[str]` 필드 도입 시 LLM이 narrative 와 별도로 생성하도록.

- [ ] **DB 자체 검증** — `public_service_reservations.service_open_*_dt` 컬럼의 비정상 값 비율 측정.
  - Spring Boot 측 데이터 수집 단계에서 sanity check 추가 검토 (별도 작업)
  - 임시로 AI 서비스 LLM 컨텍스트에서 제외 (적용 완료)

### C. 라우터 보강 (별도 PR 가능)

- [ ] **"어떻게/방법" 등 절차 질의의 sub_intent=detail 안정화** — 라우터 few-shot 1건 추가했으나 부정형/혼합 표현(예: "예약 가능한 테니스장 어떻게 찾아?") 케이스 회귀 검증 필요. eval_set_holdout 에 case 보강.
