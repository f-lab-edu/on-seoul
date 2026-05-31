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
- [x] Outbound Port — `LoadSubscriptionPort`, `SaveSubscriptionPort`, `SaveDispatchPort`, `LoadDispatchPort`, `PushNotificationPort`, `TemplateGenerationPort`
- [x] JPA 엔티티 및 Repository

---

## Phase 4. user BC — 토큰 발급 (구독은 opt-in)

> OAuth 로그인 성공 시 토큰 발급 + 콜백 리다이렉트만 수행한다.
> 알림 구독은 opt-in 모델로, 신규 사용자는 구독 0개로 시작하고 이후 직접 구독한다.
> user BC는 더 이상 notification BC에 의존하지 않는다(기본 구독 자동 생성 제거).

- [x] `OAuth2LoginSuccessHandler` — 소셜 로그인 처리 후 토큰 쿠키 발급 + 콜백 리다이렉트
- [x] `TokenResponse`에 `userId` 포함

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

**구현 완료**

- [x] `FallbackReason` 분류 고도화 — `KnockNotificationAdapter.classifyException()`에서 `TimeoutException` → `KNOCK_TIMEOUT`, 5xx → `KNOCK_SERVER_ERROR`, 그 외 → `KNOCK_UNAVAILABLE` 분류. `KnockDispatchException`이 `FallbackReason`을 구조화 필드로 보유하여 `ResilientPushNotificationAdapter`가 문자열 매칭 없이 추출
- [x] `ResilientPushNotificationAdapter` 테스트 — 성공/실패→fallback 호출/KNOCK_TIMEOUT·KNOCK_SERVER_ERROR 분류/KNOCK_UNAVAILABLE 폴백/metric 등록 6개 시나리오

**잔여 구현**

- [ ] **`ResilientPushNotificationAdapter` 예외 삼킴 버그 수정** — 현재 `LogOnly.sendFallback()`이 예외를 삼켜 스케줄러가 발송 성공으로 오인 → `txBSuccess()` 호출 → dispatch `SUCCESS` + `last_notified_at` 전진. Knock 실패 후 fallback 호출 완료 시 rethrow 추가 필요 (retry 정상 동작 보장)
- [ ] Resilience4j `CircuitBreaker` 적용 — Knock 연속 실패 시 fast-fail + `KNOCK_CIRCUIT_OPEN` 트리거
  - 의존성 추가: `resilience4j-spring-boot3`
  - `CircuitBreakerConfig`: `slidingWindowSize=10`, `minimumNumberOfCalls=10`(기본 100 → 윈도우에 맞춰 명시, 미설정 시 서킷 미작동), `failureRateThreshold=50`, `waitDurationInOpenState=60s`
  - `ResilientPushNotificationAdapter`에 `@CircuitBreaker` 적용, open 상태에서 `CallNotPermittedException` → `KNOCK_CIRCUIT_OPEN` 분류
- [ ] `OneSignalFallbackNotificationAdapter` — `FallbackNotificationPort` OneSignal REST API 구현체
  - `@ConditionalOnProperty(name="notification.fallback.onesignal.enabled")`
  - OneSignal `POST /api/v1/notifications` 호출 (EMAIL/SMS 채널 모두 커버)
  - `dispatchId`를 `external_id`로 전달 (멱등성 보장)
  - `LogOnlyFallbackNotificationAdapter` 교체 대상
- [ ] `OneSignalFallbackNotificationAdapter` 구현 후 `LogOnlyFallbackNotificationAdapter` 와이어링 재확인 및 테스트 추가

**설계 원칙**
- `FallbackNotificationPort`는 **예외를 던지지 않는다** — 발송 실패 시 로그·metric에 기록하고 종료. Knock 실패 시 rethrow는 `ResilientPushNotificationAdapter`가 담당하며, fallback 자체 실패는 `DispatchRetryScheduler`(1시간 주기, 최대 5회)의 재시도가 커버한다.
- fallback이 `LogOnly`인 동안은 Knock 실패 후 rethrow → `txBFailure` → retry 경로가 유일한 복원 수단이다.

