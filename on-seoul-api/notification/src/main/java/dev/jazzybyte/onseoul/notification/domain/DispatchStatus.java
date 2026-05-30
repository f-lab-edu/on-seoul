package dev.jazzybyte.onseoul.notification.domain;

/**
 * ADR-0004 per-batch 모델.
 * DEAD는 outbox retry 스케줄러가 attempt_count >= 5에서 전환하는 영구 중단 상태.
 */
public enum DispatchStatus {
    PENDING, SUCCESS, FAILED,
    /** 재시도 한도 초과로 영구 중단. */
    DEAD
}
