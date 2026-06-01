# 알림 템플릿 생성 API 명세 — `POST /notification/template`

> **상태**: 미구현. 본 문서가 구현 기준(계약)이다. 다른 세션은 이 문서만 보고 엔드포인트를 구현할 수 있어야 한다.
> **소비자**: API 서비스(Spring, `on-seoul-api`)의 `TemplateAgentClient`.
> **계약 출처**: Spring 클라이언트 `TemplateAgentClient` + ADR-0004(구독 1건당 배치 호출). 요청/응답 JSON 필드명·유효성 규칙은 클라이언트에서 추출한 것이므로 임의로 바꾸지 않는다(교차 경계 계약).

---

## 1. 개요

API 서비스는 서울 공공서비스 예약 데이터를 일 1회 수집한 뒤, 구독 조건에 맞는 변경(신규/변경/삭제)을 감지한다. 변경이 감지된 **구독 1건마다** 이 AI 엔드포인트를 1회 호출하여, 해당 구독자에게 보낼 **개인화된 알림 제목(`title`)과 본문(`body`)** 을 생성받는다.

API 서비스의 알림 구독은 **조건 기반**(키워드/상태/지역 필터)이라 **하나의 구독이 여러 `service_id`에 동시 매칭**된다. 따라서 한 번의 호출에 매칭된 **여러 서비스의 변경이 함께 담겨** 오며, AI는 이들을 묶어 **자연스러운 하나의 `body`** 를 생성한다.

- **호출 주체**: API 서비스의 알림 발송 파이프라인(`TemplateAgentClient`).
- **배치 모델(ADR-0004)**: 한 구독에서 발견된 모든 변경을 **서비스 그룹 리스트**(`services[]`)로 묶어 **1회 호출**한다. 서비스 M개·변경 N건 → AI 호출 1회. 각 서비스 그룹은 자신의 메타 정보와 `changes` 배열을 갖는다. 빈 리스트는 호출자가 사전 차단하므로 정상 호출은 보통 서비스 1개 이상이다.
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

> **주의**: JSON 키는 snake_case이지만, `changes[].field_name`의 **값**은 collection(UpsertService)이 기록하는 **camelCase 엔티티 필드명**이다(예: `serviceStatus`, `receiptEndDt`). 키 자체(`field_name`)는 snake_case, 그 값 문자열만 camelCase임에 유의.

요청 본문은 **서비스 그룹 리스트**(`services[]`)다. 각 그룹은 서비스 메타 정보(모두 nullable)와 해당 서비스의 `changes` 배열을 갖는다.

