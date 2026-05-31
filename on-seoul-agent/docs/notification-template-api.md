# 알림 템플릿 생성 API 명세 — `POST /notification/template`

> **상태**: 미구현. 본 문서가 구현 기준(계약)이다. 다른 세션은 이 문서만 보고 엔드포인트를 구현할 수 있어야 한다.
> **소비자**: API 서비스(Spring, `on-seoul-api`)의 `TemplateAgentClient`.
> **계약 출처**: Spring 클라이언트 `TemplateAgentClient` + ADR-0004(구독 1건당 배치 호출). 요청/응답 JSON 필드명·유효성 규칙은 클라이언트에서 추출한 것이므로 임의로 바꾸지 않는다(교차 경계 계약).

---

## 1. 개요

API 서비스는 서울 공공서비스 예약 데이터를 일 1회 수집한 뒤, 구독 조건에 맞는 변경(신규/변경/삭제)을 감지한다. 변경이 감지된 **구독 1건마다** 이 AI 엔드포인트를 1회 호출하여, 해당 구독자에게 보낼 **개인화된 알림 제목(`title`)과 본문(`body`)** 을 생성받는다.

- **호출 주체**: API 서비스의 알림 발송 파이프라인(`TemplateAgentClient`).
- **배치 모델(ADR-0004)**: 한 구독에서 발견된 모든 변경 이벤트를 `changes` 배열로 묶어 **1회 호출**한다. 변경 N건 → AI 호출 1회. 빈 배열은 호출자가 사전 차단하므로 정상 호출은 보통 1건 이상이다.
- **역할 분리**: 알림 *발송*(SMS/이메일)은 API 서비스가 담당한다. 이 엔드포인트는 메시지 *생성*만 책임진다.
- **degrade 정책**: AI 응답이 유효하지 않거나(빈 title/body), 호출이 실패하거나(타임아웃·non-2xx), 파싱이 안 되면 **클라이언트가 자체 fallback 템플릿으로 대체**한다(§6). 따라서 이 엔드포인트는 알림 누락의 단일 실패점이 아니다 — 하지만 fallback보다 자연스럽고 개인화된 메시지를 생성하는 것이 목표다.

---

## 2. 요청 / 응답 스키마

### 2.1 요청

```
POST /notification/template
Content-Type: application/json
```

JSON 필드명은 **snake_case**다(Spring DTO가 `@JsonProperty`로 snake_case 직렬화).

```json
{
  "service_id": "S240101A001",
  "changes": [
    {
      "change_type": "UPDATED",
      "field_name": "service_status",
      "old_value": "접수중",
      "new_value": "마감"
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `service_id` | string | O | 서울 공공서비스 예약 식별자 |
| `changes` | array | O | 해당 구독에서 발견된 모든 변경 이벤트(보통 1건 이상) |
| `changes[].change_type` | string | O | `NEW` \| `UPDATED` \| `DELETED` (collection의 `ChangeType` enum과 동일) |
| `changes[].field_name` | string \| null | - | 변경된 필드명(예: `service_status`, `place_name`). `NEW`/`DELETED` 시 null 가능 |
| `changes[].old_value` | string \| null | - | 변경 전 값 |
| `changes[].new_value` | string \| null | - | 변경 후 값 |

### 2.2 응답 — `200 OK`

```json
{ "title": "...", "body": "..." }
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `title` | string | 알림 제목. **non-null & 공백 아님** |
| `body` | string | 알림 본문. **non-null & 공백 아님** |

> **유효성 규칙(중요)**: 클라이언트는 `title`과 `body`가 **둘 다 non-null이고 `!isBlank()`** 일 때만 AI 응답을 채택한다. 둘 중 하나라도 null·빈문자·공백이면 클라이언트가 fallback으로 전환한다.
> → **AI는 어떤 경우에도 의미 있는 `title`·`body`를 반드시 채워야 한다.** 빈 문자열이나 공백만 채운 응답은 사실상 fallback과 동일하게 취급된다.

