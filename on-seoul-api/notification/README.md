# notification BC

`on-seoul-api` 알림 바운디드 컨텍스트.
서비스 상태 변경을 감지하여 구독자에게 SMS 또는 이메일 알림을 발송한다.

---

## 역할

| 역할 | 설명 |
|---|---|
| 기본 구독 생성 | OAuth2 로그인 성공 시 5개 데이터셋에 대한 기본 구독을 자동 생성 |
| 알림 스케줄링 | `ServiceChangeLog`를 주기적으로 조회해 구독 조건에 매칭된 변경에 대해 발송 처리 (Phase 6) |
| 템플릿 생성 | AI 서비스(`POST /notification/template`)로 자연어 메시지를 생성하고, 실패 시 정형 fallback 사용 |
| 알림 발송 | Knock을 통해 SMS/이메일 채널별 발송 |
| 발송 이력 | `NotificationDispatch`로 발송 시도·결과를 추적. 멱등 재시도 지원 |

---

## 모듈 구조

```
notification/
├── domain/
│   ├── NotificationSubscription.java   # 구독 애그리거트 루트
│   ├── NotificationDispatch.java        # 발송 단위 애그리거트 루트
│   ├── NotificationChannel.java         # enum: EMAIL | SMS
│   ├── DispatchStatus.java              # enum: PENDING | SUCCESS | FAILED | DEAD
│   ├── TemplateSource.java              # enum: AI | FALLBACK
│   ├── NotificationTemplate.java        # fallback 정형 치환 (static render)
│   ├── NotificationTemplateRequest.java # 템플릿 생성 요청 VO
│   └── TemplateResult.java              # 템플릿 생성 결과 VO (title, body, source)
│
├── port/
│   ├── in/
│   │   └── CreateDefaultSubscriptionsUseCase.java  # 기본 구독 생성 (user BC → 직접 호출)
│   └── out/
│       ├── LoadSubscriptionPort.java    # 구독 전체 조회 (스케줄러용)
│       ├── SaveSubscriptionPort.java    # 구독 저장 + saveIfAbsent
│       ├── LoadDispatchPort.java        # dispatch 조회 (재시도 목록, DEAD 가드)
│       ├── SaveDispatchPort.java        # dispatch 저장 + saveIfAbsent
│       ├── TemplateGenerationPort.java  # AI 템플릿 생성 호출
│       └── PushNotificationPort.java    # 알림 발송 (채널별)
│
├── application/
│   ├── NotificationScheduler.java              # 메인 발송 스케줄러 (fixedDelay 5분, 가상 스레드 풀)
│   ├── DispatchRetryScheduler.java             # FAILED dispatch 재시도 스케줄러 (fixedDelay 1시간)
│   ├── NotificationTxHelper.java               # 트랜잭션 분리 헬퍼 (TX A / TX B / Retry TX)
│   ├── CreateDefaultSubscriptionsService.java  # 기본 구독 생성 구현체
│   ├── NotificationSubscriptionService.java    # 구독 CRUD use case
│   └── NotificationDispatchService.java        # 발송 이력 조회 use case
│
└── adapter/
    ├── in/
    │   └── web/        # NotificationSubscriptionController, NotificationDispatchController
    └── out/
        ├── agent/       # TemplateAgentClient — FastAPI POST /notification/template
        ├── knock/       # KnockNotificationAdapter — Knock REST API (SMS/이메일)
        └── persistence/ # JPA 어댑터 — notification_subscriptions, notification_dispatches
```

---

## REST 엔드포인트 (개인화 알림 관리)

JWT 인증 필수. `JwtAuthenticationFilter` 가 `userId` request attribute 를 세팅.

| Method | Path | 설명 | 성공/실패 |
|---|---|---|---|
| GET    | `/api/notifications/subscriptions`      | 내 구독 목록 | 200 / 401 |
| POST   | `/api/notifications/subscriptions`      | 구독 생성    | 201 / 400, 409 |
| PATCH  | `/api/notifications/subscriptions/{id}` | 구독 수정 (filter/channels 부분 업데이트) | 200 / 400, 403, 404 |
| DELETE | `/api/notifications/subscriptions/{id}` | 구독 해지 | 204 / 403, 404 |
| GET    | `/api/notifications/dispatches?cursor=&size=` | 발송 이력 (cursor 기반, size 기본 20 / 최대 100) | 200 / 401 |

요청·응답 스키마는 `docs/superpowers/plans/2026-05-28-frontend-personalized-notification.md` 4장 참조.

---

## 도메인 모델

### NotificationSubscription

사용자가 특정 서비스에 대해 알림을 받겠다고 등록한 구독 단위.

