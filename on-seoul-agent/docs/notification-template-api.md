# 알림 템플릿 생성 API 명세 — `POST /notification/template`

> **상태**: 구현 완료. 본 문서가 계약 기준이다. 응답 계약은 `{title, summary}`(요약/하이라이트 전용).
> **소비자**: API 서비스(Spring, `on-seoul-api`)의 `TemplateAgentClient`.
> **계약 출처**: Spring 클라이언트 `TemplateAgentClient` + ADR-0004(구독 1건당 배치 호출). 요청/응답 JSON 필드명·유효성 규칙은 클라이언트에서 추출한 것이므로 임의로 바꾸지 않는다(교차 경계 계약).

---

## 1. 개요

API 서비스는 서울 공공서비스 예약 데이터를 일 1회 수집한 뒤, 구독 조건에 맞는 변경(신규/변경/삭제)을 감지한다. 변경이 감지된 **구독 1건마다** 이 AI 엔드포인트를 1회 호출하여, 해당 구독자에게 보낼 **알림 제목(`title`)과 요약/하이라이트(`summary`)** 를 생성받는다. 단, `title`은 라우터 코드가 날짜·서비스 개수로 결정적으로 생성하고 **AI(LLM)는 `summary`만 생성**한다.

> **역할 재정의(중요)**: 서비스명·상태·접수기간·링크 같은 "사실 정보"는 소비자(API 서비스)의 **Knock 이메일 Liquid 템플릿이 표/카드로 결정적(deterministic)으로 렌더링**한다. 따라서 AI는 사실을 본문에 재나열하지 않고, **"무엇을 주목해야 하는지"만 담은 짧은 요약/하이라이트(`summary`)** 만 생성한다. 이전 설계(서비스명·링크·상태를 `body`에 필수 포함하는 완성 본문 생성)는 **폐기**됐다 — AI의 링크·상태 환각 위험이 사라지고 책임이 명확해진다.

API 서비스의 알림 구독은 **조건 기반**(키워드/상태/지역 필터)이라 **하나의 구독이 여러 `service_id`에 동시 매칭**된다. 따라서 한 번의 호출에 매칭된 **여러 서비스의 변경이 함께 담겨** 오며, AI는 이들을 묶어 **하나의 짧은 `summary`** 로 하이라이트한다(가장 중요한 1~2건만, 전부 나열 금지).

- **호출 주체**: API 서비스의 알림 발송 파이프라인(`TemplateAgentClient`).
- **배치 모델(ADR-0004)**: 한 구독에서 발견된 모든 변경을 **서비스 그룹 리스트**(`services[]`)로 묶어 **1회 호출**한다. 서비스 M개·변경 N건 → AI 호출 1회. 각 서비스 그룹은 자신의 메타 정보와 `changes` 배열을 갖는다. 빈 리스트는 호출자가 사전 차단하므로 정상 호출은 보통 서비스 1개 이상이다.
- **역할 분리**: 알림 *발송*(SMS/이메일)은 API 서비스가 담당한다. 이 엔드포인트는 메시지 *생성*만 책임진다.
- **degrade 정책**: AI 응답이 유효하지 않거나(빈 title/summary), 호출이 실패하거나(타임아웃·non-2xx), 파싱이 안 되면 **클라이언트가 자체 fallback 템플릿으로 대체**한다(§6). 따라서 이 엔드포인트는 알림 누락의 단일 실패점이 아니다 — 하지만 fallback보다 자연스러운 요약을 생성하는 것이 목표다.

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
{ "title": "...", "summary": "..." }
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `title` | string | 알림 제목/SMS 첫 줄. **non-null & 공백 아님**. **라우터 코드가 결정적으로 생성**(LLM 아님): `온서울 맞춤 {월}월 {일}일 공공서비스 정보 - {service_count}개` |
| `summary` | string | 짧은 요약/하이라이트(2~3문장 이내). **non-null & 공백 아님**. LLM 생성 |

> **유효성 규칙(중요)**: 클라이언트는 `title`과 `summary`가 **둘 다 non-null이고 `!isBlank()`** 일 때만 AI 응답을 채택한다. 둘 중 하나라도 null·빈문자·공백이면 클라이언트가 fallback으로 전환한다.
> → `title`은 코드가 결정적으로 생성하므로 항상 non-blank가 보장된다. **AI는 의미 있는 `summary`를 반드시 채워야 한다.** 빈 문자열이나 공백만 채운 `summary`는 503으로 degrade된다(§4.3).
> **`summary`는 사실 재나열이 아니다.** 서비스명·상태·접수기간·링크는 Knock Liquid 템플릿이 별도로 보여주므로, `summary`에는 "무엇을 주목할지"(접수 임박·신규·종료·상태 변화)만 담는다(§4.2).

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
    summary: str
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

