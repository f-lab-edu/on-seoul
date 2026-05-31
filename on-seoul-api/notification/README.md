# notification BC

`on-seoul-api` 알림 바운디드 컨텍스트.
서비스 상태 변경을 감지하여 구독자에게 SMS 또는 이메일 알림을 발송한다.

---

## 역할

| 역할 | 설명 |
|---|---|
| 구독 관리 | 사용자가 직접 구독을 생성·수정·삭제 (opt-in 모델). 신규 사용자는 구독 0개로 시작 |
| 알림 스케줄링 | 수집 완료 이벤트(`CollectionCompletedEvent`) 수신 시 1회 실행. 조건에 매칭된 변경에 대해 발송 처리 |
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
│   ├── NotificationBatch.java           # 배치 실행 단위 (ADR-0004)
│   ├── NotificationChannel.java         # enum: EMAIL | SMS
│   ├── DispatchStatus.java              # enum: PENDING | SUCCESS | FAILED | DEAD
│   ├── TemplateSource.java              # enum: AI | FALLBACK
│   ├── NotificationTemplate.java        # fallback 정형 치환 (static render)
│   ├── NotificationTemplateRequest.java # 템플릿 생성 요청 VO
│   ├── TemplateResult.java              # 템플릿 생성 결과 VO (title, body, source)
│   ├── SubscriptionFilter.java          # 알림 필터 값 객체 (statuses/areaNames/maxClassNames/keywords/keywordTargets)
│   ├── KeywordTarget.java               # enum: SERVICE_NAME | PLACE_NAME (키워드 매칭 대상 컬럼)
│   └── FallbackReason.java              # enum: KNOCK_UNAVAILABLE | KNOCK_TIMEOUT | KNOCK_CIRCUIT_OPEN | KNOCK_SERVER_ERROR | NO_CONTACT
│
├── port/
│   ├── in/
│   │   └── (CreateSubscription/UpdateSubscription/DeleteSubscription/ListSubscriptions/ListDispatches)
│   └── out/
│       ├── LoadSubscriptionPort.java    # 구독 조회 (스케줄러용 청크 페이지네이션 포함)
│       ├── SaveSubscriptionPort.java    # 구독 저장 + saveIfAbsent
│       ├── LoadDispatchPort.java        # dispatch 조회 (재시도 목록, DEAD 가드)
│       ├── SaveDispatchPort.java        # dispatch 저장 + saveIfAbsent
│       ├── LoadBatchPort.java           # 배치 이력 조회 (stale RUNNING 회수)
│       ├── SaveBatchPort.java           # 배치 저장
│       ├── TemplateGenerationPort.java  # AI 템플릿 생성 호출
│       ├── PushNotificationPort.java    # 알림 발송 (채널별)
│       └── FallbackNotificationPort.java # Knock 장애 시 대체 발송 포트
│
├── application/
│   ├── NotificationScheduler.java              # 메인 발송 스케줄러 (CollectionCompletedEvent 기반, 가상 스레드 풀)
│   ├── DispatchRetryScheduler.java             # FAILED dispatch 재시도 스케줄러 (fixedDelay 1시간)
│   ├── NotificationTxHelper.java               # 트랜잭션 분리 헬퍼 (TX A / TX B / Retry TX)
│   ├── NotificationSubscriptionService.java    # 구독 CRUD use case
│   └── NotificationDispatchService.java        # 발송 이력 조회 use case
│
└── adapter/
    ├── in/
    │   └── web/        # NotificationSubscriptionController, NotificationDispatchController
    └── out/
        ├── agent/       # TemplateAgentClient — FastAPI POST /notification/template
        ├── knock/       # KnockNotificationAdapter, ResilientPushNotificationAdapter (CircuitBreaker + fallback), LogOnlyFallbackNotificationAdapter
        └── persistence/ # JPA 어댑터 — notification_subscriptions, notification_dispatches, notification_batch