```json
{
  "services": [
    {
      "service_id": "S250101",
      "service_name": "OO수영장 자유수영",
      "service_url": "https://yeyak.seoul.go.kr/...",
      "image_url": "https://...",
      "place_name": "OO체육센터",
      "area_name": "강남구",
      "service_status": "접수중",
      "target_info": "누구나",
      "receipt_start_dt": "2026-06-01T09:00:00",
      "receipt_end_dt": "2026-06-10T18:00:00",
      "changes": [
        {
          "change_type": "UPDATED",
          "field_name": "receiptEndDt",
          "old_value": "2026-06-10T18:00:00",
          "new_value": "2026-06-20T18:00:00"
        }
      ]
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `services` | array | O | 구독에 매칭된 서비스 그룹 리스트. **최소 1개**(빈 리스트는 422) |
| `services[].service_id` | string | O | 서울 공공서비스 예약 식별자 |
| `services[].service_name` | string \| null | - | 서비스명. 메시지 작성 시 권장 표기 |
| `services[].service_url` | string \| null | - | 신청 링크. 있으면 본문에 포함 권장 |
| `services[].image_url` | string \| null | - | 대표 이미지 URL(현재 메시지 생성에 직접 사용 안 함) |
| `services[].place_name` | string \| null | - | 장소명 |
| `services[].area_name` | string \| null | - | 지역(자치구) |
| `services[].service_status` | string \| null | - | 접수 상태. 서울 OpenAPI SVCSTATNM 한글 표시명 그대로(예: `접수중`, `예약마감`) |
| `services[].target_info` | string \| null | - | 신청 대상 안내(예: `누구나`) |
| `services[].receipt_start_dt` | string \| null | - | 접수 시작 일시(ISO-8601) |
| `services[].receipt_end_dt` | string \| null | - | 접수 마감 일시(ISO-8601) |
| `services[].changes` | array | O | 해당 서비스에서 발견된 변경 이벤트. **최소 1건**(빈 배열인 그룹이 있으면 422) |
| `services[].changes[].change_type` | string | O | `NEW` \| `UPDATED` \| `DELETED` (collection의 `ChangeType` enum과 동일) |
| `services[].changes[].field_name` | string \| null | - | 변경된 필드명. 값은 camelCase 엔티티 필드명(예: `serviceStatus`, `placeName`). `NEW`/`DELETED` 시 null 가능 |
| `services[].changes[].old_value` | string \| null | - | 변경 전 값 |
| `services[].changes[].new_value` | string \| null | - | 변경 후 값 |

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
_MAX_SERVICES = 50
_MAX_CHANGES_PER_SERVICE = 50


class ChangeItem(BaseModel):
    change_type: ChangeType
    # JSON 키는 snake_case지만, 이 값은 collection이 기록하는
    # camelCase 엔티티 필드명(예: serviceStatus, receiptEndDt).
    field_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None


class ServiceChangeGroup(BaseModel):
    service_id: str
    service_name: str | None = None
    service_url: str | None = None
    image_url: str | None = None
    place_name: str | None = None
    area_name: str | None = None
    service_status: str | None = None
    target_info: str | None = None
    receipt_start_dt: str | None = None
    receipt_end_dt: str | None = None
    changes: list[ChangeItem]

    @model_validator(mode="after")
    def validate_group(self) -> "ServiceChangeGroup":
        if not self.service_id.strip():
            raise ValueError("service_id는 비어 있을 수 없습니다.")
        if not self.changes:
            raise ValueError(
                f"service_id={self.service_id}의 changes는 최소 1건 이상이어야 합니다."
            )
        if len(self.changes) > _MAX_CHANGES_PER_SERVICE:
            raise ValueError(
                f"service_id={self.service_id}의 changes는 "
                f"{_MAX_CHANGES_PER_SERVICE}건을 초과할 수 없습니다. (현재: {len(self.changes)})"
            )
        return self


class NotificationTemplateRequest(BaseModel):
    services: list[ServiceChangeGroup]

    @model_validator(mode="after")
    def validate_request(self) -> "NotificationTemplateRequest":
        if not self.services:
            raise ValueError("services는 최소 1개 이상이어야 합니다.")
        if len(self.services) > _MAX_SERVICES:
            raise ValueError(
                f"services는 {_MAX_SERVICES}개를 초과할 수 없습니다. (현재: {len(self.services)})"
            )
        return self


class NotificationTemplateResponse(BaseModel):
    title: str
    body: str
```

> `change_type`을 `Literal`로 두면 enum 외 값은 Pydantic이 422로 거른다(§5 참고). `services` 빈 리스트와 그룹별 `changes` 빈 배열은 호출자가 사전 차단하지만, 방어적으로 각각 422 처리한다(`NotificationTemplateRequest`/`ServiceChangeGroup`의 `model_validator`).
> ISO-8601 일시(`receipt_start_dt`/`receipt_end_dt`)는 메시지 표기에만 쓰므로 `str | None`으로 받아 파싱 부담을 피한다. 엄격한 날짜형이 필요하면 `datetime`으로 교체 가능(단 JSON 직렬화 포맷 합의 필요).

---

## 3. `change_type` / `field_name` 의미와 예시

| `change_type` | 의미 | `field_name` | `old_value` / `new_value` |
|---|---|---|---|
| `NEW` | 구독 조건에 맞는 새 서비스 등장 | 보통 null | 보통 null |
| `UPDATED` | 기존 서비스의 특정 필드 변경 | 변경 필드명 | 변경 전/후 값 |
| `DELETED` | 서비스가 더 이상 노출되지 않음(삭제/종료) | 보통 null | 보통 null |

대표 `field_name` 값(collection의 UpsertService가 기록하는 camelCase 엔티티 필드명):

| `field_name` 값 | 한국어 표현(메시지 작성 시 권장) |
|---|---|
| `serviceStatus` | 접수 상태 (예: 접수중 → 예약마감) |
| `placeName` | 장소 |
| `areaName` | 지역(자치구) |
| `receiptStartDt` / `receiptEndDt` | 접수 시작/마감 일시 |
| `serviceOpenStartDt` / `serviceOpenEndDt` | 서비스 개시 시작/종료 일시 |
| `paymentType` | 결제 유형(무료/유료) |

### 예시 데이터

