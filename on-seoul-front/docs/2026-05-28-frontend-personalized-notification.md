# 프론트엔드 — 개인화 알림 관리 기능 구현 가이드

이 문서는 `on-seoul` 프론트엔드 구현 에이전트가 **개인화 알림 관리 화면**을 개발할 때 참고하는 작업 가이드입니다. 백엔드(`on-seoul-api` notification BC)의 도메인 모델과 데이터 구조를 기반으로, 사용자가 자신의 알림 구독을 등록·수정·해지하고 발송 이력을 확인하는 흐름을 정의합니다.

> **Note:** 백엔드의 구독 CRUD/발송 이력 REST 컨트롤러는 구현 완료되어 있습니다 (§9 협업 체크리스트 참조).
>
> **Warning:** 현재 운영 중인 Knock 계정이 무료 등급이라 **SMS 발송이 동작하지 않습니다**. 프론트엔드는 **채널을 EMAIL 전용으로 노출**하고, SMS 관련 UI는 모두 숨김 또는 "준비 중" 처리합니다. 백엔드는 SMS 채널을 도메인 모델에 그대로 두되, 프론트에서 SMS를 포함하지 않음으로써 발송 실패를 차단합니다. Knock 유료 전환 후 본 제약을 해제합니다.

---

## 1. 기능 개요

개인화 알림이란, 사용자가 관심 있는 **공공서비스 예약(`public_service_reservations`)** 에 대해 상태/지역/카테고리 조건을 지정해두면, 해당 조건에 매칭되는 변경이 발생할 때 EMAIL 또는 SMS로 알림을 받는 기능입니다.

알림 발송 자체는 백엔드 스케줄러가 주기적으로 처리합니다. 프론트엔드의 역할은 **사용자가 자신의 구독을 시각적으로 관리할 수 있는 UI** 를 제공하는 것입니다.

### 사용자 시나리오

1. OAuth2(Google/Kakao) 로그인 시 백엔드가 5개 기본 카테고리에 대한 구독을 자동 생성합니다 (`CreateDefaultSubscriptionsUseCase`).
2. 사용자가 알림 관리 화면에 진입해 현재 구독 목록을 확인합니다.
3. 각 구독의 필터(상태/지역/카테고리)와 수신 채널(EMAIL/SMS)을 조정합니다.
4. SMS 수신을 원하면 전화번호를 등록합니다.
5. 발송된 알림 이력을 확인합니다.

---

## 2. 도메인 용어

프론트엔드 코드와 UI 텍스트에서 백엔드 도메인 용어와 일관성을 유지합니다.

| 용어 | 영문 | 설명 |
|---|---|---|
| 구독 | Subscription | 사용자-서비스 1:1 매핑. `(user_id, service_id)` 조합으로 중복 방지 |
| 필터 | Filter | 구독 내부의 매칭 조건. 상태·지역·카테고리 화이트리스트 |
| 채널 | Channel | 알림 수신 수단. `EMAIL` 또는 `SMS`. 1개 이상 필수 |
| 발송 이력 | Dispatch | 실제 발송된 알림 1건. 제목·본문·발송 시각·상태 보유 |
| 배치 | Batch | 스케줄러 1회 실행 단위. 발송 이력의 부모 |
| 연락처 | Contact | 사용자의 이메일·전화번호. PII로 암호화 저장 |

---

## 3. 화면 구성

다음 4개 화면을 구현합니다. 라우트와 화면명은 프로젝트 컨벤션에 맞춰 조정합니다.

### 3.1 알림 설정 메인 (`/settings/notifications`)

사용자의 모든 구독을 카드 또는 리스트로 표시합니다.

- 각 구독 카드 표시 항목
  - 서비스 이름(`service_name`) — 외부에서 조회한 메타정보
  - 현재 필터 요약 (예: "강남구·문화행사·접수중")
  - 활성 채널 뱃지 (EMAIL, SMS)
  - 마지막 발송 시각(`last_notified_at`) — 없으면 "발송 이력 없음"
  - **편집** / **해지** 버튼
- 우상단 **새 구독 추가** 버튼

### 3.2 구독 편집 모달/페이지

기존 구독을 수정합니다.

- **필터 섹션**
  - 상태: `RECEIVING` / `CLOSED` / `STANDBY` 등 다중 선택 체크박스
  - 지역: 서울시 구(區) 다중 선택
  - 카테고리: `문화행사` / `체육시설` / `시설대관` / `교육` / `진료` 다중 선택
  - 모든 항목을 비우면 "모든 변경 알림 받기"로 안내
- **채널 섹션 (현재 EMAIL 전용)**
  - EMAIL 활성 상태로 고정 표시 — 별도 토글 없이 "이메일로 받기"로만 노출
  - SMS 토글은 v1 범위에서 비공개. UI에 노출하지 않음 (또는 비활성 + "준비 중" 안내)
  - Knock 유료 전환 시 SMS 토글 복원 — 이때 전화번호 미등록 시 등록 유도 배너 추가
- **저장 / 취소** 버튼