```

---

## REST 엔드포인트 (개인화 알림 관리)

JWT 인증 필수. `JwtAuthenticationFilter` 가 `userId` request attribute 를 세팅.

| Method | Path | 설명 | 성공/실패 |
|---|---|---|---|
| GET    | `/api/notifications/subscriptions`      | 내 구독 목록 | 200 / 401 |
| POST   | `/api/notifications/subscriptions`      | 구독 생성    | 201 / 400 |
| PATCH  | `/api/notifications/subscriptions/{id}` | 구독 수정 (filter/channels 부분 업데이트) | 200 / 400, 403, 404 |
| DELETE | `/api/notifications/subscriptions/{id}` | 구독 해지 | 204 / 403, 404 |
| GET    | `/api/notifications/dispatches?cursor=&size=` | 발송 이력 (cursor 기반, size 기본 20 / 최대 100) | 200 / 401 |

요청·응답 스키마는 `on-seoul-front/docs/2026-05-28-frontend-personalized-notification.md` 4장 참조.

---

## 도메인 모델

### NotificationSubscription

사용자가 알림 조건을 등록한 구독 단위 (opt-in, serviceId 고정 없는 조건 기반).

| 필드 | 타입 | 설명 |
|---|---|---|
| `userId` | `Long` | 구독 사용자 (user BC ID 참조) |
| `filter` | `SubscriptionFilter` (JSONB) | 알림 필터 조건. 최소 1개 조건 필수 |
| `channels` | `Set<NotificationChannel>` | 수신 채널. EMAIL, SMS 복수 선택 가능 |
| `lastNotifiedAt` | `Instant` | 마지막 발송 성공 시각. NULL = 미발송. 스케줄러가 이 값 이후만 조회 |

### SubscriptionFilter

| 필드 | 설명 |
|---|---|
| `statuses` | `service_status` 화이트리스트 (빈 집합 = 조건 미적용) |
| `areaNames` | `area_name` 화이트리스트 |
| `maxClassNames` | `max_class_name` 화이트리스트 (카테고리) |
| `keywords` | 키워드 부분일치 목록. 최대 3개, `keywordTargets` 대상 컬럼과 OR 결합 |
| `keywordTargets` | 키워드 매칭 대상 컬럼. `SERVICE_NAME` \| `PLACE_NAME`. 미지정 시 양쪽 모두 적용 |

**불변 조건:** `statuses / areaNames / maxClassNames / keywords` 중 최소 1개 비어있지 않아야 한다 (`keywordTargets`만으로는 빈 구독).

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

### 메인 발송 배치 (`NotificationScheduler` — `CollectionCompletedEvent` 수신 시 1회)

수집 스케줄러(`CollectionScheduler`, 매일 08:00)가 `collectAll()` 완료 후 이벤트를 발행하면
`@Async @EventListener`로 비동기 기동한다. 수집 중 일부 소스 실패 시에도 이벤트는 발행된다.

```
[배치 시작]
  NotificationBatch INSERT (RUNNING)

[구독별 처리 — 가상 스레드 풀, 동시 4건, keyset 페이지네이션 100건 청크]
  TX A:
    DEAD 가드: subscriptionId에 DEAD dispatch 존재 시 → 발송 skip
    changes ← ServiceChangeLog WHERE changed_at ∈ (sub.last_notified_at, batch.startedAt]  (SubscriptionFilter 적용)
    변경 없으면 skip
    Dispatch INSERT (PENDING) UNIQUE(batch_id, subscription_id) — 중복 배치 멱등 처리

  TX 밖:
    template ← TemplateAgentClient.generate()  또는  NotificationTemplate.render() (AI 실패 fallback)
    recipient ← UserContactPort.loadContact()
    ResilientPushNotificationAdapter.send()
      ├─ CircuitBreaker("knock") — OPEN 시 CallNotPermittedException fast-fail
      └─ KnockNotificationAdapter.send()
           실패 시: FallbackReason 분류 → metric 기록 → LogOnlyFallbackNotificationAdapter
                    → 원본 예외 rethrow → TX B 실패 경로 진입

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

