# AI 서비스 작업 가이드 — 시점 트리거(`trigger_type`) 지원 추가

> **대상 세션**: on-seoul-agent (AI 서비스) 단독 작업 세션.
> **선행 문서**: `notification-template-api.md` (이 문서는 그 계약의 **델타/확장분**만 다룬다. 기존 계약·degrade·self-timeout 규칙은 그대로 유효).
> **계약 출처**: API 서비스(on-seoul-api)에 시점 기반 알림 트리거가 이미 구현·커밋됨. 요청 본문에 `trigger_type` 필드가 **추가**되어 들어오기 시작한다.
> **상태**: 미구현. 이 문서를 기준으로 AI 서비스를 수정한다.

---

## 1. 배경 — 왜 바뀌나

기존 알림은 **변경 이벤트 기반(change_log)** 한 종류뿐이었다. 이제 API 서비스가 **시점 기반 트리거 3종**을 추가해, 총 4종의 트리거가 `POST /notification/template`을 호출한다.

| `trigger_type` | 의미 | 발생 시점 | `changes` 유무 |
|---|---|---|---|
| `CHANGE` | 구독 조건에 맞는 서비스의 **필드 변경** 감지(기존 동작) | 수집 후 변경 감지 시 | **있음** (1건 이상) |
| `OPEN_DAY` | 서비스 **개시일 도래** (오늘 개시) | 매일 09:30 잡 | **없음** (빈 배열) |
| `BEFORE_RECEIPT_D1` | **접수 시작 D-1** (내일 접수 시작) | 매일 09:30 잡 | **없음** (빈 배열) |
| `DEADLINE_DDAY` | **접수 마감 당일** (오늘 마감, 아직 접수중) | 매일 09:30 잡 | **없음** (빈 배열) |

**핵심 변화 2가지**:
1. 요청 본문에 최상위 `trigger_type` 필드가 추가된다.
2. 시점 트리거(`OPEN_DAY`/`BEFORE_RECEIPT_D1`/`DEADLINE_DDAY`)는 **`changes`가 빈 배열**로 온다 — "바뀐 게 있어서"가 아니라 "오늘이 그 날짜라서" 알리는 것이기 때문. 기존 `ServiceChangeGroup.changes` 최소 1건 검증을 **시점 트리거에서는 완화**해야 한다.

`CHANGE`는 기존 동작과 100% 동일하다.

---

## 2. 요청 스키마 변경 (`schemas/notification.py`)

### 2.1 추가되는 필드

```json
{
  "trigger_type": "DEADLINE_DDAY",
  "services": [
    {
      "service_id": "S250101",
      "service_name": "OO수영장 자유수영",
      "service_url": "https://yeyak.seoul.go.kr/...",
      "area_name": "강남구",
      "service_status": "접수중",
      "receipt_start_dt": "2026-06-01T09:00:00",
      "receipt_end_dt": "2026-06-03T18:00:00",
      "changes": []
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `trigger_type` | string | **O (신규)** | `CHANGE` \| `OPEN_DAY` \| `BEFORE_RECEIPT_D1` \| `DEADLINE_DDAY`. enum 외 값은 422 |
| `services[].changes` | array | 조건부 | `CHANGE`면 그룹당 최소 1건. **시점 3종이면 빈 배열 허용** |

> **하위호환 주의**: 기존 호출자(레거시 CHANGE 경로)가 `trigger_type`을 생략할 수도 있는지 확인하라. API 서비스 구현상 `TemplateAgentRequest`가 항상 `trigger_type`을 채워 보낸다(기본 `CHANGE`). 방어적으로 **`trigger_type` 누락 시 `CHANGE`로 기본값** 처리하면 안전하다.

### 2.2 Pydantic 수정 포인트

기존 `schemas/notification.py`(이미 구현됨)에 다음을 적용:

```python
from typing import Literal

TriggerType = Literal["CHANGE", "OPEN_DAY", "BEFORE_RECEIPT_D1", "DEADLINE_DDAY"]