### 3.3 새 구독 추가

서비스 검색 후 구독을 생성합니다.

1. 서비스 검색창 (`service_name` 또는 `service_id` 검색)
2. 검색 결과 클릭 → 구독 편집 화면으로 진입 (기본 필터: 모두 비움, 채널: EMAIL 고정)
3. **저장** 시 구독 생성 API 호출

> **Note:** 검색 API는 `collection` BC 또는 신규 컨트롤러로 제공됩니다. 백엔드 팀과 조율이 필요합니다.

### 3.4 발송 이력 (`/settings/notifications/history`)

지난 알림 발송 이력을 시간순으로 표시합니다.

- 각 이력 항목
  - 발송 시각(`sent_at`)
  - 서비스 이름
  - 제목(`generated_title`) / 본문(`generated_body`)
  - 채널 (현재는 EMAIL만 노출. 응답에 SMS가 포함되어 들어와도 화면에서는 v1 범위에서 표시하지 않음)
  - 상태 (`SUCCESS` / `FAILED`)
- 페이지네이션 또는 무한 스크롤

### 3.5 연락처 등록 (v1 비공개)

SMS 채널 활성화에 필요한 전화번호를 등록·수정합니다. **v1 범위에서는 화면 노출하지 않습니다** (Knock 유료 전환 후 활성화).

- 입력 필드: 전화번호 (E.164 형식 권장: `+821012345678`)
- 저장 시 `PATCH /api/users/me/contact` 호출

---

## 4. API 계약

JWT 인증이 모든 엔드포인트에 필요합니다. 백엔드는 `JwtAuthenticationFilter`가 요청 속성 `userId`를 설정한 후 컨트롤러에 전달합니다.

### 4.1 구독 목록 조회 (신규 — 백엔드 구현 필요)

```http
GET /api/notifications/subscriptions
Authorization: Bearer <access_token>
```

**응답 (200 OK):**

```json
{
  "subscriptions": [
    {
      "id": 12,
      "serviceId": "OA-2269",
      "serviceName": "서울시 문화행사 공공서비스예약 정보",
      "filter": {
        "statuses": ["RECEIVING"],
        "areaNames": ["강남구"],
        "maxClassNames": ["문화행사"]
      },
      "channels": ["EMAIL", "SMS"],
      "lastNotifiedAt": "2026-05-26T14:30:00Z",
      "createdAt": "2026-05-01T09:00:00Z"
    }
  ]
}
```

### 4.2 구독 생성 (신규)

```http
POST /api/notifications/subscriptions
Content-Type: application/json

{
  "serviceId": "OA-2269",
  "filter": {
    "statuses": ["RECEIVING"],
    "areaNames": ["강남구"],
    "maxClassNames": ["문화행사"]
  },
  "channels": ["EMAIL"]
}
```

> **Warning:** v1 범위에서는 `channels` 에 항상 `["EMAIL"]` 만 전송합니다. SMS 를 포함하면 백엔드는 수락하지만 Knock 무료 등급으로 인해 실제 발송이 실패합니다.

**응답:** `201 Created`, 생성된 구독 객체. `409 Conflict` 시 이미 동일 `(user_id, service_id)` 구독 존재.

### 4.3 구독 수정 (신규)

```http
PATCH /api/notifications/subscriptions/{id}
Content-Type: application/json

{
  "filter": { "statuses": ["RECEIVING", "STANDBY"] },
  "channels": ["EMAIL", "SMS"]
}
```

**응답:** `200 OK`. `403 Forbidden` 시 다른 사용자의 구독.

### 4.4 구독 해지 (신규)

```http
DELETE /api/notifications/subscriptions/{id}
```

**응답:** `204 No Content`.

### 4.5 발송 이력 조회 (신규)

```http
GET /api/notifications/dispatches?cursor=<id>&size=20
```

**응답:**

```json
{
  "dispatches": [
    {
      "id": 1234,
      "subscriptionId": 12,
      "serviceName": "서울시 문화행사 공공서비스예약 정보",
      "title": "강남구 문화행사 접수가 시작되었습니다",
      "body": "...",
      "status": "SUCCESS",
      "sentAt": "2026-05-26T14:30:00Z"
    }
  ],
  "nextCursor": 1214
}
```

### 4.6 연락처 수정 (구현 완료, v1 비공개)

> **Note:** v1 범위에서는 호출하지 않습니다. SMS 활성화 시 사용.

```http
PATCH /api/users/me/contact
Content-Type: application/json

{ "phoneNumber": "+821012345678" }
```

**응답:** `200 OK`. 길이 20자 초과 시 `400 Bad Request`.

---

## 5. 필터 값 마스터 데이터

필터 옵션은 백엔드 enum 또는 데이터에서 유도되는 화이트리스트입니다.

### 5.1 `statuses` (서비스 상태)

| 값 | 한국어 표시 |
|---|---|
| `RECEIVING` | 접수중 |
| `STANDBY` | 접수대기 |
| `CLOSED` | 접수마감 |