### KnockNotificationAdapter + ResilientPushNotificationAdapter (`adapter/out/knock`)

[Knock](https://knock.app) REST API를 통해 SMS/이메일을 채널별로 발송한다.

**ResilientPushNotificationAdapter** — `PushNotificationPort`의 `@Primary` 구현체. 두 탄력성 레이어를 제공한다:

| 레이어 | 동작 |
|---|---|
| Resilience4j CircuitBreaker(`knock`) | OPEN 상태에서 `CallNotPermittedException` fast-fail. `KNOCK_CIRCUIT_OPEN`으로 분류 |
| Fallback 라우팅 | Knock 실패 시 `FallbackReason` 분류 → metric 기록 → `FallbackNotificationPort` 호출 → **원본 예외 rethrow** |

> **예외 rethrow 이유:** fallback이 `LogOnly`인 현재, 예외를 삼키면 스케줄러가 발송 성공으로 오인해
> `txBSuccess`를 호출한다. rethrow로 `txBFailure` → `FAILED` → retry 경로가 정상 동작하도록 보장.
> 추후 OneSignal fallback 구현 시 발송 성공/실패 여부에 따른 rethrow 정책 재검토 필요.

**KnockNotificationAdapter** — 실제 Knock REST API 호출 (`@Qualifier("knockPrimary")`).

| 항목 | 값 |
|---|---|
| 인증 | `knock.api-key` (Authorization 헤더) |
| 이메일 워크플로우 | `knock.email-workflow-key` (기본: `service-change-email`) |
| SMS 워크플로우 | `knock.sms-workflow-key` (기본: `service-change-sms`) |
| 타임아웃 | `knock.timeout-seconds` (기본 10초) |
| 예외 변환 | `TimeoutException` → `KNOCK_TIMEOUT`, 5xx → `KNOCK_SERVER_ERROR`, 그 외 → `KNOCK_UNAVAILABLE` |

**LogOnlyFallbackNotificationAdapter** — `FallbackNotificationPort` 기본 스텁. 로그·메트릭만 기록, 실 발송 없음.
실 구현체(`OneSignalFallbackNotificationAdapter`, TODO) 등록 시 `@ConditionalOnMissingBean`으로 자동 교체.

### NotificationSubscriptionPersistenceAdapter (`adapter/out/persistence`)

`saveIfAbsent()` — `JdbcTemplate` + `DataIntegrityViolationException` catch 방식으로 `ON CONFLICT DO NOTHING` 의미론 구현 (H2/PostgreSQL 공통 호환).

---

## 설정 (`application.yml`)

```yaml
ai:
  service:
    url: "http://localhost:8000"
    template-timeout-seconds: 10

knock:
  api-key: ""                           # 필수
  email-workflow-key: "service-change-email"
  sms-workflow-key: "service-change-sms"
  timeout-seconds: 10

notification:
  scheduler:
    stale-threshold-ms: 600000          # stale RUNNING batch 회수 임계값 (기본 10분)
    # fixed-delay-ms 는 더 이상 사용되지 않음 — CollectionCompletedEvent 기반으로 전환됨
  retry-scheduler:
    fixed-delay-ms: 3600000             # FAILED dispatch 재시도 주기 (기본 1시간)

resilience4j:
  circuitbreaker:
    instances:
      knock:
        sliding-window-size: 10
        failure-rate-threshold: 50      # 10회 중 5회 실패 시 OPEN
        wait-duration-in-open-state: 60s
        permitted-number-of-calls-in-half-open-state: 3
```

---

## BC 간 의존 관계

```
common BC
  └─ CollectionCompletedEvent  ──▶  notification BC (이벤트 구독)

collection BC
  └─ CollectionScheduler  ──(publishEvent)──▶  CollectionCompletedEvent

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
