# ADR-0004: 알림 발송 흐름의 오케스트레이션 방식

**Status:** Accepted
**Date:** 2026-04-25
**Depends on:** [ADR-0001](./0001-context-communication.md), [ADR-0003](./0003-consistency-and-transaction.md)

## Context

알림 발송 흐름은 일직선이다: `구독 순회 → ChangeLog 매칭 → AI 템플릿 생성 → 푸시 발송 → Dispatch 기록 → last_notified_at 갱신`. 단계 간 상태 전이가 없으므로 Saga/상태 머신 패턴은 과잉이다.

피드백에서 두 가지 결함이 지적되었다:
1. **JVM 크래시 시 PENDING Dispatch가 누락**되는 시나리오 — last_notified_at이 이미 갱신되어 다음 tick에서 재매칭되지 않는 문제.
2. **AI 호출 실패 판정 기준**과 **타임아웃 값**, **스케줄러 동시성**, **운영 metrics**가 명시되지 않음.

## Decision

스케줄러를 **배치 잡**으로 본다(프로세스 매니저 아님). 보상 트랜잭션 없음. 정합성은 **UNIQUE 제약과 last_notified_at 갱신 시점**의 두 메커니즘으로 보장한다 — 단, **last_notified_at은 푸시 성공 후에만 갱신**한다 (피드백 보완안 B 채택).

### 흐름 (단일 워커, 가상 스레드 풀)

```
[스케줄러 tick]
for each NotificationSubscription (가상 스레드 풀, 동시 4건):
  TX A:
    changes ← ServiceChangeLog WHERE changed_at > sub.last_notified_at AND filter 매칭
    if changes is empty: continue
    for each change in changes:
      Dispatch INSERT (status=PENDING, attempt_count=0)
        ON CONFLICT (subscription_id, change_log_id) DO NOTHING  -- 기존 row 보존
  TX A commit

  for each change in changes (TX 밖):
    dispatch ← Dispatch WHERE (subscription_id, change_log_id) AND status IN (PENDING, FAILED)
                          AND attempt_count < MAX_ATTEMPTS
    if dispatch is null: continue   -- 이미 SUCCESS 거나 DEAD
    template ← AI 호출 (POST /notification/template, 10s timeout)
              OR fallback (NotificationTemplate.render — 정형 변수 치환)
    result ← 푸시 발송 (dispatch.id를 idempotency key로)
    TX B:
      if result is success:
        Dispatch UPDATE status=SUCCESS, sent_at=now(),
                       generated_title=..., generated_body=...,
                       template_source=AI|FALLBACK
        Subscription UPDATE last_notified_at = MAX(last_notified_at, change.changed_at)
      else:
        Dispatch UPDATE status=FAILED, attempt_count=attempt_count+1, last_error=...
        -- last_notified_at은 갱신하지 않음
        if attempt_count + 1 >= MAX_ATTEMPTS: status=DEAD
    TX B commit
```

### 핵심 보장

- **last_notified_at은 푸시 성공 시에만 전진** → JVM 크래시로 푸시 결과가 누락되어도 다음 tick에서 같은 ChangeLog가 다시 매칭된다.
- **UNIQUE(subscription_id, change_log_id)** → 재매칭되어도 Dispatch INSERT는 `ON CONFLICT DO NOTHING`으로 차단; 기존 PENDING/FAILED row를 그대로 사용해 푸시만 재시도.
- **idempotency key = dispatch.id** → push provider 단에서 짧은 시간 내 동일 dispatch 재전송 차단(브라우저 웹푸시는 endpoint 단위로 처리).
- **별도 retry 워커 불필요** → 스케줄러 자신이 PENDING/FAILED를 함께 처리.

### 단계별 실패 매트릭스