### 5.2 `maxClassNames` (카테고리)

| 값 | 데이터셋 |
|---|---|
| `문화행사` | OA-2269 |
| `체육시설` | OA-2266 |
| `시설대관` | OA-2267 |
| `교육` | OA-2268 |
| `진료` | OA-2270 |

### 5.3 `areaNames` (지역)

서울시 25개 자치구 목록. `/api/areas` 같은 별도 엔드포인트로 제공받거나 프론트엔드에 하드코딩합니다. 백엔드 팀과 합의가 필요합니다.

---

## 6. 인증과 토큰 처리

`on-seoul` 인증은 OAuth2 로그인 후 JWT(Access + Refresh) 발급 방식입니다. 자세한 내용은 `on-seoul-api/docs/api-service-implementation.md`의 JWT 섹션을 참조합니다.

- Access Token: HTTP 헤더 `Authorization: Bearer <token>`
- Refresh Token: HttpOnly 쿠키 (자동 전송)
- 401 응답 수신 시 `POST /api/auth/refresh` 호출 후 재시도

---

## 7. 에러 응답 처리

백엔드는 `OnSeoulApiException` 기반의 표준 에러 응답을 반환합니다.

```json
{
  "error": "<error_code>",
  "message": "<사용자 표시 메시지>"
}
```

주요 에러 코드:

| 코드 | HTTP | 처리 방안 |
|---|---|---|
| `unauthorized` | 401 | 로그인 화면으로 리다이렉트 또는 refresh 시도 |
| `forbidden` | 403 | "권한이 없습니다" 토스트 |
| `not_found` | 404 | "구독을 찾을 수 없습니다" 표시 |
| `conflict` | 409 | "이미 구독 중인 서비스입니다" 표시 |
| `validation_error` | 400 | 폼 필드별 에러 메시지 표시 |
| `internal_error` | 500 | "잠시 후 다시 시도해 주세요" 토스트 |

---

## 8. UI/UX 가이드라인

- **채널 EMAIL 고정 (v1):** Knock 무료 등급으로 SMS 가 비활성. 구독 생성/수정 요청 시 `channels` 는 항상 `["EMAIL"]`. SMS 관련 UI(토글·전화번호 등록·발송 이력 채널 표시)는 모두 숨김 또는 "준비 중".
- **빈 필터 안내:** 필터가 모두 비어 있으면 "이 서비스의 모든 변경에 대해 알림을 받습니다"로 명시.
- **last_notified_at 표시:** 상대시간(예: "3일 전")과 절대시간을 함께 제공합니다.
- **낙관적 UI:** 토글이나 필터 변경은 즉시 UI에 반영하고, 실패 시 롤백 + 토스트로 안내합니다.
- **Knock 유료 전환 후 v2 작업:** SMS 토글 복원, 전화번호 등록 화면 노출, 발송 이력 채널 컬럼 표시, "마지막 채널 보호" 가드(최소 1개 필수) 도입.

---

## 9. 백엔드 협업 체크리스트

프론트엔드 작업을 시작하기 전에 백엔드 팀과 다음을 확인합니다.

- [x] 구독 CRUD 컨트롤러 4개 신규 구현 (`spring-backend` 에이전트 작업) — `NotificationSubscriptionController` (GET/POST/PATCH/DELETE)
- [x] 발송 이력 조회 컨트롤러 신규 구현 — `NotificationDispatchController` (cursor 기반 페이지네이션)
- [ ] 서비스 검색 API 경로 확정 (`collection` BC의 기존 API 재사용 여부)
- [ ] 지역(`areaNames`) 마스터 데이터 제공 방식 결정 (API vs 정적)
- [ ] 발송 이력 응답에 `serviceName` 포함 여부 (JOIN 필요) — 현재 백엔드 응답은 `subscriptionId` 만 포함. 프론트에서 별도 조회 또는 백엔드 JOIN 추가 결정 필요
- [x] 채널 최소 1개 검증을 백엔드에서도 강제 — `@NotEmpty` (POST) + `UpdateSubscriptionRequest.toCommand()` 가드 + DB `chk_ns_channels` 3중 방어

---

## 10. 참고 자료

- `on-seoul-api/docs/domain-refactoring-implementation.md` — Phase 2~6 notification BC 구현 현황
- `on-seoul-api/notification/README.md` — 도메인 모델 개요
- `on-seoul-api/schema/migration-scripts/04-create-tables-for-notification.sql` — DB 스키마 원본
- `on-seoul-api/docs/adr/0004-notification-orchestration.md` — 배치 모델 ADR
- `on-seoul-api/docs/knock-integration-guide.md` — Knock 발송 가이드 (참고용)

---

## Next steps

1. 백엔드 협업 체크리스트의 모든 항목을 `spring-backend` 에이전트와 함께 해결합니다.
2. API 계약이 확정되면 프론트엔드 코드 생성을 시작합니다.
3. 화면별 컴포넌트를 단계적으로 구현하고, 각 단계에서 백엔드 모킹 또는 실 API로 동작을 검증합니다.
