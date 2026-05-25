# API 서비스 도메인 리팩터링 구현 목록

ADR 기반 수직 BC 분리 및 알림 기능 신규 구현.
단일 브랜치 `refactor/domain-bc-split` 에서 진행한다.

아키텍처 결정의 전제는 `on-seoul-api/docs/adr/` 를 참조한다.

---

## Phase 1. 패키지 수직 분리

> 현재 구조(수평 레이어 최상위)를 BC 최상위 구조로 재편한다.
> 상세 결정은 `adr/README.md` (BC 4개 + 애그리거트 9개 전제) 참조.

- [x] 목표 패키지 레이아웃 확정
  ```
  dev.jazzybyte.onseoul.
    user/        (domain/, application/, port/, adapter/)
    chat/        (domain/, application/, port/, adapter/)
    collection/  (domain/, application/, port/, adapter/)
    notification/(domain/, application/, port/, adapter/)
  ```
- [x] `user` BC 이동 — `User`, `SocialLoginService`, `OAuth2LoginSuccessHandler`, 관련 Port
- [x] `chat` BC 이동 — `ChatRoom`, `ChatMessage`, `ChatStreamService`, `SendQueryService`, 관련 Port
- [x] `collection` BC 이동 — `PublicServiceReservation`, `ServiceChangeLog`, `CollectionHistory`, `ApiSourceCatalog`, 수집 서비스 전체, 관련 Port
- [x] `common/`, `bootstrap/` 은 BC 불포함 — 현 위치 유지
- [x] ArchUnit 헥사고날 의존성 규칙 갱신 (`HexagonalArchTest`)
- [x] BC 간 참조는 ID 전달로만 — 교차 BC 엔티티 직접 참조 제거 (`adr/README.md` 컨텍스트 간 참조 정책 참조)

검증: `./gradlew test` 전 구간 통과

---

## Phase 2. notification BC — DB 스키마

> 알림 구독 및 발송 테이블 신규 생성.
> 상세 결정은 `adr/0004-notification-orchestration.md` 참조.

- [x] `notification_subscriptions` 테이블 — `user_id`, `service_id`, `filter`(JSONB), `last_notified_at`, `channels`(JSONB — EMAIL/SMS 복수 선택)
- [x] `notification_dispatches` 테이블 — `subscription_id`, `change_log_id`, `status`(`PENDING`/`SUCCESS`/`FAILED`/`DEAD`), `attempt_count`, `sent_at`, `generated_title`, `generated_body`, `template_source`, `last_error`
- [x] UNIQUE 제약 — `(subscription_id, change_log_id)` (`adr/0004` 멱등성 보장 참조)
- [x] 마이그레이션 스크립트 작성 (`schema/migration-scripts/04-create-tables-for-notification.sql`)
- [x] H2 테스트 스키마(`jpa-test-schema.sql`) 동기화
- [x] `users.phone_number VARCHAR(20)` — SMS 수신용 전화번호 (사용자 직접 등록)

---

## Phase 3. notification BC — 도메인 · 포트 구현

> 상세 결정은 `adr/0001-context-communication.md`, `adr/0004-notification-orchestration.md` 참조.

- [x] `NotificationSubscription` 애그리거트 — `channels`(Set\<NotificationChannel\>), `filter`, `lastNotifiedAt`
- [x] `NotificationDispatch` 애그리거트 — `generated_title/body`, `template_source`, `markSuccess()`, `markFailed()`
- [x] `NotificationChannel` enum — EMAIL, SMS
- [x] Inbound Port — `CreateDefaultSubscriptionsUseCase` (ADR-0001 BC 간 동기 호출 인터페이스)
- [x] Outbound Port — `LoadSubscriptionPort`, `SaveSubscriptionPort`, `SaveDispatchPort`, `LoadDispatchPort`, `PushNotificationPort`, `TemplateGenerationPort`
- [x] JPA 엔티티 및 Repository

---

## Phase 4. user BC — 기본 구독 생성 연결

> OAuth 로그인 성공 시 기본 구독을 동기 직접 호출로 생성한다.
> 상세 결정은 `adr/0001-context-communication.md`, `adr/0002-domain-event-catalog.md` 참조.

