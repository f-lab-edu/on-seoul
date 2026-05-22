# ADR-0004: 알림 발송 흐름의 오케스트레이션 방식

**Status:** Accepted
**Date:** 2026-04-25
**Depends on:** [ADR-0001](./0001-context-communication.md), [ADR-0003](./0003-consistency-and-transaction.md)

## Context

알림 발송 흐름은 일직선이다: `구독 순회 → 필터 기반 ChangeLog 조회 → AI 배치 템플릿 생성 → 푸시 발송 → Dispatch 기록 → last_notified_at 갱신`. 단계 간 상태 전이가 없으므로 Saga/상태 머신 패턴은 과잉이다.

Phase 6 구현 후 Dispatch 모델 재검토 결과 세 가지 개선 방향이 도출되었다:

1. **`SubscriptionFilter` 도입**: 구독의 `filter` JSONB를 역직렬화하여 `service_change_log JOIN public_service_reservations` 쿼리의 WHERE 조건으로 직접 적용한다. 변경 이벤트를 모두 적재한 뒤 애플리케이션에서 필터링하는 방식보다 쿼리 단계에서 필터를 적용하는 것이 더 명확하다.

2. **per-change Dispatch 제거**: 기존 `UNIQUE(subscription_id, change_log_id)` 모델은 변경 이벤트별 FAILED/DEAD 재시도 루프를 상정했다. 그러나 **`last_notified_at` 자체가 재시도 메커니즘**이다 — 발송 실패 시 `last_notified_at`을 갱신하지 않으면 다음 배치에서 동일한 변경 목록이 자동 재조회된다. per-change 재시도 루프는 불필요하다.

3. **`notification_batch` 분리 — 실행 단위를 명시적 엔티티로 관리**: `tick_at`을 Dispatch의 복합 키로 사용하는 방식은 시각 정규화에 의존하고 운영 메타데이터(처리 건수, 소요 시간)를 추적할 수 없다. On Seoul은 이미 `collection_history`로 수집 배치를 추적하고 있다. 알림도 같은 패턴(`notification_batch`)을 따르면 도메인 전반에 일관된 구조가 만들어지고, **멱등성 키가 시각이 아닌 실행 ID(`batch_id`)로 명시**된다.

## Decision

스케줄러를 **배치 잡**으로 본다(프로세스 매니저 아님). 보상 트랜잭션 없음. 정합성은 두 메커니즘으로 보장한다:
- **`UNIQUE(batch_id, subscription_id)`** — 1차 방어선. 같은 배치 내 중복 발송을 차단한다.
- **`last_notified_at` 미갱신 정책** — 2차 방어선. 발송 실패 시 다음 배치에서 자동 재조회된다.

### 테이블 구조

```sql
-- 스케줄러 실행 단위
CREATE TABLE notification_batch (
    id            BIGSERIAL PRIMARY KEY,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        VARCHAR(20) NOT NULL DEFAULT 'RUNNING',  -- RUNNING / SUCCESS / FAILED
    sent_count    INT,
    failed_count  INT
);
-- scheduled_at: @Scheduled(fixedDelay)에서는 의도 시각 개념이 없어 제거. cron 전환 시 재추가.
-- total_subscriptions: 사전 카운트 쿼리 비용 대비 운영 가치 모호 → 제거.
-- created_at: started_at과 중복 → 제거.

-- batch_id × subscription_id 발송 시도
CREATE TABLE notification_dispatch (
    id               BIGSERIAL PRIMARY KEY,
    batch_id         BIGINT NOT NULL REFERENCES notification_batch(id),
    subscription_id  BIGINT NOT NULL,
    status           VARCHAR(20) NOT NULL,   -- PENDING / SUCCESS / FAILED
    generated_title  TEXT,
    generated_body   TEXT,
    template_source  VARCHAR(20),            -- AI / FALLBACK
    sent_at          TIMESTAMPTZ,
    last_error       TEXT,
    created_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (batch_id, subscription_id)       -- 멱등성 키
);
```

### 흐름 (단일 워커, 가상 스레드 풀)

