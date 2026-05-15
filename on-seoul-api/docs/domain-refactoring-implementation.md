# API 서비스 도메인 리팩터링 구현 목록

ADR 기반 수직 BC 분리 및 알림 기능 신규 구현.
단일 브랜치 `refactor/domain-bc-split` 에서 진행한다.

아키텍처 결정의 전제는 `on-seoul-api/docs/adr/` 를 참조한다.

---

## Phase 1. 패키지 수직 분리

> 현재 구조(수평 레이어 최상위)를 BC 최상위 구조로 재편한다.
> 상세 결정은 `adr/README.md` (BC 4개 + 애그리거트 9개 전제) 참조.

- [ ] 목표 패키지 레이아웃 확정
  ```
  dev.jazzybyte.onseoul.
    user/        (domain/, application/, port/, adapter/)
    chat/        (domain/, application/, port/, adapter/)
    collection/  (domain/, application/, port/, adapter/)
    notification/(domain/, application/, port/, adapter/)
  ```
- [ ] `user` BC 이동 — `User`, `SocialLoginService`, `OAuth2LoginSuccessHandler`, 관련 Port
- [ ] `chat` BC 이동 — `ChatRoom`, `ChatMessage`, `ChatStreamService`, `SendQueryService`, 관련 Port
- [ ] `collection` BC 이동 — `PublicServiceReservation`, `ServiceChangeLog`, `CollectionHistory`, `ApiSourceCatalog`, 수집 서비스 전체, 관련 Port
- [ ] `common/`, `bootstrap/` 은 BC 불포함 — 현 위치 유지
- [ ] ArchUnit 헥사고날 의존성 규칙 갱신 (`HexagonalArchTest`)
- [ ] BC 간 참조는 ID 전달로만 — 교차 BC 엔티티 직접 참조 제거 (`adr/README.md` 컨텍스트 간 참조 정책 참조)

검증: `./gradlew test` 전 구간 통과

---

## Phase 2. notification BC — DB 스키마

> 알림 구독 및 발송 테이블 신규 생성.
> 상세 결정은 `adr/0004-notification-orchestration.md` 참조.

- [ ] `notification_subscriptions` 테이블 — `user_id`, `service_id`, `filter`(JSONB), `last_notified_at`
- [ ] `notification_dispatches` 테이블 — `subscription_id`, `change_log_id`, `status`(`PENDING`/`SUCCESS`/`FAILED`/`DEAD`), `attempt_count`, `sent_at`, `generated_title`, `generated_body`, `template_source`, `last_error`
- [ ] UNIQUE 제약 — `(subscription_id, change_log_id)` (`adr/0004` 멱등성 보장 참조)
- [ ] `notification_dispatch_templates` 테이블 — fallback 정형 템플릿 (선택)
- [ ] 마이그레이션 스크립트 작성 (`schema/migration-scripts/`)
- [ ] H2 테스트 스키마(`jpa-test-schema.sql`) 동기화

---

## Phase 3. notification BC — 도메인 · 포트 구현

> 상세 결정은 `adr/0001-context-communication.md`, `adr/0004-notification-orchestration.md` 참조.

- [ ] `NotificationSubscription` 애그리거트 — `filter` VO 포함
- [ ] `NotificationDispatch` 애그리거트 — `generated_title/body`, `template_source` 컬럼 직접 보유
- [ ] Inbound Port — `CreateDefaultSubscriptionsUseCase` (ADR-0001 BC 간 동기 호출 인터페이스)
- [ ] Outbound Port — `LoadSubscriptionPort`, `SaveSubscriptionPort`, `SaveDispatchPort`, `PushNotificationPort` (SMS/이메일 발송), `TemplateAgentPort`
- [ ] JPA 엔티티 및 Repository

---

## Phase 4. user BC — 기본 구독 생성 연결

> OAuth 로그인 성공 시 기본 구독을 동기 직접 호출로 생성한다.
> 상세 결정은 `adr/0001-context-communication.md`, `adr/0002-domain-event-catalog.md` 참조.

- [ ] `OAuth2LoginSuccessHandler` → `CreateDefaultSubscriptionsUseCase.create(userId)` 직접 호출
- [ ] 호출은 JWT 발급 TX 밖(별도 TX 또는 TX 없음) — `adr/0003-consistency-and-transaction.md` 참조
- [ ] `CreateDefaultSubscriptionsUseCase` 구현체 — 5개 데이터셋에 대한 기본 구독 INSERT

---

## Phase 5. notification BC — 템플릿 어댑터 구현

> AI 서비스 `POST /notification/template` 호출 어댑터.
> 상세 결정은 `adr/0001-context-communication.md` (ACL 적용 대상) 참조.

- [ ] `TemplateAgentClient` (WebClient 기반) — `adapter/out/agent/`
- [ ] `TemplateAgentDtoMapper` — ACL (FastAPI DTO ↔ 도메인)
- [ ] AI 호출 실패 판정 및 fallback 처리 — `adr/0004` 파라미터 참조
- [ ] 발송 채널 어댑터 — `PushNotificationPort` 구현 (SMS/이메일)

---

## Phase 6. notification BC — 알림 스케줄러 구현

> 배치 잡 방식. 상태 머신 아님.
> 상세 결정은 `adr/0004-notification-orchestration.md` 전체, `adr/0003-consistency-and-transaction.md` 참조.

- [ ] `NotificationScheduler` — `@Scheduled(fixedDelay)` 기반
- [ ] 가상 스레드 풀 + `Semaphore` 동시성 제어 — `adr/0004` 파라미터 참조
- [ ] TX A — `ServiceChangeLog` 매칭 + `NotificationDispatch` INSERT (`ON CONFLICT DO NOTHING`)
- [ ] TX B — 푸시 성공 시 `status=SUCCESS` + `last_notified_at` 갱신 / 실패 시 `status=FAILED`, `attempt_count++`
- [ ] `DEAD` 처리 — `attempt_count >= MAX_ATTEMPTS` 도달 시
- [ ] Fallback 템플릿 (`NotificationTemplate.render`) — AI 호출 실패 시 사용
- [ ] Micrometer 운영 metrics 3종 등록 — `adr/0004` 운영 metrics 참조

---

## Phase 7. Embeddings 갱신 워커

> ChangeLog 커밋 후 Qdrant 재생성을 비동기로 처리한다.
> 상세 결정은 `adr/0003-consistency-and-transaction.md` (≤5분 SLA) 참조.

- [ ] `EmbeddingSyncQueue` — in-memory 큐 또는 `ScheduledExecutor`
- [ ] 수집 TX 커밋 직후 `service_id` enqueue
- [ ] 워커 — Qdrant upsert 호출 (`on-seoul-agent` FastAPI 엔드포인트 또는 직접 Qdrant 클라이언트)
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
- 도메인 이벤트 및 메시지 브로커는 MVP 범위 밖 (`adr/0002-domain-event-catalog.md`).
- 알림 스케줄러 파라미터(동시성, 타임아웃, MAX_ATTEMPTS, 백오프)는 `adr/0004` 파라미터 표 준수.
- 테스트 케이스는 최대한 유지하며, BC 이동 및 리팩토링으로 인한 변경 사항을 함께 반영한다. 신규 기능은 충분한 단위 테스트와 통합 테스트를 작성한다.