| 필드 | 타입 | 설명 |
|---|---|---|
| `userId` | `Long` | 구독 사용자 (user BC ID 참조) |
| `serviceId` | `String` | 대상 서비스 (OA-2266~OA-2270, collection BC 자연키 참조) |
| `filter` | `String` (JSONB) | 알림 필터 조건. `{}` = 모든 변경에 알림 |
| `channels` | `Set<NotificationChannel>` | 수신 채널. EMAIL, SMS 복수 선택 가능 |
| `lastNotifiedAt` | `Instant` | 마지막 발송 성공 시각. NULL = 미발송. 스케줄러가 이 값 이후만 조회 |

**불변 조건:** `(user_id, service_id)` 조합 유일 — 중복 구독 방지.

### NotificationBatch

스케줄러 tick 1회 = 배치 실행 단위. 전체 구독 처리 결과를 집계한다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `startedAt` | `Instant` | 배치 시작 시각. `txBSuccess`가 `last_notified_at` 커서를 이 값으로 전진 |
| `finishedAt` | `Instant` | 배치 종료 시각 |
| `status` | `BatchStatus` | RUNNING → SUCCESS \| FAILED |
| `sentCount` | `int` | 발송 성공 구독 수 |
| `failedCount` | `int` | 발송 실패 구독 수 |

**상태 머신:** `RUNNING` → `complete()` → `SUCCESS` \| `fail()` → `FAILED`.

### NotificationDispatch

배치 실행 1건 × 구독 1건 = 발송 시도 단위 (ADR-0004 per-batch 모델).

| 필드 | 타입 | 설명 |
|---|---|---|
| `batchId` | `Long` | 소속 배치 실행 (`notification_batch.id` 참조) |
| `subscriptionId` | `Long` | 발송 트리거 구독 |
| `status` | `DispatchStatus` | PENDING → SUCCESS \| FAILED → (retry) → DEAD |
| `attemptCount` | `int` | `DispatchRetryScheduler`가 시도한 횟수. `MAX_ATTEMPTS(5)` 도달 시 DEAD 전환 |
| `templateSource` | `TemplateSource` | AI \| FALLBACK |
| `generatedTitle/Body` | `String` | 생성된 메시지 내용. 발송 실패 시에도 저장 (retry 재사용) |

**불변 조건:** `UNIQUE(batch_id, subscription_id)` — 같은 배치에서 동일 구독에 중복 발송 방지.  
**재시도 메커니즘:** 발송 실패 시 `last_notified_at` 미갱신 → `DispatchRetryScheduler`가 `generated_title/body`를 재사용해 AI 호출 없이 1시간 주기로 추가 재시도.  
**영구 실패 종료:** `attemptCount >= 5` → `markDead()` 전환. 이후 메인 배치가 DEAD 가드(`existsDeadDispatchBySubscriptionId`)로 해당 구독을 건너뛰어 row 무한 누적을 방지한다.

---

## 발송 흐름

ADR-0004 기반 배치 잡 방식. 상태 머신 아님.

### 메인 발송 배치 (`NotificationScheduler` — fixedDelay 5분)

```
[배치 시작]
  NotificationBatch INSERT (RUNNING)

[구독별 처리 — 가상 스레드 풀, 동시 4건]
  TX A:
    DEAD 가드: subscriptionId에 DEAD dispatch 존재 시 → 발송 skip (row 무한 누적 방지)
    changes ← ServiceChangeLog WHERE changed_at ∈ (sub.last_notified_at, batch.startedAt]  (SubscriptionFilter 적용)
    변경 없으면 skip
    Dispatch INSERT (PENDING) UNIQUE(batch_id, subscription_id) — 중복 배치 멱등 처리

  TX 밖:
    template ← TemplateAgentClient.generate()  또는  NotificationTemplate.render() (AI 실패 fallback)
    recipient ← UserContactPort.loadContact()
    KnockNotificationAdapter.send(recipient, title, body, dispatchId, channels)
      └─ ResilientPushNotificationAdapter가 래핑 → 실패 시 LogOnlyFallbackNotificationAdapter로 라우팅

  TX B (결과별):
    성공: Dispatch → SUCCESS + generatedTitle/Body 저장, Subscription.last_notified_at = batch.startedAt
    실패: Dispatch → FAILED + generatedTitle/Body 저장 (retry 재사용 목적), last_notified_at 미갱신

[배치 종료]
  NotificationBatch UPDATE (COMPLETED|FAILED, sent_count, failed_count)
```

### 재시도 배치 (`DispatchRetryScheduler` — fixedDelay 1시간)

```
retryable ← FAILED dispatch WHERE generated_title IS NOT NULL
                                AND attempt_count < 5
                                AND updated_at < now - 10분  (메인 배치 레이스 방지)
                                AND 구독별 최신 1건

for each dispatch:
  sub ← loadSubscriptionPort.loadById()   // 구독 삭제됐으면 skip
  recipient ← UserContactPort.loadContact()
  KnockNotificationAdapter.send(기존 title, 기존 body, ...)   // AI 호출 없음

  성공: Dispatch → SUCCESS, Subscription.last_notified_at = retryStartedAt
  실패: attempt_count++
        attempt_count >= 5 → Dispatch → DEAD (last_notified_at 미갱신)
```

