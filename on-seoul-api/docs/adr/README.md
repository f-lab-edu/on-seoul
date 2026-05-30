# Architecture Decision Records — on-seoul-api

라운드 2 통합 패턴 결정의 결과물입니다. 라운드 1(애그리거트/BC 경계)은 별도 문서로 다루지 않고 본 ADR 들의 전제로 고정합니다.

## 인덱스

| ID | 제목 | Status | 핵심 결정 |
|---|---|---|---|
| [0001](./0001-context-communication.md) | 컨텍스트 간 통신 방식 | Accepted | BC 간 동기 Port 직접 호출. ACL은 외부 시스템(서울 Open API, FastAPI)에만 적용 |
| [0002](./0002-domain-event-catalog.md) | 도메인 이벤트 카탈로그 | Accepted | MVP에서 도메인 이벤트 발행 0개. 메시지 브로커·Outbox 미도입 |
| [0003](./0003-consistency-and-transaction.md) | 일관성 경계와 트랜잭션 정책 | Accepted | PG 내부는 강한 일관성. PG↔Qdrant·외부 호출은 최종 일관성 + 5분 SLA |
| [0004](./0004-notification-orchestration.md) | 알림 발송 흐름 오케스트레이션 | Accepted | 배치 잡(상태 머신 아님). 푸시 성공 시 last_notified_at 갱신. UNIQUE 제약 단독 멱등 |

## 전제 (라운드 1 확정 사항)

### 바운디드 컨텍스트 (4개)

`user/`, `chat/`, `collection/`, `notification/` — 각각 헥사고날(`domain/`, `application/`, `port/`, `adapter/`) 내부 구조를 갖는 최상위 패키지.

### 애그리거트 (9개)

| 애그리거트 | 소속 BC | 비고 |
|---|---|---|
| User | user | OAuth credential 흡수 |
| ChatRoom | chat | 메타데이터만 |
| ChatMessage | chat | `trace` JSONB 컬럼으로 trace 흡수 |
| PublicServiceReservation | collection | |
| ServiceChangeLog | collection | 별도 애그리거트 |
| ServiceEmbeddings | collection | Qdrant 저장 |
| CollectionHistory | collection | |
| NotificationSubscription | notification | 매칭 규칙은 `filter` VO로 흡수 |
| NotificationDispatch | notification | `generated_title/body` 직접 보유, `template_source` 컬럼 포함 |

### 컨텍스트 간 참조 (ID로만)

- `chat.ChatRoom` → `user.User.id`
- `notification.Subscription` → `user.User.id`, `collection.PSR.service_id`
- `notification.Dispatch` → `notification.Subscription.id`, `collection.ServiceChangeLog.id`
- `collection.ServiceChangeLog` → `collection.PSR.service_id` (동일 BC)

### Agent (FastAPI) 처리

독립 BC 아님. 각 BC의 `adapter/out/`에서 호출:
- `chat.adapter.out.ChatAgentClient` — `POST /chat/stream`
- `notification.adapter.out.TemplateAgentClient` — `POST /notification/template`

## 변경 절차

- 신규 결정: 다음 번호로 `NNNN-slug.md` 추가, 본 README 표 갱신.
- 기존 결정 변경: 새 ADR을 추가하고 이전 ADR의 Status를 `Superseded by ADR-NNNN`으로 표기. 기존 본문은 보존.