서비스 1개 · 상태 변경(UPDATED 1건):
```json
{ "services": [
  { "service_id": "S250101", "service_name": "OO수영장 자유수영", "area_name": "강남구",
    "service_url": "https://yeyak.seoul.go.kr/...",
    "changes": [{ "change_type": "UPDATED", "field_name": "serviceStatus",
                  "old_value": "접수중", "new_value": "예약마감" }] }
] }
```

서비스 1개 · 여러 변경(UPDATED 2건):
```json
{ "services": [
  { "service_id": "S250101", "service_name": "OO수영장 자유수영",
    "changes": [
      { "change_type": "UPDATED", "field_name": "receiptEndDt",
        "old_value": "2026-06-10", "new_value": "2026-06-20" },
      { "change_type": "UPDATED", "field_name": "serviceStatus",
        "old_value": "예약마감", "new_value": "접수중" }
    ] }
] }
```

여러 서비스(구독 1건이 여러 service_id에 매칭 — 하나의 body로 묶어 안내):
```json
{ "services": [
  { "service_id": "S250101", "service_name": "OO수영장 자유수영", "area_name": "강남구",
    "service_url": "https://yeyak.seoul.go.kr/aaa",
    "changes": [{ "change_type": "UPDATED", "field_name": "serviceStatus",
                  "old_value": "예약마감", "new_value": "접수중" }] },
  { "service_id": "S250602", "service_name": "□□도서관 글쓰기교실", "area_name": "강남구",
    "service_url": "https://yeyak.seoul.go.kr/bbb",
    "changes": [{ "change_type": "NEW", "field_name": null,
                  "old_value": null, "new_value": null }] }
] }
```

신규(NEW) 단일:
```json
{ "services": [
  { "service_id": "S250607", "service_name": "△△문화행사",
    "changes": [{ "change_type": "NEW", "field_name": null,
                  "old_value": null, "new_value": null }] }
] }
```

---

## 4. 권장 구현 방향

### 4.1 LLM 연동

기존 패턴(`llm/generator.py`의 `Generator`, `llm/client.py`의 `get_chat_model`, 프롬프트는 `llm/prompts/` 모듈 상수)을 그대로 재사용한다.

- **프롬프트 배치**: `llm/prompts/notification.py` 신규. 시스템 프롬프트(`NOTIFICATION_SYSTEM`)와 Few-shot 예시를 `router.py`처럼 모듈 상수로 둔다. 프롬프트를 라우터/스키마에 인라인하지 않는다.
- **입력 직렬화**: `NotificationTemplateRequest.services`를 사람이 읽기 쉬운 형태로 정리해 프롬프트에 넣는다(예: 서비스별로 `service_name`/`area_name`/`service_url`/접수기간 + `changes` 요약을 한 블록으로). `image_url`은 메시지 생성에 직접 쓰지 않으니 생략 가능. 여러 서비스를 **그룹 단위로 구분**해 전달하면 LLM이 하나의 body로 묶기 쉽다.
- **호출**: `Generator.generate(prompt, system=NOTIFICATION_SYSTEM)` 또는 구조화 출력이 필요하면 `get_chat_model().with_structured_output(NotificationTemplateResponse)` LCEL 체인. 구조화 출력을 쓰면 title/body 누락을 모델 단에서 강제하기 쉽다.
- **온도**: `temperature=0.0`~`0.3` 권장(알림은 톤 일관성이 중요, 과한 창의성 불필요).
- **provider/timeout**: `get_chat_model`이 이미 `timeout=30`(Gemini) / `request_timeout=30`(OpenAI), `max_retries=3`을 설정한다. 단 **소비자 타임아웃은 10초**(§6)이므로, 이 엔드포인트의 실효 응답은 10초 내여야 한다. LLM 호출에 `asyncio.wait_for(..., timeout=<10초 미만, 예: 8초>)`를 걸어 클라이언트 타임아웃 전에 자체 degrade(§5)로 빠지도록 한다. self-timeout 초과 시 명확한 에러로 전환(§5) — API 서비스의 10초 타임아웃이 발동하기 전에 빠진다.

### 4.2 프롬프트 가이드(메시지 품질)