---

## Phase 6-3. notification BC — 구독 모델 개선 및 트리거 전환

> serviceId 고정 구독을 조건 기반 구독으로 전환하고, 알림 발송 트리거를 이벤트 구동 방식으로 교체한다.

**구독 모델 개선**

- [x] `notification_subscriptions.service_id` 컬럼 제거 — `UNIQUE(user_id, service_id)` 제약 삭제, 데이터 초기화
- [x] `SubscriptionFilter` record에 `keywordTargets: Set<KeywordTarget>` 추가 (5번째 컴포넌트)
- [x] `KeywordTarget` enum 신설 — `SERVICE_NAME`, `PLACE_NAME`. `serverDefaults()`는 기존 동작 보존 + 하위호환 fallback 역할
- [x] `ServiceChangePersistenceAdapter` — `keywordTargets`를 통해 매칭 대상 컬럼을 동적으로 선택. `serverDefaults()` 직접 사용 제거
- [x] `SubscriptionFilter.isEmpty()` — `keywordTargets`를 조건 판정에서 의도적 제외 (대상만으로는 빈 구독)
- [x] `FilterDto` — `keywordTargets: Set<String>` 와이어 타입, `toDomain()`에서 `KeywordTarget.valueOf()` 변환 (미인식 값 → 400)
- [x] `NotificationPersistenceMapper` — `keywordTargets` 키 부재(구 JSONB) → `serverDefaults()` fallback, 미인식 토큰 graceful skip
- [x] `NotificationSubscriptionService.normalizeKeywordTargets()` — 키워드 있는데 targets 비어있으면 `serverDefaults()` 주입
- [x] `NotificationSubscriptionJpaEntity` — `filter`·`channels` 필드에 `@JdbcTypeCode(SqlTypes.JSON)` 추가 (Hibernate 6 JSONB 바인딩 오류 수정)
- [x] `CreateSubscriptionRequest` — `@AssertTrue` 빈 구독 가드 추가 (DTO 레이어 1차 방어)

**알림 발송 트리거 전환**

- [x] `common.event.CollectionCompletedEvent` record 신설 — collection↔notification 직접 의존 없이 이벤트 공유
- [x] `CollectionScheduler` — `collectAll()` 완료 후 `finally` 블록에서 이벤트 발행 (예외 발생 시에도 발행)
- [x] `NotificationScheduler` — `@Scheduled(fixedDelayString)` 제거 → `@Async @EventListener(CollectionCompletedEvent)` 전환. `AtomicBoolean`으로 중복 실행 방지
- [x] `OnSeoulApiApplication` — `@EnableAsync` 추가

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

**처리 순서 보장 (이벤트 체인): 수집 → 임베딩 동기화 → 알림**

별도 큐 대신 Spring `ApplicationEvent` 체인으로 인과관계를 코드로 표현한다. in-memory 큐/ScheduledExecutor는 도입하지 않는다(불필요한 복잡도). 일 1회 수집이므로 폴링/재큐가 필요 없고, 실패는 best-effort로 다음 수집 run에서 자연 복구된다.

```
CollectionScheduler ──CollectionCompletedEvent(runStartedAt)──▶ EmbeddingSyncWorker(@Async, collection BC)
                                                                      │
                                                          ──EmbeddingSyncCompletedEvent──▶ NotificationScheduler(@Async)
```

- `CollectionCompletedEvent`에 `runStartedAt`(Instant)을 실어 "이번 run" 변경분을 식별한다. `collectAll()`은 소스별 collection_id를 만들고 deletion sweep은 소스 횡단이라 단일 collection_id가 없으므로, `service_change_log.changed_at >= runStartedAt` timestamp 기반이 collection_id 기반보다 견고하다.
- 변경 service_id를 아는 것은 collection BC 책임이므로 `EmbeddingSyncWorker`는 collection 모듈에 둔다.