| 단계 | 실패 시 동작 | 멱등성 보장 방식 | 보상 필요 |
|---|---|---|---|
| 매칭 (ChangeLog 조회) | 다음 tick에서 재실행 | read-only | 불필요 |
| Dispatch INSERT (TX A) | TX A 롤백, 푸시 미시도 | UNIQUE(subscription_id, change_log_id) + ON CONFLICT DO NOTHING | 불필요 |
| AI 템플릿 생성 호출 | fallback 사용; 흐름 계속 | fallback이 결정적 함수 | 불필요 |
| 푸시 발송 | TX B에서 status=FAILED, attempt_count++; 같은 또는 다음 tick에서 재시도 | dispatch.id를 idempotency key로 provider에 전달 | 불필요 |
| Dispatch UPDATE / last_notified_at (TX B) | TX B 롤백; 다음 tick에서 동일 ChangeLog 다시 매칭 → UNIQUE로 INSERT 차단 → 기존 row로 재시도 | UNIQUE + 푸시 성공 후 갱신 정책 | 불필요 |
| 최종 영구 실패 (attempt_count ≥ MAX_ATTEMPTS) | status=DEAD; 운영 metric으로 노출 | — | 보상 없음 (도메인 상태 되돌릴 것 없음) |

### 파라미터

| 항목 | 값 | 비고 |
|---|---|---|
| 스케줄러 주기 | 5분 | `@Scheduled(fixedDelay = 5m)` |
| 동시성 | **가상 스레드 풀, 동시 4건** | `Executors.newVirtualThreadPerTaskExecutor()` + `Semaphore(4)` |
| AI 호출 timeout | **10초** | `WebClient.responseTimeout(Duration.ofSeconds(10))` |
| AI 실패 판정 | HTTP non-2xx / 10초 타임아웃 / 응답 `title` 또는 `body`가 빈 문자열 | 위 중 하나라도 해당 시 fallback |
| MAX_ATTEMPTS | 5 | 도달 시 DEAD |
| 백오프 | 지수 (1m, 2m, 4m, 8m, 16m) | `attempt_count` 기반 다음 시도 시각 계산 |
| 금칙어/PII 검증 | Phase 2 | LLM provider safety filter에 1차 의존 |

### 운영 metrics (MVP 포함)

Micrometer + Actuator `/metrics`:
- `notification.template.source{source=AI|FALLBACK}` — counter. AI fallback 비율 모니터링.
- `notification.dispatch.dead.total` — counter. DEAD 누적량 (사용자가 알림을 못 받았다는 신호).
- `notification.dispatch.attempts{result=success|failed}` — counter. 시도/성공 비율.

별도 인프라 불필요. Phase 2에서 Prometheus + Grafana 연계.

## Consequences

**긍정**
- JVM 크래시·푸시 실패 모두 멱등 재실행으로 자연 복구. 보상 트랜잭션 없음.
- 정합성 보장이 두 메커니즘(UNIQUE + last_notified_at 갱신 시점)으로 단순화. 별도 retry 워커 불필요.
- 가상 스레드 4건 동시성으로 AI 호출 지연이 누적되더라도 단일 tick 처리량 확보. AI Service 부하는 4건 이내로 캡됨.

**부정**
- AI fallback이 빈번하면 메시지 품질 저하 — `template_source` 비율 monitoring이 운영 책임을 짊어짐.
- 푸시 provider가 idempotency key를 지원하지 않으면 짧은 시간 내 중복 push 가능성. 브라우저 웹푸시는 endpoint 단위 dedup이 일반적이지만, 채택 provider별 확인 필요.
- 가상 스레드 4건 동시 처리가 PG 커넥션 풀과 경합할 수 있음 — 풀 사이즈 ≥ 8 권고.

## Alternatives Considered

**보완안 A (피드백): retry 워커가 PENDING도 픽업**
- 정합성을 3가지(last_notified_at 범위 필터 + UNIQUE + retry 워커 PENDING 픽업)가 함께 맞물려야 보장. 역할 분리가 모호.
- **기각.** 채택안 B가 더 적은 변경으로 같은 보장 달성.

**푸시를 Dispatch INSERT와 같은 TX 안에서 호출**
- 외부 호출이 TX 길이를 늘리고 rollback 시 이미 발송된 푸시는 되돌릴 수 없음.
- **기각.**

**스케줄러를 프로세스 매니저로 모델링 (상태 머신)**
- 일직선 흐름에 상태 머신은 과잉 추상화. **기각.**

**순차 처리 (동시성 1)**
- 구독자 수 증가 시 단일 tick 처리량 부족 위험. 가상 스레드 4건이 더 안전한 출발점.
- **기각.**