- **목표**: §6의 fallback 템플릿보다 자연스럽고 개인화된 한국어 메시지. 여러 서비스를 **하나의 매끄러운 body로 통합**.
- **title**: 짧게(권장 ~30자 이내). 핵심을 한눈에. 서비스 1개면 그 변경을, 여러 개면 묶음 안내(예: `관심 서비스 2건 변경 안내`, `접수가 다시 시작됐어요`).
- **body(서비스 1개)**: 1~2문장. 무엇이 어떻게 바뀌었는지 + 행동 유도(예: "지금 신청해 보세요"). `service_url`이 있으면 링크를 본문에 포함(SMS/이메일 양쪽 고려).
- **body(여러 서비스)**: 매칭된 서비스들을 **각각 구분**해 안내한다. `service_name`/`area_name`으로 어떤 서비스인지 식별 가능하게 하고, 서비스별 핵심 변경을 짧게 요약. `service_url`이 있는 서비스는 링크를 함께 제시. 전체를 하나의 메시지로 자연스럽게 이어 작성(목록형 나열도 허용하되 마크다운/이모지는 금지).
- **메타 활용**: `service_name`(이름), `area_name`(지역), `place_name`(장소), `target_info`(대상), `receipt_start_dt`/`receipt_end_dt`(접수기간)가 있으면 적극 활용해 풍부하게. 없는 필드는 언급하지 않는다(추측 금지).
- **노출 금지값**: `service_id`를 본문에 그대로 노출하지 말 것(사용자 친화적이지 않음). `service_status`(및 `serviceStatus` 변경값)는 이미 서울 OpenAPI SVCSTATNM 한글 표시명(예: 접수중/예약마감)이므로 그대로 사용하면 된다 — 내부 영문 enum이 아니다.
- **change_type별 톤**: `NEW`는 등장 안내, `UPDATED`는 변경 안내, `DELETED`는 종료/마감 안내.
- **금지**: 빈 title/body, 마크다운, 이모지(프로젝트 정책), 추측성 사실(없는 일정·가격·링크 지어내기 금지).

### 4.3 빈 응답 방지(핵심)

§2.2 유효성 규칙상 빈 title/body는 fallback과 동일하다. 구현은 **LLM 결과를 그대로 반환하기 전에 자체 검증**한다.

- 구조화 출력이라도 모델이 공백을 채울 수 있으므로, 반환 직전 `title.strip()` / `body.strip()`이 비어 있지 않은지 확인한다.
- 비어 있으면 §5의 자체 최소 메시지(또는 non-2xx)로 전환한다 — **공백 응답을 200으로 흘려보내지 않는다.**

---

## 5. 에러 처리

| 상황 | 처리 | 비고 |
|---|---|---|
| 입력 검증 실패(필수 필드 누락, `change_type` enum 위반, `services` 빈 리스트, 그룹별 `changes` 빈 배열 등) | **422** (Pydantic + `main.py`의 `validation_exception_handler`) | 별도 코드 불필요. 모델 정의만으로 동작 |
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
| 호출 단위 | 구독 1건당 1회. 매칭된 모든 서비스를 `services[]` 그룹 리스트로, 각 서비스의 변경을 `changes` 배열로 묶어 전달(배치) |

> **구조 변경점(단일 → 그룹 리스트)**: 기존 `TemplateAgentClient`는 `{service_id, changes[]}` **단일 서비스** 본문을 보냈다. 구독이 조건 기반이라 하나의 구독이 여러 `service_id`에 매칭되므로, 클라이언트는 매칭된 서비스 전부를 `{services: [{service_id, ...meta, changes[]}, ...]}` **그룹 리스트**로 묶어 보내도록 변경된다. 응답 계약(`{title, body}`)과 유효성·타임아웃·fallback 규칙은 그대로다.

### 클라이언트 fallback 템플릿 (AI 응답 품질의 baseline)

AI 실패 시 클라이언트가 생성하는 형태. **AI는 이보다 자연스러운 메시지를(특히 여러 서비스를 하나로 묶어) 만드는 것이 목표.**

- **title**: `[서울공공서비스] 관심 서비스 {service_count}건 변경 알림`(서비스 1개면 해당 `service_name` 또는 `service_id` 사용)
- **body(서비스 1개·변경 1건)**: `{service_name}의 {field_name}이(가) {old_value} → {new_value}(으)로 변경되었습니다.`
- **body(여러 변경/여러 서비스)**: 위 문장 + ` (외 {n-1}건)` 또는 서비스별 한 줄 요약 나열

---

## 7. 라우터 등록 위치