class ServiceChangeGroup(BaseModel):
    # ... 기존 필드 동일 ...
    changes: list[ChangeItem]  # 기존엔 최소 1건 검증이 그룹 validator에 있었음

    # ⚠️ validate_group의 "changes 최소 1건" 규칙을 그룹 단독으로 강제하지 말 것.
    #    시점 트리거는 changes가 빈 배열이다. changes 검증은 요청 레벨에서
    #    trigger_type과 함께 판단해야 한다(아래 NotificationTemplateRequest 참고).


class NotificationTemplateRequest(BaseModel):
    trigger_type: TriggerType = "CHANGE"   # 신규. 누락 시 CHANGE 기본
    services: list[ServiceChangeGroup]

    @model_validator(mode="after")
    def validate_request(self) -> "NotificationTemplateRequest":
        if not self.services:
            raise ValueError("services는 최소 1개 이상이어야 합니다.")
        if len(self.services) > _MAX_SERVICES:
            raise ValueError(...)
        # trigger_type별 changes 규칙
        if self.trigger_type == "CHANGE":
            for g in self.services:
                if not g.changes:
                    raise ValueError(
                        f"CHANGE 트리거의 service_id={g.service_id} changes는 "
                        "최소 1건이어야 합니다."
                    )
        # 시점 트리거(OPEN_DAY/BEFORE_RECEIPT_D1/DEADLINE_DDAY)는 changes 빈 배열 허용
        return self
```

> **결정 필요**: 기존 `ServiceChangeGroup.validate_group`에 "changes 최소 1건"이 박혀 있으면, 그 규칙을 그룹에서 제거하고 위처럼 **요청 레벨에서 trigger_type 분기**로 옮긴다. 그룹 validator는 `service_id` 비어있음·`changes` 상한(50건) 검증만 남긴다.

---

## 3. 시스템 프롬프트 분기 (`llm/prompts/notification.py`)

`summary`만 LLM이 생성한다(§기존 문서 4.2 그대로). 트리거 종류에 따라 **하이라이트 관점이 달라지므로** trigger_type별 지시를 프롬프트에 주입한다.

| `trigger_type` | summary 톤·관점 | 예시 문구 방향 |
|---|---|---|
| `CHANGE` | 무엇이 바뀌었는지 행동 유도 (기존) | "접수 마감일이 미뤄졌어요", "다시 접수 중이에요" |
| `OPEN_DAY` | 오늘부터 이용/안내 시작 | "오늘부터 만나볼 수 있어요" |
| `BEFORE_RECEIPT_D1` | **내일** 접수 시작 — 준비 유도 | "내일 접수가 시작돼요. 미리 준비하세요" |
| `DEADLINE_DDAY` | **오늘** 마감 — 긴박감 | "오늘 접수가 마감돼요. 서두르세요" |

구현 권장:
- `NOTIFICATION_SYSTEM`(공통 규칙: 사실 재나열 금지, service_id/내부식별자 비노출, 2~3문장, 한국어)은 유지.
- trigger_type별 **추가 지시 1~2문장**을 `build_prompt(trigger_type, services)`에서 동적으로 덧붙인다. 별도 상수(예: `_TRIGGER_HINTS: dict[TriggerType, str]`)로 둔다.
- 시점 트리거는 `changes`가 비어 있으므로, 입력 직렬화 시 변경 블록 대신 **서비스 메타(이름/지역/접수기간/상태)와 "오늘/내일 마감·시작·개시" 맥락**을 넣는다. `receipt_start_dt`/`receipt_end_dt`를 활용해 "내일"·"오늘"을 자연스럽게 표현하되, **날짜를 그대로 재나열하진 않는다**(Knock 카드가 이미 보여줌).

> **사실 재나열 금지 원칙은 트리거 종류와 무관하게 유지**된다. 시점 트리거에서도 summary는 "주목 포인트"만 담는다.

---

## 4. title 생성 (`routers/notification.py`)

현재 `title`은 코드가 결정적으로 생성한다(`_make_title`, `온서울 맞춤 {월}월 {일}일 공공서비스 정보 - {service_count}개`). 시점 트리거에 대해 title을 어떻게 할지 **결정 필요**:

- **옵션 A (권장, 최소 변경)**: 기존 `_make_title` 그대로 사용. title은 브랜드 톤 고정값이고 trigger 맥락은 summary가 전달하므로 충분. **이 옵션이면 라우터 title 로직 변경 없음.**
- **옵션 B**: trigger_type별 title 분기(예: 마감 D-day → "오늘 마감! …"). 긴박감을 제목에서부터 주고 싶을 때. 단 API 서비스의 fallback title과 톤이 갈릴 수 있으니 일관성 확인 필요.

→ 특별한 요구가 없으면 **옵션 A**로 두고, 라우터는 `trigger_type`을 `build_prompt`에만 넘긴다.

---

## 5. 라우터 핸들러 수정 요지

```python
@router.post("/template")
async def create_template(req: NotificationTemplateRequest) -> NotificationTemplateResponse:
    try:
        prompt = build_prompt(req.trigger_type, req.services)   # trigger_type 전달
        result = await asyncio.wait_for(
            Generator().generate_summary(prompt, system=NOTIFICATION_SYSTEM),
            timeout=_LLM_TIMEOUT_SECONDS,  # 8초 (소비자 10초보다 짧게)
        )
    except (TimeoutError, Exception) as exc:
        logger.warning("notification template LLM 실패, degrade: %s", exc)
        raise HTTPException(status_code=503, detail="template generation failed") from exc

    if not result.summary.strip():
        raise HTTPException(status_code=503, detail="empty template")
    return NotificationTemplateResponse(title=_make_title(len(req.services)), summary=result.summary)