- [x] `OAuth2LoginSuccessHandler` → `CreateDefaultSubscriptionsUseCase.create(userId)` 직접 호출
- [x] 호출은 JWT 발급 TX 밖(별도 TX 또는 TX 없음) — `adr/0003-consistency-and-transaction.md` 참조
- [x] `CreateDefaultSubscriptionsUseCase` 구현체 — 5개 데이터셋(OA-2266~2270) 기본 구독 INSERT (채널 기본값: EMAIL)
- [x] `TokenResponse`에 `userId` 추가 — 핸들러→구독 생성 연결 목적

---

## Phase 5. notification BC — 템플릿 어댑터 + 발송 어댑터 구현

> AI 서비스 `POST /notification/template` 호출 어댑터 및 Knock 발송 어댑터.
> 상세 결정은 `adr/0001-context-communication.md` (ACL 적용 대상), `adr/0004` 참조.

- [x] `TemplateAgentClient` (WebClient 기반, 10초 타임아웃) — `adapter/out/agent/`
- [x] `TemplateAgentDtoMapper` — ACL (FastAPI DTO ↔ 도메인)
- [x] AI 호출 실패 판정 및 fallback 처리 — non-2xx / 타임아웃 / 빈 title·body → `NotificationTemplate.render()`
- [x] `KnockNotificationAdapter` — `PushNotificationPort` 구현 (Knock REST API, SMS/이메일 채널별 워크플로우)
- [x] `user` BC: `PATCH /api/users/me/contact` — 전화번호 등록/수정 엔드포인트

---

## Phase 6. notification BC — 알림 스케줄러 구현

> 배치 잡 방식. 상태 머신 아님.
> 상세 결정은 `adr/0004-notification-orchestration.md` 전체, `adr/0003-consistency-and-transaction.md` 참조.

- [x] `NotificationScheduler` — `@Scheduled(fixedDelayString)` 기반, `NotificationTxHelper` TX 헬퍼 분리 (가상 스레드 프록시 호환)
- [x] 가상 스레드 풀 + `Semaphore(4)` 동시성 제어 — `adr/0004` 파라미터 참조
- [x] TX A — `ServiceChangeLog` 매칭(`LoadServiceChangePort`) + `NotificationDispatch` INSERT (`saveIfAbsent`, `REQUIRES_NEW`)
- [x] TX B — 푸시 성공 시 `status=SUCCESS` + `last_notified_at` 갱신 / 실패 시 `status=FAILED`, `attempt_count++` (`REQUIRES_NEW`)
- [x] `DEAD` 처리 — `attempt_count >= MAX_ATTEMPTS` 도달 시
- [x] Fallback 템플릿 (`NotificationTemplate.render`) — `TemplateAgentClient`가 non-2xx/타임아웃/빈 응답 시 자동 사용
- [x] Micrometer 운영 metrics 3종 등록 — `notification.template.source`, `notification.dispatch.dead`, `notification.dispatch.attempts`

---

## Phase 6-1. notification BC — 필터 기반 배치 발송 리팩터링

> Phase 6에서 구현한 **per-change Dispatch 모델**을 **`notification_batch` 기반 배치 모델**로 전환한다.
> 멱등성 1차 키는 `UNIQUE(batch_id, subscription_id)`, 2차 방어선은 `last_notified_at` 미갱신 정책.
> `collection_history`와 동일한 배치 추적 패턴을 알림 도메인에도 적용한다.
> 상세 결정은 `adr/0004-notification-orchestration.md` 참조.

**DB 스키마**

- [x] `notification_batch` 테이블 신규 생성
  ```sql
  CREATE TABLE notification_batch (
      id            BIGSERIAL PRIMARY KEY,
      started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
      finished_at   TIMESTAMPTZ,
      status        VARCHAR(20) NOT NULL DEFAULT 'RUNNING',  -- RUNNING / SUCCESS / FAILED
      sent_count    INT,
      failed_count  INT
  );
  ```
- [x] `notification_dispatch` 테이블 변경 — `change_log_id` 제거, `batch_id BIGINT NOT NULL REFERENCES notification_batch(id)` 추가, `attempt_count`/DEAD 관련 컬럼 정리, UNIQUE `(batch_id, subscription_id)` 재생성

**도메인 / 포트**