- 라우터 파일: `routers/notification.py` 신규.
  - `embeddings.py`처럼 `router = APIRouter(prefix="/notification", tags=["notification"])` 선언.
  - 핸들러: `@router.post("/template")` → `async def create_template(req: NotificationTemplateRequest) -> NotificationTemplateResponse`.
  - **async 필수**(LLM 호출은 `await`). 요청 경로에 블로킹 I/O 금지.

```python
# routers/notification.py
import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from schemas.notification import (
    NotificationTemplateRequest,
    NotificationTemplateResponse,
)
# from llm.prompts.notification import NOTIFICATION_SYSTEM, build_prompt
# from llm.generator import Generator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notification", tags=["notification"])

_LLM_TIMEOUT_SECONDS = 8.0  # 소비자(10초)보다 짧게 self-timeout


@router.post("/template")
async def create_template(
    req: NotificationTemplateRequest,
) -> NotificationTemplateResponse:
    try:
        # prompt = build_prompt(req.services)
        # result = await asyncio.wait_for(
        #     Generator().generate_template(prompt, system=NOTIFICATION_SYSTEM),
        #     timeout=_LLM_TIMEOUT_SECONDS,
        # )
        result = await asyncio.wait_for(..., timeout=_LLM_TIMEOUT_SECONDS)  # noqa
    except (TimeoutError, Exception) as exc:  # 타임아웃/네트워크/파싱 등
        logger.warning("notification template LLM 실패, degrade: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="template generation failed",
        ) from exc

    # 빈 응답 방지(§4.3): 공백 title/body를 200으로 흘려보내지 않는다.
    if not result.title.strip() or not result.body.strip():
        logger.warning("notification template 공백 응답, degrade")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="empty template",
        )
    return result
```

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

- [ ] `schemas/notification.py` — §2.3 모델(`ChangeItem`, `ServiceChangeGroup`, `NotificationTemplateRequest`, `NotificationTemplateResponse`)
- [ ] `llm/prompts/notification.py` — `NOTIFICATION_SYSTEM` + Few-shot(여러 서비스 묶음 예시 포함)
- [ ] `routers/notification.py` — `POST /notification/template`, async, self-timeout(<10초), 빈 응답 방지, LLM 실패 시 degrade(§5)
- [ ] `main.py` — `include_router` 추가
- [ ] 테스트(`tests/test_notify_template.py`) — Fake LLM. 케이스: 서비스 1개·변경 1건, 서비스 여러 개(묶음 body), `change_type` 위반 422, `services` 빈 리스트 422, 그룹별 `changes` 빈 배열 422, LLM 예외 시 degrade 경로(non-2xx 또는 최소 템플릿 200), 빈 title/body가 절대 200으로 안 나가는지. **실제 LLM/외부 호출 금지.**
- [ ] `uv run pytest` / `uv run ruff check .` 그린

---

## 9. 변경 이력

| 날짜 | 변경 내용 | 사유 |
|---|---|---|
| 2026-06-01 | `changes[].field_name` **값**을 snake_case → camelCase로 정정(예: `receipt_end_dt`→`receiptEndDt`, `service_status`→`serviceStatus`). JSON 키 자체는 snake_case 유지. §2에 키/값 표기 차이 명시 추가. `service_status` 예시값을 영문 enum(`RECEIPT`/`END`) → 서울 OpenAPI SVCSTATNM 한글 표시명(접수중/예약마감)으로 교체. | collection의 UpsertService가 실제 기록하는 `field_name` 값은 camelCase 엔티티 필드명이며, `service_status` 저장값은 한글 표시명이라 기존 예시가 구현 세션을 오도함 |
| 2026-06-01 | **요청 계약을 단일 `service_id` → 서비스 그룹 리스트(`services[]`) 구조로 전환.** `ServiceChange` → `ChangeItem` 개명, `ServiceChangeGroup`(메타 필드 nullable + `changes`) 신설, `NotificationTemplateRequest.services: list[ServiceChangeGroup]`. `services` 빈 리스트 / 그룹별 `changes` 빈 배열 422. 프롬프트 가이드에 "여러 서비스를 하나의 body로 묶기" + 메타(이름/링크/지역/접수기간) 활용 추가. 응답 계약(`{title, body}`)·유효성·타임아웃·fallback 규칙은 불변. | 알림 구독이 조건 기반이라 하나의 구독이 여러 `service_id`에 동시 매칭됨 — 단일 구조로는 여러 서비스 변경을 한 body로 묶지 못함 |
| 초기 | `POST /notification/template` 단일 서비스(`{service_id, changes[]}`) 계약 작성 | 구현 기준 문서 착수 |