### 2.3 Pydantic 모델 제안 (`schemas/notification.py` 신규)

기존 `schemas/embeddings.py` 컨벤션(BaseModel, snake_case 그대로, `model_validator`로 의미 검증)을 따른다. snake_case 필드명이 그대로 JSON 키이므로 alias는 필요 없다.

```python
"""알림 템플릿 생성 API 스키마."""

from typing import Literal

from pydantic import BaseModel, model_validator

ChangeType = Literal["NEW", "UPDATED", "DELETED"]
_MAX_CHANGES = 50


class ServiceChange(BaseModel):
    change_type: ChangeType
    field_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None


class NotificationTemplateRequest(BaseModel):
    service_id: str
    changes: list[ServiceChange]

    @model_validator(mode="after")
    def validate_request(self) -> "NotificationTemplateRequest":
        if not self.service_id.strip():
            raise ValueError("service_id는 비어 있을 수 없습니다.")
        if not self.changes:
            raise ValueError("changes는 최소 1건 이상이어야 합니다.")
        if len(self.changes) > _MAX_CHANGES:
            raise ValueError(
                f"changes는 {_MAX_CHANGES}건을 초과할 수 없습니다. (현재: {len(self.changes)})"
            )
        return self


class NotificationTemplateResponse(BaseModel):
    title: str
    body: str
```

> `change_type`을 `Literal`로 두면 enum 외 값은 Pydantic이 422로 거른다(§5 참고). `changes` 빈 배열은 호출자가 사전 차단하지만, 방어적으로 422 처리한다.

---

## 3. `change_type` / `field_name` 의미와 예시

| `change_type` | 의미 | `field_name` | `old_value` / `new_value` |
|---|---|---|---|
| `NEW` | 구독 조건에 맞는 새 서비스 등장 | 보통 null | 보통 null |
| `UPDATED` | 기존 서비스의 특정 필드 변경 | 변경 필드명 | 변경 전/후 값 |
| `DELETED` | 서비스가 더 이상 노출되지 않음(삭제/종료) | 보통 null | 보통 null |

대표 `field_name`(on_data `public_service_reservations` 컬럼 기준):

| `field_name` | 한국어 표현(메시지 작성 시 권장) |
|---|---|
| `service_status` | 접수 상태 (예: 접수중 → 마감) |
| `place_name` | 장소 |
| `area_name` | 지역(자치구) |
| `receipt_start_dt` / `receipt_end_dt` | 접수 시작/마감 일시 |
| `payment_type` | 결제 유형(무료/유료) |
| `service_url` | 신청 링크 |

### 예시 데이터

상태 변경(UPDATED 1건):
```json
{ "service_id": "S240101A001",
  "changes": [{ "change_type": "UPDATED", "field_name": "service_status",
                "old_value": "접수중", "new_value": "마감" }] }
```

여러 변경(UPDATED 2건):
```json
{ "service_id": "S240101A001",
  "changes": [
    { "change_type": "UPDATED", "field_name": "receipt_end_dt",
      "old_value": "2026-06-10", "new_value": "2026-06-20" },
    { "change_type": "UPDATED", "field_name": "service_status",
      "old_value": "마감", "new_value": "접수중" }
  ] }
```

신규(NEW):
```json
{ "service_id": "S240601B007",
  "changes": [{ "change_type": "NEW", "field_name": null,
                "old_value": null, "new_value": null }] }
```

---

## 4. 권장 구현 방향

### 4.1 LLM 연동

기존 패턴(`llm/generator.py`의 `Generator`, `llm/client.py`의 `get_chat_model`, 프롬프트는 `llm/prompts/` 모듈 상수)을 그대로 재사용한다.