**핵심 보장:**
- `last_notified_at`은 **푸시 성공 시에만** 전진 → JVM 크래시 후 자동 복구
- `changedAtBefore = batch.startedAt` → 쿼리 시점 이후 변경은 다음 배치로 미뤄 중복 발송 방지
- `UNIQUE(batch_id, subscription_id)` → 같은 배치 내 중복 발송 차단
- DEAD 가드 → `attemptCount ≥ 5` 이후 메인 배치도 발송 중단하여 row 무한 누적 방지
- `generated_title/body`를 FAILED 시에도 저장 → retry 스케줄러가 AI 없이 재사용
- `dispatch.id`를 Knock 워크플로우 트리거 식별자로 전달

---

## 어댑터

### TemplateAgentClient (`adapter/out/agent`)

FastAPI AI 서비스(`POST /notification/template`)를 호출해 자연어 알림 메시지를 생성한다.

| 항목 | 값 |
|---|---|
| 타임아웃 | `ai.service.template-timeout-seconds` (기본 10초) |
| fallback 조건 | HTTP non-2xx / 타임아웃 / 응답 title·body 빈 문자열 |
| fallback 구현 | `NotificationTemplate.render()` — `"[서울공공서비스] {serviceId} 변경 알림"` 형식 |
| ACL | `TemplateAgentDtoMapper` — FastAPI DTO ↔ 도메인 변환 |

### KnockNotificationAdapter (`adapter/out/knock`)

[Knock](https://knock.app) REST API를 통해 SMS/이메일을 채널별로 발송한다.

| 항목 | 값 |
|---|---|
| 인증 | `knock.api-key` (Authorization 헤더, ExchangeFilterFunction으로 마스킹) |
| 이메일 워크플로우 | `knock.email-workflow-key` (기본: `service-change-email`) |
| SMS 워크플로우 | `knock.sms-workflow-key` (기본: `service-change-sms`) |
| 타임아웃 | `knock.timeout-seconds` (기본 10초) |
| 채널 처리 | `channels`에 EMAIL 포함 시 이메일 워크플로우 트리거, SMS 포함 시 SMS 워크플로우 트리거 |
| 실패 처리 | 채널별 예외를 `KnockDispatchException(FallbackReason, cause)`으로 분류하여 throw. `ResilientPushNotificationAdapter`가 `instanceof` 분기로 `FallbackReason`을 추출해 fallback 라우팅 |

### NotificationSubscriptionPersistenceAdapter (`adapter/out/persistence`)

`saveIfAbsent()` — `JdbcTemplate` + `DataIntegrityViolationException` catch 방식으로 `ON CONFLICT DO NOTHING` 의미론 구현 (H2/PostgreSQL 공통 호환).

---

## 설정 (`application.yml`)

```yaml
ai:
  service:
    url: "http://localhost:8000"      # FastAPI AI 서비스 base URL
    template-timeout-seconds: 10      # AI 템플릿 호출 타임아웃 (ADR-0004)

knock:
  api-key: ""                         # Knock API 키 (필수)
  email-workflow-key: "service-change-email"
  sms-workflow-key: "service-change-sms"
  timeout-seconds: 10                 # Knock API 호출 타임아웃

notification:
  scheduler:
    fixed-delay-ms: 300000            # 메인 발송 배치 주기 (기본 5분)
  retry-scheduler:
    fixed-delay-ms: 3600000           # FAILED dispatch 재시도 주기 (기본 1시간)
```

---

## BC 간 의존 관계

```
user BC
  └─ OAuth2LoginSuccessHandler
       └─ [동기 직접 호출] CreateDefaultSubscriptionsUseCase  ← notification BC
                                                              (ADR-0001)

notification BC
  └─ adapter/out/agent  ──▶  FastAPI AI 서비스 (외부, ACL 적용)
  └─ adapter/out/knock  ──▶  Knock (외부, SMS/이메일)
  └─ adapter/out/persistence ──▶ PostgreSQL (on_data)
```

BC 간 참조는 ID만 전달 (`userId: Long`, `serviceId: String`).
`user`, `collection` 도메인 객체를 직접 import 하지 않는다.

---

## 관련 문서

- [ADR-0001 — 컨텍스트 간 통신 방식](../docs/adr/0001-context-communication.md)
- [ADR-0004 — 알림 발송 흐름 오케스트레이션](../docs/adr/0004-notification-orchestration.md)
- [도메인 모델](../../docs/domain-model.md)
- [구현 목록](../docs/domain-refactoring-implementation.md)