여러 서비스(구독 1건이 여러 service_id에 매칭 — 하나의 summary로 묶어 하이라이트):
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
- **입력 직렬화**: `NotificationTemplateRequest.services`를 사람이 읽기 쉬운 형태로 정리해 프롬프트에 넣는다(예: 서비스별로 `service_name`/`area_name`/`service_url`/접수기간 + `changes` 요약을 한 블록으로). `image_url`은 메시지 생성에 직접 쓰지 않으니 생략 가능. 여러 서비스를 **그룹 단위로 구분**해 전달하면 LLM이 하나의 summary로 묶기 쉽다.
- **호출**: `get_chat_model().with_structured_output(_HighlightResponse)` LCEL 체인. 구조화 출력으로 `{summary}` 누락을 모델 단에서 강제한다. `_HighlightResponse`는 라우터 내부 전용 모델(`summary`만)이며, 응답 직전 검증 후 `title`(코드 생성)과 함께 `NotificationTemplateResponse`로 옮긴다.
- **service_id 비노출**: 입력 직렬화 시 각 서비스 블록 헤더를 `[서비스 N]` 인덱스로만 표기하고 `service_id`는 절대 프롬프트에 넣지 않는다(출력 누출 구조적 차단).
- **온도**: `temperature=0.0`~`0.3` 권장(알림은 톤 일관성이 중요, 과한 창의성 불필요).
- **provider/timeout**: `get_chat_model`이 이미 `timeout=30`(Gemini) / `request_timeout=30`(OpenAI), `max_retries=3`을 설정한다. 단 **소비자 타임아웃은 10초**(§6)이므로, 이 엔드포인트의 실효 응답은 10초 내여야 한다. LLM 호출에 `asyncio.wait_for(..., timeout=<10초 미만, 예: 8초>)`를 걸어 클라이언트 타임아웃 전에 자체 degrade(§5)로 빠지도록 한다. self-timeout 초과 시 명확한 에러로 전환(§5) — API 서비스의 10초 타임아웃이 발동하기 전에 빠진다.

### 4.2 시스템 프롬프트 의도(요약/하이라이트 전용)

`NOTIFICATION_SYSTEM`은 AI에게 **사실을 재나열하지 말고 행동 유도 포인트만 하이라이트**하도록 지시한다. 핵심 의도:

- **사실 재나열 금지**: 서비스명·상태·접수기간·링크는 Knock Liquid 템플릿이 표/카드로 보여주므로, `summary`에서 모두 옮겨 적지 않는다. AI는 "무엇을 주목할지"에 집중한다.
- **행동 유도 우선순위**: 접수 임박(`receiptEndDt` 가까움) > 신규 등장(`NEW`) > 상태 변화(접수 재개 등) > 종료(`DELETED`) > 기타. 가장 가치 높은 1~2건만 골라 강조.
- **여러 서비스**: "구독하신 조건에 N건의 변경이 있어요" 류로 묶고, 가장 중요한 1~2건만 하이라이트. **전부 나열 금지.**
- **title**: LLM이 생성하지 않는다. 라우터 코드(`_make_title`)가 `온서울 맞춤 {월}월 {일}일 공공서비스 정보 - {service_count}개` 포맷으로 결정적으로 생성한다.
- **summary**: 2~3문장 이내(SMS 고려). 한국어 필수.
- **노출 금지**: `service_id`, `[서비스 N]` 인덱스, `field_name`의 camelCase 값(serviceStatus 등) 같은 내부 식별자. `service_status` 변경값(접수중/예약마감 등 한글 표시명)은 필요 시 자연스럽게 사용 가능.
- **change_type별 톤**: `NEW`=등장 안내, `UPDATED`=변경/행동 유도, `DELETED`=차분한 종료 안내.
- **추측 금지**: 입력에 없는 사실(일정·가격·링크)을 지어내지 않는다.
- **봉인 평가셋(`scripts/eval/eval_set_holdout.tsv`)은 프롬프트/few-shot에 절대 사용 금지**(검색 recall 평가셋이며 본 프롬프트와 무관하지만 명시적으로 금지).

`summary`만 LLM이 생성한다(구조화 출력 `{summary}`). `title`은 라우터 코드(`_make_title(len(req.services))`)가 날짜·서비스 개수로 결정적으로 생성한다 — LLM이 만든 제목은 일관성이 없어, 브랜드 톤(`온서울 맞춤 …`)을 고정하기 위해 코드 생성으로 되돌렸다.

### 4.3 빈 응답 방지(핵심)

§2.2 유효성 규칙상 빈 summary는 fallback과 동일하다. 구현은 **LLM 결과를 그대로 반환하기 전에 자체 검증**한다.