```
[스케줄러 시작]
  Batch INSERT (started_at=now(), status=RUNNING) → batch_id 발급

for each NotificationSubscription (가상 스레드 풀, 동시 4건):
  TX A:
    filter  ← SubscriptionFilter.from(sub.filter)   -- JSONB 역직렬화
    changes ← service_change_log
               JOIN public_service_reservations ON service_id
               WHERE changed_at > sub.last_notified_at
               AND filter 조건 (카테고리·지역·상태)
    if changes is empty: continue
    Dispatch INSERT (batch_id, subscription_id, status=PENDING)
      ON CONFLICT (batch_id, subscription_id) DO NOTHING
  TX A commit

  (TX 밖):
    template ← AI 호출 (POST /notification/template, List<ChangeItem>, 10s timeout)
              OR fallback (NotificationTemplate.render — 정형 변수 치환)
    result   ← 푸시 발송 (dispatch.id를 idempotency key로)
  TX B:
    if result is success:
      Dispatch UPDATE status=SUCCESS, sent_at=now(),
                     generated_title=..., generated_body=..., template_source=AI|FALLBACK
      Subscription UPDATE last_notified_at = batch.started_at
        -- batch.started_at을 커서로 사용: 이 배치가 시작될 때까지의 변경은 모두 처리됨
    else:
      Dispatch UPDATE status=FAILED, last_error=...
      -- last_notified_at 갱신하지 않음 → 다음 배치에서 동일 변경 목록 자동 재조회
  TX B commit

[스케줄러 완료]
  Batch UPDATE (status=SUCCESS|FAILED, finished_at, sent_count, failed_count)
```

### 핵심 보장

- **`UNIQUE(batch_id, subscription_id)` — 1차 방어선**: 스케줄러가 중복 실행되거나 같은 배치 안에서 재진입해도 Dispatch INSERT는 `ON CONFLICT DO NOTHING`으로 차단된다. 시각 정규화에 의존하지 않으므로 스케줄 주기 변경 시에도 멱등성이 깨지지 않는다.
- **`last_notified_at` — 2차 방어선(재시도 커서)**: 발송 실패 또는 JVM 크래시로 `last_notified_at`이 전진하지 않으면, 다음 배치에서 동일한 변경 목록이 자동 재조회·재시도된다.
- **`batch.started_at`을 커서로 사용**: `started_at` 시점에 존재하는 모든 변경이 이 배치의 처리 대상이다. `started_at` 이후에 도착한 변경은 다음 배치가 처리한다.
- **배치 메타데이터 추적**: `notification_batch`에서 실행 시각·처리 건수·소요 시간을 한 번의 조회로 확인 가능. `collection_history`와 동일한 관찰 패턴.
- **idempotency key = dispatch.id** → push provider 단에서 짧은 시간 내 동일 dispatch 재전송 차단.

### 단계별 실패 매트릭스

| 단계 | 실패 시 동작 | 멱등성 보장 방식 | 보상 필요 |
|---|---|---|---|
| Batch INSERT | 예외 발생 시 배치 전체 중단 | 새 배치 실행 시 새 batch_id 발급 | 불필요 |
| 필터 기반 ChangeLog 조회 | 다음 배치에서 재실행 | read-only | 불필요 |
| Dispatch INSERT (TX A) | TX A 롤백, 푸시 미시도 | UNIQUE(batch_id, subscription_id) + ON CONFLICT DO NOTHING | 불필요 |
| AI 배치 템플릿 생성 호출 | fallback 사용; 흐름 계속 | fallback이 결정적 함수 | 불필요 |
| 푸시 발송 | TX B에서 status=FAILED; `last_notified_at` 미갱신 → 다음 배치 자동 재조회 | dispatch.id를 idempotency key로 provider에 전달 | 불필요 |
| Dispatch UPDATE / last_notified_at (TX B) | TX B 롤백; `last_notified_at` 미갱신 → 다음 배치에서 동일 변경 목록 재조회 → 새 batch_id로 Dispatch INSERT | last_notified_at 갱신 정책 | 불필요 |
| Batch UPDATE (완료 기록) | status=FAILED로 기록. 다음 배치는 신규 batch_id로 정상 실행 | — | 불필요 |

### 파라미터

| 항목 | 값 | 비고 |
|---|---|---|
| 스케줄러 주기 | 5분 | `@Scheduled(fixedDelay = 5m)` |
| 동시성 | **가상 스레드 풀, 동시 4건** | `Executors.newVirtualThreadPerTaskExecutor()` + `Semaphore(4)` |
| AI 호출 timeout | **10초** | `WebClient.responseTimeout(Duration.ofSeconds(10))` |
| AI 실패 판정 | HTTP non-2xx / 10초 타임아웃 / 응답 `title` 또는 `body`가 빈 문자열 | 위 중 하나라도 해당 시 fallback |
| MAX_ATTEMPTS | — | per-change DEAD 루프 제거. 재시도는 last_notified_at 미갱신으로 자동 처리 |
| 금칙어/PII 검증 | Phase 2 | LLM provider safety filter에 1차 의존 |