- **프롬프트 배치**: `llm/prompts/notification.py` 신규. 시스템 프롬프트(`NOTIFICATION_SYSTEM`)와 Few-shot 예시를 `router.py`처럼 모듈 상수로 둔다. 프롬프트를 라우터/스키마에 인라인하지 않는다.
- **호출**: `Generator.generate(prompt, system=NOTIFICATION_SYSTEM)` 또는 구조화 출력이 필요하면 `get_chat_model().with_structured_output(NotificationTemplateResponse)` LCEL 체인. 구조화 출력을 쓰면 title/body 누락을 모델 단에서 강제하기 쉽다.
- **온도**: `temperature=0.0`~`0.3` 권장(알림은 톤 일관성이 중요, 과한 창의성 불필요).
- **provider/timeout**: `get_chat_model`이 이미 `timeout=30`(Gemini) / `request_timeout=30`(OpenAI), `max_retries=3`을 설정한다. 단 **소비자 타임아웃은 10초**(§6)이므로, 이 엔드포인트의 실효 응답은 10초 내여야 한다. LLM 호출에 `asyncio.wait_for(..., timeout=<10초 미만, 예: 8초>)`를 걸어 클라이언트 타임아웃 전에 자체 degrade(§5)로 빠지도록 한다.

### 4.2 프롬프트 가이드(메시지 품질)

- **목표**: §6의 fallback 템플릿보다 자연스럽고 개인화된 한국어 메시지.
- **title**: 짧게(권장 ~30자 이내). 핵심 변경을 한눈에. 예) `접수가 다시 시작됐어요`, `마감 임박 안내`.
- **body**: 1~2문장. 무엇이 어떻게 바뀌었는지 + 행동 유도(예: "지금 신청해 보세요"). `service_id`를 본문에 그대로 노출하지 말 것(사용자 친화적이지 않음).
- **여러 변경**: `changes`가 여러 건이면 핵심 변경 위주로 요약하되 누락된 건수를 자연스럽게 언급("이 외에도 일부 정보가 변경됐어요").
- **change_type별 톤**: `NEW`는 등장 안내, `UPDATED`는 변경 안내, `DELETED`는 종료/마감 안내.
- **금지**: 빈 title/body, 마크다운, 이모지(프로젝트 정책), 추측성 사실(없는 일정·가격 지어내기 금지).

### 4.3 빈 응답 방지(핵심)

§2.2 유효성 규칙상 빈 title/body는 fallback과 동일하다. 구현은 **LLM 결과를 그대로 반환하기 전에 자체 검증**한다.

- 구조화 출력이라도 모델이 공백을 채울 수 있으므로, 반환 직전 `title.strip()` / `body.strip()`이 비어 있지 않은지 확인한다.
- 비어 있으면 §5의 자체 최소 메시지(또는 non-2xx)로 전환한다 — **공백 응답을 200으로 흘려보내지 않는다.**

---

## 5. 에러 처리

| 상황 | 처리 | 비고 |
|---|---|---|
| 입력 검증 실패(필수 필드 누락, `change_type` enum 위반, `changes` 빈 배열 등) | **422** (Pydantic + `main.py`의 `validation_exception_handler`) | 별도 코드 불필요. 모델 정의만으로 동작 |
| LLM 호출 실패(타임아웃/네트워크/파싱) | **자체 degrade** 권장 | 아래 참고 |
| LLM 결과가 빈 title/body | 자체 degrade 또는 non-2xx | §4.3 |

**LLM 실패 시 AI 서비스 자체 동작 권고:**

- 클라이언트는 어떤 실패든(타임아웃·non-2xx·빈 응답) fallback으로 안전하게 처리하므로, **non-2xx(예: 503) 반환도 알림 누락을 유발하지 않는다.** 따라서 둘 중 어느 쪽도 안전하다:
  1. **(권장) AI 서비스가 자체 최소 템플릿을 200으로 반환** — fallback과 동일 형식(§6)을 AI 서비스에서 만들어 반환하면, 네트워크 왕복/클라이언트 분기 없이 일관된 결과를 보장. 단 fallback보다 나은 메시지가 아니라면 의미가 없으므로, LLM 1차 실패 시에만 사용.
  2. **non-2xx 반환(예: 503 Service Unavailable)** — LLM 장애를 명확히 신호. 클라이언트가 fallback으로 전환. 구현이 단순.