- 구조화 출력이라도 모델이 공백을 채울 수 있으므로, 반환 직전 `summary.strip()`이 비어 있지 않은지 확인한다.
- 비어 있으면 503으로 전환한다 — **공백 응답을 200으로 흘려보내지 않는다.**
- `title`은 코드 생성이므로 빈 값이 될 수 없어 별도 검증하지 않는다.

---

## 5. 에러 처리

| 상황 | 처리 | 비고 |
|---|---|---|
| 입력 검증 실패(필수 필드 누락, `change_type` enum 위반, `services` 빈 리스트, 그룹별 `changes` 빈 배열 등) | **422** (Pydantic + `main.py`의 `validation_exception_handler`) | 별도 코드 불필요. 모델 정의만으로 동작 |
| LLM 호출 실패(타임아웃/네트워크/파싱) | **503** | 아래 참고 |
| LLM 결과가 빈 summary | **503** | §4.3 (title은 코드 생성이라 빈 값 불가) |

> **현재 구현**은 LLM 실패·타임아웃·빈 응답을 모두 **503**(detail: `알림 템플릿 생성에 실패했습니다.`)로 degrade한다. 소비자는 503을 fallback 트리거로 처리하므로 알림 누락은 발생하지 않는다.

**LLM 실패 시 AI 서비스 자체 동작 권고:**

- 클라이언트는 어떤 실패든(타임아웃·non-2xx·빈 응답) fallback으로 안전하게 처리하므로, **non-2xx(예: 503) 반환도 알림 누락을 유발하지 않는다.** 따라서 둘 중 어느 쪽도 안전하다:
  1. **(채택) non-2xx 반환(503 Service Unavailable)** — LLM 장애를 명확히 신호. 클라이언트가 fallback으로 전환. 구현이 단순하고 관측이 명확하여 현재 구현은 이 방식을 쓴다.
  2. AI 서비스가 자체 최소 템플릿을 200으로 반환하는 방식도 가능하나, 건수 기반 fallback 제목은 이미 소비자가 더 잘 만들므로 채택하지 않는다.
- **금지**: LLM 실패 시 빈 title/summary를 200으로 반환(클라이언트가 fallback하므로 결과는 같으나, 200+빈응답은 관측·디버깅을 흐림). 명시적으로 degrade(503)하라.
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
| 응답 유효성 | `title`·`summary`가 **둘 다 non-null & `!isBlank()`** 일 때만 채택. 미달 시 fallback |
| 호출 단위 | 구독 1건당 1회. 매칭된 모든 서비스를 `services[]` 그룹 리스트로, 각 서비스의 변경을 `changes` 배열로 묶어 전달(배치) |

> **응답 계약 변경(`{title, body}` → `{title, summary}`)**: 이전 설계는 서비스명·링크·상태를 `body`에 필수 포함하는 **완성 본문**을 AI가 생성했다. 사실 표시 책임이 Knock 이메일 Liquid 템플릿으로 이전됐으므로, AI는 사실 재나열을 멈추고 짧은 **요약/하이라이트(`summary`)** 만 생성한다. 요청 계약(`{services: [...]}`)·유효성(둘 다 non-blank)·타임아웃·fallback 규칙은 그대로다.

### 클라이언트 fallback 템플릿 (AI 응답 실패 시 baseline)

AI가 503·공백·타임아웃이면 클라이언트가 결정적으로 생성하는 형태. 사실(서비스명·상태·접수기간·링크)은 어차피 Knock Liquid가 표/카드로 보여주므로, fallback도 건수 기반의 단순 안내로 충분하다.

- **title**: `구독하신 {service_count}개 서비스 변경 알림`(서비스 1개면 해당 `service_name`)
- **summary**: 건수 기반 결정적 요약(예: `구독 조건에 {n}건의 변경이 있어요.`)

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

    # 빈 응답 방지(§4.3): 공백 summary를 200으로 흘려보내지 않는다.
    if not result.summary.strip():
        logger.warning("notification template 공백 응답, degrade")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="empty template",
        )
    # title은 코드 결정적 생성, summary는 LLM 생성
    return NotificationTemplateResponse(title=_make_title(len(req.services)), summary=result.summary)
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

- [x] `schemas/notification.py` — §2.3 모델(`ChangeItem`, `ServiceChangeGroup`, `NotificationTemplateRequest`, `NotificationTemplateResponse`=`{title, summary}`)
- [x] `llm/prompts/notification.py` — `NOTIFICATION_SYSTEM`(요약/하이라이트 전용) + Few-shot(`{title, summary}` 묶음 예시)
- [x] `routers/notification.py` — `POST /notification/template`, async, self-timeout(8초), 빈 title/summary 방지, LLM 실패 시 503(§5)
- [x] `main.py` — `include_router` 등록(기존)
- [x] 테스트(`tests/test_notify_template.py`) — AsyncMock LLM. 케이스: 서비스 1개/여러 개 요약, `change_type` 위반 422, `services` 빈 리스트 422, 그룹별 `changes` 빈 배열 422, self-timeout 503, LLM 예외 503, 빈 title/빈 summary가 절대 200으로 안 나가는지. **실제 LLM/외부 호출 금지.**
- [x] `uv run pytest` / `uv run ruff check .` 그린

