# ADR-0002: 도메인 이벤트 카탈로그

**Status:** Accepted
**Date:** 2026-04-25
**Depends on:** [ADR-0001](./0001-context-communication.md)

## Context

식별된 시나리오 6종을 후보로 두고, 진짜 BC 경계를 넘는 비동기 신호인지 검토했다. **알림이 pull(스케줄러) 방식**이라는 점이 결정의 축이다 — 수집 결과가 알림을 push로 트리거하지 않으므로 변경 감지 이벤트의 소비자가 없다.

## Decision

**MVP에서는 도메인 이벤트 발행 0개**. 모든 후보를 동기 직접 호출 또는 audit row INSERT로 흡수한다.

| 후보 이벤트 | 발행 BC | 잠재 구독 BC | MVP 처리 |
|---|---|---|---|
| ChatMessageSent | chat | — | `chat.application.ChatService.send()` 내부 처리, 이벤트 없음 |
| ChatAnswerCompleted | chat | — | `chat_messages.trace` JSONB에 기록, 이벤트 없음 |
| ServiceChanged | collection | (없음) | `service_change_log` INSERT 자체가 audit, 이벤트 없음 |
| SubscriptionCreated/Removed | notification | — | CRUD, 이벤트 없음 |
| NotificationDispatched | notification | — | `notification_dispatch` row가 audit, 이벤트 없음 |
| UserRegistered | user | notification | OAuth 성공 핸들러에서 `CreateDefaultSubscriptionsUseCase` 직접 호출 (동기, 동일 TX 밖) |

`UserRegistered`의 동기 호출 흐름은 [ADR-0001](./0001-context-communication.md)의 BC 간 호출 예시 참조.

### 인프라 결정

- **메시지 브로커 (Kafka/RabbitMQ):** **도입하지 않음.** BC 간 통신은 동기 inbound Port 직접 호출, 외부 시스템 통신은 REST API 호출로 처리한다.
- **Spring `ApplicationEventPublisher`:** **도입하지 않음.**
- **Outbox 패턴:** **도입하지 않음.** (발행할 이벤트가 없으므로 자명)

## Consequences

**긍정**
- 이벤트 도입 비용 0. 도메인 흐름이 코드를 따라 직선으로 읽힘.
- 멘탈 모델 단순: "다른 BC에 영향 주려면 inbound port 호출".

**부정**
- BC 간 동기 호출이 누적되면 호출 그래프가 dense해질 수 있음 — 통합 테스트로 의존 방향만 단방향 유지(상위→하위).

## Alternatives Considered

**위 후보 전체를 `ApplicationEventPublisher`로 발행 (동기 리스너)**
- 동기 트랜잭션 리스너로 동작해 이득 없이 흐름만 분산. 추적 비용만 증가.
- **기각.**

**`UserRegistered`만 발행 (도메인 이벤트 1개 시작점)**
- 단일 소비자(같은 트랜잭션, 동일 호출 사이트)에 이벤트를 끼우는 것은 추상화 비용 대비 이득 0.
- **기각.**