- [x] `NotificationBatch` 도메인 객체 — `id`, `startedAt`, `finishedAt`, `status`, `sentCount`, `failedCount`
- [x] `SaveBatchPort` / `LoadBatchPort` 인터페이스 추가
- [x] `SubscriptionFilter` 도메인 객체 — `filter` JSONB 역직렬화 → SQL WHERE 조건 생성 (카테고리·지역·상태)
- [x] `LoadServiceChangePort` 인터페이스 변경 — `loadSince(serviceId, Instant)` → `loadFiltered(SubscriptionFilter, Instant lastNotifiedAt)` (`service_change_log JOIN public_service_reservations`)
- [x] `NotificationTemplateRequest` 변경 — 단건 → `List<ChangeItem>` 배치 요청 (AI 서비스 `POST /notification/template` 스펙 반영)
- [x] `NotificationDispatch` 도메인 객체 변경 — `changeLogId` 필드 제거, `batchId` 추가

**어댑터 / 애플리케이션**

- [x] `NotificationBatchPersistenceAdapter` — `SaveBatchPort` / `LoadBatchPort` 구현 (JdbcTemplate)
- [x] `NotificationDispatchPersistenceAdapter` 변경 — `UNIQUE(batch_id, subscription_id)` 기반 `saveIfAbsent()`
- [x] `NotificationTxHelper` TX A — `batch_id` 파라미터 추가, `loadFiltered()` 호출, Dispatch INSERT ON CONFLICT DO NOTHING
- [x] `NotificationTxHelper` TX B — 발송 성공 시 `last_notified_at = batch.startedAt` 전진; 실패 시 `status=FAILED` + `last_error` 기록 (`last_notified_at` 미갱신)
- [x] `NotificationScheduler` 변경
  - 배치 시작 시 `notification_batch` INSERT (status=RUNNING)
  - per-change 루프 제거 → 구독별 배치 1회 처리
  - 배치 완료 시 `notification_batch` UPDATE (status, finished_at, sent_count, failed_count)

**테스트**

- [x] `NotificationTxHelperTest` — batch_id 파라미터, `batch.startedAt` 커서 전진 시나리오 반영
- [x] `NotificationSchedulerTest` — Batch INSERT/UPDATE 검증, sent_count/failed_count 집계 시나리오 추가

---

## Phase 6-2. notification BC — Knock Fallback 탄력성

> Knock 장애(연결 불가·타임아웃·5xx·서킷 오픈) 발생 시 대체 발송 수단으로 라우팅한다.
> `NotificationScheduler` / `NotificationTxHelper`는 변경 없이 동작한다 — `PushNotificationPort` 계약이 동일하게 유지된다.

**인터페이스 스텁 (완료)**

- [x] `FallbackReason` enum — `KNOCK_UNAVAILABLE / KNOCK_TIMEOUT / KNOCK_CIRCUIT_OPEN / KNOCK_SERVER_ERROR / NO_CONTACT`
- [x] `FallbackNotificationPort` — fallback 아웃바운드 포트 인터페이스
- [x] `ResilientPushNotificationAdapter` — `@Primary` 데코레이터. Knock 호출 → `RuntimeException` catch → `FallbackNotificationPort` 라우팅. `notification.push.fallback{reason}` metric 기록
- [x] `LogOnlyFallbackNotificationAdapter` — `@ConditionalOnMissingBean` 기본 스텁. 로그·메트릭만 기록, 실 발송 없음. 실 구현체 등록 시 자동 교체

**미구현 (Phase 6-2 본 작업)**

- [ ] `FallbackReason` 분류 고도화 — `classifyReason()`에서 예외 타입·HTTP 상태코드로 세분화
- [ ] Resilience4j `CircuitBreaker` 적용 — Knock 연속 실패 시 fast-fail + `KNOCK_CIRCUIT_OPEN` 트리거
  - 의존성 추가: `resilience4j-spring-boot3`
  - `CircuitBreakerConfig`: `slidingWindowSize=10`, `failureRateThreshold=50`, `waitDurationInOpenState=60s`
- [ ] `SmtpFallbackNotificationAdapter` — JavaMailSender 직접 SMTP 발송 (EMAIL 채널 전용)
  - `FallbackNotificationPort` 구현, `@ConditionalOnProperty(name="notification.fallback.smtp.enabled")`
  - `dispatchId`를 `X-Dispatch-Id` 헤더 또는 제목에 포함 (idempotency)
  - SMS 채널은 로그만 (Twilio 직접 연동은 별도 검토)