---

## 9. 변경 이력

| 날짜 | 변경 내용 | 사유 |
|---|---|---|
| 2026-06-03 | **시점 트리거(`trigger_type`) 지원 추가.** 요청 최상위 `trigger_type: Literal["CHANGE","OPEN_DAY","BEFORE_RECEIPT_D1","DEADLINE_DDAY"]`(누락 시 `CHANGE` 기본). `CHANGE`는 그룹당 `changes` 최소 1건 필수(요청 레벨 검증), 시점 3종은 빈 `changes` 허용. `_TRIGGER_HINTS`를 `_build_messages`가 SystemMessage에 주입하고, `_format_group`은 빈 changes 시 "- 변경:" 블록을 생략(trigger-aware). `build_prompt` 신설 없음, `_make_title`·degrade·8초 self-timeout·응답 계약 `{title, summary}` 불변. | API 서비스가 개시일/접수 D-1/마감 당일 시점 알림을 추가해 `trigger_type`을 보내기 시작 |
| 2026-06-01 | **`title` 생성 출처를 LLM → 코드로 변경.** `summary`만 LLM이 생성하고(`_HighlightResponse{summary}`), `title`은 라우터 코드 `_make_title(len(req.services))`가 `온서울 맞춤 {월}월 {일}일 공공서비스 정보 - {service_count}개` 포맷으로 결정적 생성. 프롬프트(`NOTIFICATION_SYSTEM`)·few-shot에서 `title` 제거(출력 JSON `{summary}`만). degrade 검사는 빈 `summary`→503만 유지(빈 `title` 검사 제거). 응답 계약 `{title, summary}`·유효성(둘 다 non-blank)·타임아웃·fallback 규칙은 불변. | LLM이 생성하던 제목이 일관성이 없어 브랜드 톤(`온서울 맞춤 …`)을 코드로 고정 |
| 2026-06-01 | **역할 재정의: "완성 본문(`body`) 생성" → "요약/하이라이트(`summary`) 생성".** 응답 계약 `{title, body}` → `{title, summary}`. AI는 서비스명·상태·접수기간·링크 같은 사실을 재나열하지 않고 행동 유도 하이라이트(접수 임박·신규·종료·상태 변화)만 생성. `title`도 LLM이 맥락 반영해 생성(이전 `_make_title` 건수 기반 코드 생성 제거 → `_SummaryResponse{title, summary}` 구조화 출력). 프롬프트(`NOTIFICATION_SYSTEM`)·few-shot 전면 교체. 빈 title 또는 빈 summary → 503. | 사실 표시 책임이 소비자(API 서비스)의 Knock 이메일 Liquid 템플릿(표/카드 결정적 렌더링)으로 이전됨 — AI가 사실을 본문에 재나열할 필요가 사라지고 링크·상태 환각 위험이 제거됨 |
| 2026-06-01 | `changes[].field_name` **값**을 snake_case → camelCase로 정정(예: `receipt_end_dt`→`receiptEndDt`, `service_status`→`serviceStatus`). JSON 키 자체는 snake_case 유지. §2에 키/값 표기 차이 명시 추가. `service_status` 예시값을 영문 enum(`RECEIPT`/`END`) → 서울 OpenAPI SVCSTATNM 한글 표시명(접수중/예약마감)으로 교체. | collection의 UpsertService가 실제 기록하는 `field_name` 값은 camelCase 엔티티 필드명이며, `service_status` 저장값은 한글 표시명이라 기존 예시가 구현 세션을 오도함 |
| 2026-06-01 | **요청 계약을 단일 `service_id` → 서비스 그룹 리스트(`services[]`) 구조로 전환.** `ServiceChange` → `ChangeItem` 개명, `ServiceChangeGroup`(메타 필드 nullable + `changes`) 신설, `NotificationTemplateRequest.services: list[ServiceChangeGroup]`. `services` 빈 리스트 / 그룹별 `changes` 빈 배열 422. 프롬프트 가이드에 "여러 서비스를 하나의 body로 묶기" + 메타(이름/링크/지역/접수기간) 활용 추가. 응답 계약(`{title, body}`)·유효성·타임아웃·fallback 규칙은 불변. | 알림 구독이 조건 기반이라 하나의 구독이 여러 `service_id`에 동시 매칭됨 — 단일 구조로는 여러 서비스 변경을 한 body로 묶지 못함 |
| 초기 | `POST /notification/template` 단일 서비스(`{service_id, changes[]}`) 계약 작성 | 구현 기준 문서 착수 |