### 운영 metrics (MVP 포함)

Micrometer + Actuator `/metrics`:
- `notification.template.source{source=AI|FALLBACK}` — counter. AI fallback 비율 모니터링.
- `notification.dispatch.attempts{result=success|failed}` — counter. 시도/성공 비율.
- `notification.dispatch.failed.total` — counter. 발송 실패 누적량 (다음 tick 재시도 예정 신호).

별도 인프라 불필요. Phase 2에서 Prometheus + Grafana 연계.

## Consequences

**긍정**
- 멱등성 키가 시각이 아닌 실행 ID(`batch_id`)로 명시적. 스케줄 주기 변경·수동 트리거에도 멱등성 보장.
- `notification_batch`로 배치 실행 메타데이터(처리 건수, 소요 시간) 추적 — `collection_history`와 동일한 관찰 패턴으로 도메인 일관성.
- JVM 크래시·푸시 실패 모두 `last_notified_at` 미갱신으로 자연 복구. 보상 트랜잭션·별도 retry 워커 불필요.
- Dispatch 스키마가 경량화됨 — `change_log_id`, `attempt_count`, DEAD 상태 불필요.
- `SubscriptionFilter`로 쿼리 단계에서 불필요한 변경 이벤트를 사전 제거. 애플리케이션 필터링 루프 없음.
- AI 배치 호출 1회(List<ChangeItem>)로 구독 1건 처리 완료 — AI Service 호출 횟수 최소화.
- 수동 재실행이 새로운 `batch_id`로 명확히 분리됨. 특정 배치의 영향 범위를 `batch_id`로 즉시 조회 가능.

**부정**
- AI fallback이 빈번하면 메시지 품질 저하 — `template_source` 비율 monitoring이 운영 책임을 짊어짐.
- 영구 발송 실패(네트워크 단절 등)에 대한 자동 DEAD 전환이 없어 운영자가 `notification_dispatch` status=FAILED를 직접 모니터링해야 함.
- `notification_batch` INSERT가 실패하면 해당 배치 전체가 중단됨. 배치 INSERT 자체는 단순 PG write이므로 실패 확률이 낮지만, 모니터링 대상에 포함해야 함.
- 가상 스레드 4건 동시 처리가 PG 커넥션 풀과 경합할 수 있음 — 풀 사이즈 ≥ 8 권고.

## Alternatives Considered

**per-change Dispatch 모델 — UNIQUE(subscription_id, change_log_id)**
- Phase 6에서 초기 구현된 방식. 변경 이벤트마다 Dispatch row를 생성하고 FAILED/DEAD 상태로 per-change 재시도.
- `last_notified_at`이 이미 재시도 메커니즘이므로 per-change FAILED/DEAD 루프는 중복이다. 구독당 N건 변경 발생 시 N개의 Dispatch row가 생성되어 스키마와 쿼리가 복잡해짐.
- **기각.** per-batch 모델이 동일한 멱등성을 더 단순한 구조로 달성.

**per-tick Dispatch — UNIQUE(subscription_id, tick_at)**
- 시각을 멱등성 키로 사용하는 방식. 배치 실행 단위가 테이블에 명시되지 않아 운영 메타데이터 추적 불가.
- `tick_at`의 정밀도(밀리초)에 따라 같은 tick에 두 Dispatch가 삽입될 수 있고, 스케줄 주기 변경 시 기존 row와 충돌 가능성 존재.
- **기각.** `notification_batch` 분리 방식이 시각 정규화 없이 동일한 멱등성을 보장하고 운영 가시성도 제공.

**retry 워커가 PENDING Dispatch도 픽업**
- 정합성을 3가지(last_notified_at + UNIQUE + retry 워커 PENDING 픽업)가 맞물려야 보장. 역할 분리가 모호.
- **기각.** last_notified_at 미갱신 정책만으로 충분.

**푸시를 Dispatch INSERT와 같은 TX 안에서 호출**
- 외부 호출이 TX 길이를 늘리고 rollback 시 이미 발송된 푸시는 되돌릴 수 없음.
- **기각.**

**스케줄러를 프로세스 매니저로 모델링 (상태 머신)**
- 일직선 흐름에 상태 머신은 과잉 추상화. **기각.**

**순차 처리 (동시성 1)**
- 구독자 수 증가 시 단일 tick 처리량 부족 위험. 가상 스레드 4건이 더 안전한 출발점.
- **기각.**