- [ ] `InAppFallbackNotificationAdapter` (선택) — `notification_outbox` 테이블에 미발송 알림 저장
  - 프론트엔드가 폴링 또는 SSE로 읽어가는 구조 (별도 API 설계 필요)
- [ ] `ResilientPushNotificationAdapter` 테스트 — Knock 성공/실패/timeout 시나리오, fallback 호출 여부, metric 등록
- [ ] `LogOnlyFallbackNotificationAdapter` 교체 후 기존 Knock 테스트 와이어링 재확인

**설계 원칙**
- `FallbackNotificationPort`는 **절대 예외를 던지지 않는다** — 발송 실패 시 로그·metric에 기록하고 종료. Scheduler는 `last_notified_at` 미갱신으로 다음 배치에서 재시도.
- SMTP fallback도 실패하면 `notification_dispatch.status = FAILED`로 기록 (TxHelper가 처리).

---

## Phase 7. Embeddings 갱신 워커

> ChangeLog 커밋 후 AI 서비스에 임베딩 갱신을 위임한다. 임베딩 생성과 pgvector 적재는 AI 서비스(`on-seoul-agent`) 책임 — API 서비스는 REST API 호출만 한다.
> 상세 결정은 `adr/0003-consistency-and-transaction.md` (≤5분 SLA) 참조.

**AI 서비스 호출 스펙: `POST /embeddings/services/sync`**

```json
// Request
{ "upsert": ["S240101A001"], "delete": ["S230501Z099"] }

// Response 202
{ "accepted": { "upsert": 1, "delete": 1 } }
```

- `upsert`: 신규/변경 service_id 목록. `delete`: 삭제된 service_id 목록.
- `len(upsert) + len(delete) ≤ 500` — 초과 시 청크 분할 전송.
- 둘 다 빈 배열이면 422 → 호출 전 가드 필요.

---

- [ ] `EmbeddingSyncQueue` — in-memory 큐 또는 `ScheduledExecutor`
- [ ] 수집 TX 커밋 직후 변경 유형별로 enqueue — 신규/변경 `service_id`는 `upsert`, 삭제된 `service_id`는 `delete`로 분류
- [ ] 워커 — `on-seoul-agent` `POST /embeddings/services/sync` 호출 (WebClient, 빈 배열 가드 + 500건 초과 시 청크 분할). 실패 시 로그 + 다음 tick 재시도
- [ ] Micrometer 게이지 `embeddings.sync.lag.seconds` 등록 — `adr/0003` 감시 요건 참조

---

## Phase 8. 테스트

- [ ] `NotificationSubscription` / `NotificationDispatch` 도메인 단위 테스트
- [ ] `NotificationScheduler` — TX A / TX B 분리, `ON CONFLICT DO NOTHING` 멱등성, `last_notified_at` 갱신 시점, DEAD 전환
- [ ] `TemplateAgentClient` — AI 호출 성공 / non-2xx / 타임아웃 / 빈 title·body → fallback
- [ ] `CreateDefaultSubscriptionsUseCase` 연결 통합 테스트
- [ ] ArchUnit — BC 간 직접 엔티티 참조 부재 검증
- [ ] `./gradlew test` 전 구간 통과

---

## 참고

- 모든 결정 근거는 `on-seoul-api/docs/adr/` 에 있다. ADR을 벗어나는 구현이 필요하면 구현 전에 확인한다.
- BC 간 참조는 ID 전달만 허용 (`adr/README.md` 컨텍스트 간 참조 정책).
- 도메인 이벤트 및 메시지 브로커는 도입하지 않는다. 외부 시스템 통신은 REST API(WebClient) 직접 호출 (`adr/0002-domain-event-catalog.md`).
- 알림 스케줄러 파라미터(동시성, 타임아웃, MAX_ATTEMPTS, 백오프)는 `adr/0004` 파라미터 표 준수.
- 테스트 케이스는 최대한 유지하며, BC 이동 및 리팩토링으로 인한 변경 사항을 함께 반영한다. 신규 기능은 충분한 단위 테스트와 통합 테스트를 작성한다.