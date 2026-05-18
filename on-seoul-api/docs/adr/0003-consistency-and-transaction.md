# ADR-0003: 일관성 경계와 트랜잭션 정책

**Status:** Accepted
**Date:** 2026-04-25

## Context

저장소는 두 종류다: 단일 PostgreSQL(정형 데이터 + pgvector 임베딩을 동일 인스턴스에서 관리), 외부 푸시 시스템. PG 내부는 단일 TX로 강한 일관성이 가능하지만, 임베딩 갱신은 벡터 생성을 위한 외부 AI 서비스 호출이 선행되므로 TX 외부에서 비동기로 처리되며 최종 일관성이 필수다.

피드백에서 짚힌 문제: **ChangeLog 작성과 Embeddings 갱신 사이의 부정합 윈도우**가 정량적으로 명세되지 않음. 알림을 받은 사용자가 곧바로 채팅으로 그 서비스를 질의하면 Vector 라우팅이 구버전 임베딩을 반환할 수 있다.

## Decision

PG 내부 작업은 **강한 일관성, 1 TX**. 임베딩 갱신(pgvector) 및 외부 호출은 **최종 일관성**으로 처리하되, **부정합 허용 윈도우 = 5분 SLA**를 명시하고 metrics로 감시한다.

### 작업별 정책

| 작업 | 일관성 수준 | 트랜잭션 경계 | 실패 시 동작 |
|---|---|---|---|
| ChatRoom 생성 + 첫 메시지 저장 | 강한 일관성 | 1 TX | 전체 롤백; 클라이언트가 재시도 |
| 채팅방 삭제 + 메시지 정리 | 강한 일관성 | 1 TX (cascade soft-delete) | 대량이면 `deleted_at` 마킹만 1 TX → 별도 배치로 물리 정리 |
| ChangeLog 작성 + Embeddings 재생성 | **최종 일관성 (≤5분)** | PG 1 TX (ChangeLog) + AI 서비스 호출 후 pgvector upsert (별도 TX) | ChangeLog 작성은 강한 일관성. Embeddings는 수집 TX 커밋 직후 별도 워커가 AI 서비스를 비동기 호출해 벡터를 생성, `service_embeddings` 테이블(pgvector)에 upsert. 실패 시 `content_hash` 비교 기반 다음 수집 배치에서 자동 복구 |
| 스케줄러 1회 실행 (전체 순회) | 최종 일관성 | **구독자 1건 = 1 TX** | 1건 실패는 로그 + 다음 구독자 계속. 잡 전체 실패도 다음 tick에서 멱등 재실행 ([ADR-0004](./0004-notification-orchestration.md)) |
| Dispatch 발송 실패 → 재시도 | 최종 일관성 | Dispatch UPDATE 1 TX | `attempt_count++`, 백오프 후 같은 스케줄러 tick이 픽업 ([ADR-0004](./0004-notification-orchestration.md)) |

### Embeddings 갱신 SLA 운영 정의

- **목표 시간차:** ChangeLog 커밋 후 5분 이내 pgvector 반영.
- **갱신 트리거:** 수집 트랜잭션 커밋 직후 `EmbeddingSyncQueue`에 `service_id`를 enqueue. 별도 워커(Spring `@Async` 또는 `ScheduledExecutor` 기반)가 큐를 소비해 **AI 서비스 REST API**를 직접 호출해 임베딩 벡터를 생성한 후 `service_embeddings` 테이블(pgvector)에 upsert. 메시지 브로커 없음.
- **부정합 시 사용자 경험:** 알림을 받은 시점 ~ 임베딩 갱신 완료 전 윈도우(최대 5분)에 채팅으로 같은 서비스를 질의하면, Vector 라우팅 결과가 구버전 임베딩을 반환할 수 있다. 이 경우 SQL 라우팅은 영향 없음(정형 데이터는 PG에서 최신 상태). MVP 허용 범위.
- **감시:** Micrometer 게이지 `embeddings.sync.lag.seconds` — 가장 오래 미반영된 `ChangeLog.changed_at` 기준 경과 시간. 5분 초과가 1시간 이상 지속되면 alert.

### 강제 순서 요구가 발생하는 경우

`알림 발송과 채팅 검색 결과 정합성`을 데모 핵심으로 격상하게 되면, 스케줄러가 `embeddings.updated_at >= change_log.changed_at`을 조건으로 매칭하는 옵션을 Phase 2에서 추가한다. MVP는 그렇게 하지 않는다.

## Consequences

**긍정**
- 락 범위 좁음. 부분 실패가 잡 전체를 무너뜨리지 않음.
- 부정합 정량값(5분)과 metric이 명시되어 운영 판단 가능.

**부정**
- 5분 윈도우 동안 Vector 답변의 신선도가 떨어짐. 데모 시점 운 나쁘면 구버전 답변.
- Embeddings 워커가 장기 실패하면 `embeddings.sync.lag.seconds`가 누적 — 알림 기능과는 독립이지만 채팅 품질에 영향. metric alert 필수. 단, pgvector가 동일 PG 인스턴스에 있어 스토리지 계층 장애는 없으며, AI 서비스 호출 실패만이 lag 원인이 된다.

## Alternatives Considered

**스케줄러 전체 순회를 1 TX**
- 락 보유 시간이 분 단위로 늘어나고 1건 실패가 전체 롤백. **기각.**

**Embeddings 갱신 완료를 알림 스케줄러가 강제로 기다림 (옵션 B)**
- Embeddings 워커 실패가 알림 발송 지연으로 전파됨. MVP에 과한 결합.
- **기각** (Phase 2로 연기).