```

degrade(503)·self-timeout(8초)·빈 summary 방지 규칙은 **기존 문서 §4.3/§5 그대로**. API 서비스가 503·타임아웃·빈 응답을 모두 fallback으로 처리하므로(이미 trigger_type별 fallback 문구 구현됨) 알림 누락은 없다.

---

## 6. 테스트 (`tests/test_notify_template.py` 보강)

기존 케이스 유지 + 아래 추가. **실제 LLM/외부 호출 금지(AsyncMock)**:

- `trigger_type` 4종 각각 정상 200 (summary 생성)
- `trigger_type` enum 외 값 → 422
- **시점 트리거 + `changes: []` → 200** (빈 changes 허용 확인)
- **`CHANGE` + `changes: []` → 422** (CHANGE는 changes 필수)
- `trigger_type` 누락 → CHANGE 기본값으로 동작 (하위호환)
- trigger_type별로 `build_prompt`에 올바른 힌트가 주입되는지 (프롬프트 단위 검증)
- 기존 degrade(503)·self-timeout·빈 summary 케이스 회귀

---

## 7. 작업 체크리스트

- [ ] `schemas/notification.py` — `TriggerType` Literal, `NotificationTemplateRequest.trigger_type`(기본 CHANGE), changes 검증을 요청 레벨 trigger_type 분기로 이동
- [ ] `llm/prompts/notification.py` — `_TRIGGER_HINTS` + `build_prompt(trigger_type, services)` 분기, 시점 트리거 입력 직렬화(빈 changes 대응)
- [ ] `routers/notification.py` — `trigger_type`을 `build_prompt`에 전달 (title은 옵션 A 유지)
- [ ] `tests/test_notify_template.py` — §6 케이스 보강
- [ ] `uv run pytest` / `uv run ruff check .` 그린
- [ ] `notification-template-api.md` 변경 이력에 `trigger_type` 지원 1줄 추가

---

## 8. 계약 불변 사항 (바꾸지 말 것)

- 응답 계약 `{title, summary}` 유지. `summary`만 LLM 생성, `title`은 코드 생성.
- snake_case JSON 키, `field_name` **값**은 camelCase(serviceStatus 등) — §기존 문서 §2 그대로.
- self-timeout 8초, degrade 503, 빈 summary → 503.
- `service_id`·내부 인덱스·camelCase 필드명 프롬프트/출력 비노출.
- API 서비스 측은 이미 완료됨(이 세션에서 손대지 않음): `trigger_type`을 보내고, AI 실패 시 trigger_type별 fallback으로 발송한다.