---

- [x] 변경 분류 소스 보강 — `UpsertService`가 NEW(신규 service_id), DELETED(deletion sweep)도 `service_change_log`에 기록(기존 UPDATED 유지). DDL 변경 없음(field_name/old_value/new_value는 nullable, DELETED collection_id는 이번 run 첫 collection_id 재사용).
- [x] `LoadChangedServiceIdsPort.loadSince(since)` — `changed_at >= since`인 distinct service_id를 upsert(NEW∪UPDATED)/delete(DELETED)로 분류 (`CollectionPersistenceAdapter` 구현).
- [x] `EmbeddingSyncWorker` — `@Async @EventListener(CollectionCompletedEvent)`. 변경 조회 → 빈배열 가드 → 500건 초과 시 청크 분할 → `EmbeddingSyncPort.sync()` 호출. 실패해도 예외를 삼키지 않고 로그 남기며, finally에서 `EmbeddingSyncCompletedEvent` 발행(best-effort, 알림 흐름 비차단).
- [x] `EmbeddingSyncClient` — `on-seoul-agent` `POST /embeddings/services/sync` 호출 (WebClient, `ai.service.embedding-sync-timeout-seconds`, TemplateAgentClient 패턴). 둘 다 빈 배열이면 호출 생략(422 회피).
- [x] `EmbeddingSyncCompletedEvent`(common) 신설 + `NotificationScheduler` 재배선 — `CollectionCompletedEvent` 대신 `EmbeddingSyncCompletedEvent`를 listen해 알림이 임베딩 동기화 완료 후 실행됨을 보장(AtomicBoolean 중복 방지 유지).
- [ ] Micrometer 게이지 `embeddings.sync.lag.seconds` 등록 — `adr/0003` 감시 요건 참조 (선택, 미구현)

---

## Phase 8. 테스트

- [x] `NotificationSubscription` / `NotificationDispatch` 도메인 단위 테스트 — 상태 전이(FAILED→SUCCESS lastError 초기화), 5회 도달 시 DEAD, `MAX_ATTEMPTS==5`, `isPending`, filter 생성 경로(parsedFilter/JSON null/정규화)
- [x] `NotificationScheduler` — TX A / TX B 분리, `ON CONFLICT DO NOTHING` 멱등성(`NotificationTxHelperTest`), `last_notified_at` 푸시 성공 시에만 전진, DEAD 전환(`DispatchRetrySchedulerTest`)
- [x] `TemplateAgentClient` — AI 호출 성공 / non-2xx(4xx·5xx) / 타임아웃 / 빈 title·body → fallback (MockWebServer)
- [x] ArchUnit — 모든 BC 쌍으로 cross-BC `domain` 엔티티 참조 금지 일반화(`HexagonalArchTest`)
- [x] `./gradlew test` 전 구간 통과 — 6개 모듈 530 tests, 0 failures

---

## 참고

- 모든 결정 근거는 `on-seoul-api/docs/adr/` 에 있다. ADR을 벗어나는 구현이 필요하면 구현 전에 확인한다.
- BC 간 참조는 ID 전달만 허용 (`adr/README.md` 컨텍스트 간 참조 정책).
- 도메인 이벤트 및 메시지 브로커는 도입하지 않는다. 외부 시스템 통신은 REST API(WebClient) 직접 호출 (`adr/0002-domain-event-catalog.md`).
- 알림 스케줄러 파라미터(동시성, 타임아웃, MAX_ATTEMPTS, 백오프)는 `adr/0004` 파라미터 표 준수.
- 테스트 케이스는 최대한 유지하며, BC 이동 및 리팩토링으로 인한 변경 사항을 함께 반영한다. 신규 기능은 충분한 단위 테스트와 통합 테스트를 작성한다.