- **금지**: LLM 실패 시 빈 title/body를 200으로 반환(클라이언트가 fallback하므로 결과는 같으나, 200+빈본문은 관측·디버깅을 흐림). 명시적으로 degrade하라.
- **타임아웃**: §4.1대로 LLM 호출을 10초보다 짧게(예: 8초) self-timeout 걸어, 클라이언트 타임아웃 전에 위 1/2 경로로 빠진다.

> 본 엔드포인트는 챗봇 스트리밍과 무관하므로 SSE·trace 적재 대상이 아니다. 관측이 필요하면 표준 로깅(`logger.warning`/`logger.exception`)으로 충분하다. trace 테이블(`chat_agent_traces`)은 채팅 워크플로우 전용이므로 여기서 쓰지 않는다.

---

## 6. 소비자(API 서비스) 계약 요약

`TemplateAgentClient`가 강제하는 계약. 구현은 이 표를 만족해야 한다.

| 항목 | 값 / 동작 |
|---|---|
| 엔드포인트 | `POST /notification/template`, `Content-Type: application/json` |
| 타임아웃 | `ai.service.template-timeout-seconds` = **10초**(ADR-0004). 초과 시 클라이언트가 fallback 사용 → AI는 10초 내 응답 필수 |
| non-2xx 응답 | 클라이언트가 fallback 사용(알림 누락 아님, fallback 메시지로 발송) |
| 응답 유효성 | `title`·`body`가 **둘 다 non-null & `!isBlank()`** 일 때만 채택. 미달 시 fallback |
| 호출 단위 | 구독 1건당 1회. 모든 변경을 `changes` 배열로 묶어 전달(배치) |

### 클라이언트 fallback 템플릿 (AI 응답 품질의 baseline)

AI 실패 시 클라이언트가 생성하는 형태. **AI는 이보다 자연스러운 메시지를 만드는 것이 목표.**

- **title**: `[서울공공서비스] {service_id} 변경 알림`
- **body(변경 1건)**: `{field_name} 이(가) {old_value} → {new_value} 으로 변경되었습니다.`
- **body(여러 건)**: 위 문장 + ` (외 {n-1}건)`

---

## 7. 라우터 등록 위치

- 라우터 파일: `routers/notification.py` 신규.
  - `embeddings.py`처럼 `router = APIRouter(prefix="/notification", tags=["notification"])` 선언.
  - 핸들러: `@router.post("/template")` → `async def create_template(req: NotificationTemplateRequest) -> NotificationTemplateResponse`.
  - **async 필수**(LLM 호출은 `await`). 요청 경로에 블로킹 I/O 금지.
- `main.py` 등록: 기존 `app.include_router(...)` 블록에 추가.

```python
# main.py
from routers import notification as notification_router
...
app.include_router(notification_router.router)
```

> `embeddings_router`가 prefix를 라우터 내부에서 선언하므로, `notification.router`도 동일하게 내부 prefix(`/notification`)를 갖고 `include_router`에는 추가 prefix를 주지 않는다. 최종 경로는 `/notification/template`이다.

---

## 8. 구현 체크리스트(다음 세션용)

- [ ] `schemas/notification.py` — §2.3 모델(`ServiceChange`, `NotificationTemplateRequest`, `NotificationTemplateResponse`)
- [ ] `llm/prompts/notification.py` — `NOTIFICATION_SYSTEM` + Few-shot
- [ ] `routers/notification.py` — `POST /notification/template`, async, self-timeout(<10초), 빈 응답 방지, LLM 실패 시 degrade(§5)
- [ ] `main.py` — `include_router` 추가
- [ ] 테스트(`tests/test_notify_template.py`) — Fake LLM. 케이스: 정상 1건/여러 건, `change_type` 위반 422, `changes` 빈 배열 422, LLM 예외 시 degrade 경로(non-2xx 또는 최소 템플릿 200), 빈 title/body가 절대 200으로 안 나가는지. **실제 LLM/외부 호출 금지.**
- [ ] `uv run pytest` / `uv run ruff check .` 그린
